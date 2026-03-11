#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
voco-dict.py

フォルダ内の .srt / .vtt / .txt を走査し、
VOICEVOXで読み間違えが起きやすい「候補語」を抽出して
{ "単語": "読み(かな/推定/空)" } のJSONを生成する。

モード:
  --mode voicevox : VOICEVOX Engine に問い合わせて読みを取得
  --mode blank   : 値は空文字（エンジン不要）
  --mode guess   : 簡易推定（エンジン不要）

使い方例:
  python voco-dict.py ./subs -o reading_map.json --mode blank
  python voco-dict.py ./subs -o reading_map.json --mode guess
  python voco-dict.py ./subs -o reading_map.json --mode voicevox --voicevox http://127.0.0.1:50021 --speaker 3
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, List, Dict, Optional

import requests


# -------------------------
# 字幕テキスト抽出
# -------------------------

TS_LINE_SRT = re.compile(r"^\s*\d{1,2}:\d{2}:\d{2}[,\.]\d{1,3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[,\.]\d{1,3}")
TS_LINE_VTT = re.compile(r"^\s*\d{1,2}:\d{2}(?::\d{2})?\.\d{1,3}\s*-->\s*\d{1,2}:\d{2}(?::\d{2})?\.\d{1,3}")
INDEX_LINE = re.compile(r"^\s*\d+\s*$")
VTT_HEADER = re.compile(r"^\s*WEBVTT\b")
HTML_TAG = re.compile(r"<[^>]+>")

def normalize_text_line(line: str) -> str:
    s = HTML_TAG.sub("", line)
    s = s.replace("&nbsp;", " ")
    s = s.replace("\u3000", " ")
    return s.strip()

def iter_text_lines_from_file(path: str, encoding_candidates: List[str]) -> Iterable[str]:
    data: Optional[str] = None
    for enc in encoding_candidates:
        try:
            with open(path, "r", encoding=enc, errors="strict") as f:
                data = f.read()
            break
        except Exception:
            continue
    if data is None:
        with open(path, "r", encoding=encoding_candidates[0], errors="replace") as f:
            data = f.read()

    for raw in data.splitlines():
        line = normalize_text_line(raw)
        if not line:
            continue
        if VTT_HEADER.match(line):
            continue
        if TS_LINE_SRT.match(line) or TS_LINE_VTT.match(line):
            continue
        if "-->" in line and (":" in line or "." in line):
            if TS_LINE_SRT.match(line) or TS_LINE_VTT.match(line):
                continue
        if INDEX_LINE.match(line):
            continue
        yield line


# -------------------------
# 候補語抽出（ヒューリスティック）
# -------------------------

@dataclass(frozen=True)
class ExtractConfig:
    min_token_len: int = 2
    max_token_len: int = 40

TOKEN_RE = re.compile(
    r"""
    (?:[A-Za-z]{2,}\d+[A-Za-z0-9\-_/\.]*)
  | (?:\d+[A-Za-z]{2,}[A-Za-z0-9\-_/\.]*)
  | (?:[A-Z]{2,}(?:[-_/\.][A-Z0-9]{1,})*)
  | (?:[A-Za-z]+(?:[-_][A-Za-z0-9]+)+)
  | (?:[A-Za-z][A-Za-z0-9]{2,})
  | (?:[一-龥]{3,})
  | (?:[0-9]+(?:\.[0-9]+){1,})
  | (?:[A-Za-z0-9]+/[A-Za-z0-9]+)
    """,
    re.VERBOSE,
)

STOP_EXACT = {"WEBVTT", "NOTE", "STYLE"}
STOP_RE = re.compile(r"^(?:\d+|[A-Za-z]{1})$")

def should_keep_token(tok: str, cfg: ExtractConfig) -> bool:
    if tok in STOP_EXACT:
        return False
    if STOP_RE.match(tok):
        return False
    if len(tok) < cfg.min_token_len or len(tok) > cfg.max_token_len:
        return False
    if re.match(r"^\d{1,2}:\d{2}", tok):
        return False
    return True

def extract_tokens_from_lines(lines: Iterable[str], cfg: ExtractConfig) -> List[str]:
    out: List[str] = []
    for line in lines:
        for m in TOKEN_RE.finditer(line):
            tok = m.group(0).strip()
            tok = tok.strip("[](){}<>\"'“”‘’、。,:;!?　 ")
            if not tok:
                continue
            if should_keep_token(tok, cfg):
                out.append(tok)
    return out


# -------------------------
# VOICEVOX かな取得
# -------------------------

def voicevox_kana(text: str, voicevox_url: str, speaker: int, timeout: float = 8.0) -> Optional[str]:
    try:
        r = requests.post(
            f"{voicevox_url.rstrip('/')}/audio_query",
            params={"text": text, "speaker": speaker},
            timeout=timeout,
        )
        r.raise_for_status()
        q = r.json()
        if isinstance(q, dict):
            kana = q.get("kana")
            if isinstance(kana, str) and kana.strip():
                return kana.strip()

            aps = q.get("accent_phrases")
            if isinstance(aps, list):
                parts: List[str] = []
                for ap in aps:
                    moras = ap.get("moras") if isinstance(ap, dict) else None
                    if not isinstance(moras, list):
                        continue
                    for mora in moras:
                        if isinstance(mora, dict):
                            t = mora.get("text")
                            if isinstance(t, str) and t:
                                parts.append(t)
                if parts:
                    return "".join(parts)
        return None
    except Exception:
        return None

def voicevox_available(voicevox_url: str, timeout: float = 2.5) -> bool:
    """
    Engineがいるか軽く確認。/speakers が取れればOK扱い。
    """
    try:
        r = requests.get(f"{voicevox_url.rstrip('/')}/speakers", timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


# -------------------------
# エンジン無し簡易推定
# -------------------------

LETTER_KANA = {
    "A": "エー", "B": "ビー", "C": "シー", "D": "ディー", "E": "イー", "F": "エフ",
    "G": "ジー", "H": "エイチ", "I": "アイ", "J": "ジェー", "K": "ケー", "L": "エル",
    "M": "エム", "N": "エヌ", "O": "オー", "P": "ピー", "Q": "キュー", "R": "アール",
    "S": "エス", "T": "ティー", "U": "ユー", "V": "ブイ", "W": "ダブリュー", "X": "エックス",
    "Y": "ワイ", "Z": "ズィー",
}

DIGIT_KANA = {
    "0": "ゼロ", "1": "イチ", "2": "ニ", "3": "サン", "4": "ヨン",
    "5": "ゴ", "6": "ロク", "7": "ナナ", "8": "ハチ", "9": "キュウ",
}

def guess_reading(tok: str) -> str:
    """
    かなり雑な推定。
    - 全大文字の略語: 1文字ずつ読み
    - 英数字混在: 英字は1文字読み、数字はゼロ/イチ…で読み
    - それ以外: 空（人手で埋める前提）
    """
    s = tok.strip()
    if not s:
        return ""

    # A/B みたいなもの
    if re.fullmatch(r"[A-Za-z0-9]+/[A-Za-z0-9]+", s):
        parts = s.split("/")
        return "スラッシュ".join(guess_reading(p) or p for p in parts)

    # 1.2.3 みたいなもの
    if re.fullmatch(r"\d+(?:\.\d+)+", s):
        out = []
        for ch in s:
            if ch == ".":
                out.append("テン")
            else:
                out.append(DIGIT_KANA.get(ch, ch))
        return "".join(out)

    # 全大文字略語（LLM, RTX, GPU 等）
    if re.fullmatch(r"[A-Z]{2,}", s):
        return "".join(LETTER_KANA.get(ch, ch) for ch in s)

    # 英数字混在（RTX4090, USB-C, v2 等）
    if re.search(r"[A-Za-z]", s) and re.search(r"\d", s):
        out = []
        for ch in s:
            if ch.isalpha():
                out.append(LETTER_KANA.get(ch.upper(), ch))
            elif ch.isdigit():
                out.append(DIGIT_KANA.get(ch, ch))
            elif ch in "-_":
                out.append("ハイフン" if ch == "-" else "アンダーバー")
            elif ch == ".":
                out.append("テン")
            elif ch == "/":
                out.append("スラッシュ")
            else:
                # その他記号は無視 or そのまま
                pass
        return "".join(out)

    # kebab/snake など（OpenAI_API 等）はとりあえず分割して英字部分だけ読む
    if re.search(r"[-_]", s) and re.search(r"[A-Za-z]", s):
        chunks = re.split(r"[-_]+", s)
        reads = [guess_reading(c) or c for c in chunks if c]
        joiner = "ハイフン" if "-" in s else "アンダーバー"
        return joiner.join(reads)

    return ""


# -------------------------
# メイン
# -------------------------

def collect_files(root: str) -> List[str]:
    exts = {".srt", ".vtt", ".txt"}
    paths: List[str] = []
    for dirpath, _, filenames in os.walk(root):
        for fn in filenames:
            if os.path.splitext(fn)[1].lower() in exts:
                paths.append(os.path.join(dirpath, fn))
    return sorted(paths)

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", help="字幕ファイル(.srt/.vtt/.txt)が入ったフォルダ")
    ap.add_argument("-o", "--out", default="reading_map.json", help="出力JSONファイル")

    ap.add_argument("--mode", choices=["voicevox", "blank", "guess"], default="blank",
                    help="voicevox=エンジン問い合わせ / blank=空文字 / guess=簡易推定")
    ap.add_argument("--voicevox", default="http://127.0.0.1:50021", help="VOICEVOX Engine URL")
    ap.add_argument("--speaker", type=int, default=3, help="speaker ID")
    ap.add_argument("--fallback", choices=["blank", "guess", "none"], default="blank",
                    help="mode=voicevox でエンジン不在時の動作（既定: blank）")

    ap.add_argument("--min-count", type=int, default=1, help="この出現回数以上のみ採用")
    ap.add_argument("--top", type=int, default=0, help="0なら全件。>0なら頻度上位N件のみ")
    ap.add_argument("--enc", default="utf-8,utf-8-sig,cp932,shift_jis", help="読み込みエンコーディング候補(カンマ区切り)")
    args = ap.parse_args()

    encs = [e.strip() for e in args.enc.split(",") if e.strip()]
    cfg = ExtractConfig()

    files = collect_files(args.folder)
    if not files:
        raise SystemExit(f"対象ファイルが見つかりません: {args.folder}")

    counter: Counter[str] = Counter()
    for p in files:
        lines = iter_text_lines_from_file(p, encs)
        toks = extract_tokens_from_lines(lines, cfg)
        counter.update(toks)

    items = [(tok, cnt) for tok, cnt in counter.items() if cnt >= args.min_count]
    items.sort(key=lambda x: (-x[1], x[0]))
    if args.top and args.top > 0:
        items = items[: args.top]

    # モード決定（voicevox指定でもエンジンが無ければfallbackへ）
    mode = args.mode
    if mode == "voicevox":
        if not voicevox_available(args.voicevox):
            if args.fallback == "none":
                raise SystemExit(f"VOICEVOX Engine に接続できません: {args.voicevox}")
            mode = args.fallback  # blank or guess

    out_map: Dict[str, str] = {}
    for tok, _cnt in items:
        if mode == "blank":
            out_map[tok] = ""
        elif mode == "guess":
            out_map[tok] = guess_reading(tok)
        else:  # voicevox
            kana = voicevox_kana(tok, args.voicevox, args.speaker)
            out_map[tok] = kana if kana is not None else ""

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out_map, f, ensure_ascii=False, indent=4)

    print(f"OK: {len(out_map)}件を書き出しました -> {args.out} (mode={mode})")

if __name__ == "__main__":
    main()

