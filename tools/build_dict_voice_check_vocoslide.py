#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_dict_voice_check.py

vocoslide のカスタム辞書 JSON（{"単語": "読み"}）を読み込み、
辞書なし / カスタム辞書適用 / 読み方調整パターンを聴き比べできる
静的 HTML ページと VOICEVOX 音声 WAV を生成します。

前提:
  - VOICEVOX ENGINE が http://127.0.0.1:50021 で起動していること
  - Python 標準ライブラリのみで動作

基本例:
  python tools/build_dict_voice_check.py --input dict/custom_dict.json --out dict_check --speaker 3 --limit 100

読み方調整パターンは既定で自動生成され、出力先に reading_patterns.auto.json として保存されます。

手動パターンを追加する例:
  python tools/build_dict_voice_check.py --input dict/custom_dict.json --out dict_check --speaker 3 --patterns dict/reading_patterns.json

reading_patterns.json 例:
{
  "重複": ["ちょうふく", "じゅうふく", "ちょーふく"],
  "AI": [
    {"label": "カタカナ", "reading": "エーアイ"},
    {"label": "ひらがな", "reading": "えーあい"},
    {"label": "区切る", "text": "エー、アイ。"}
  ]
}
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


@dataclass
class AudioVariant:
    key: str
    label: str
    text: str
    audio_file: str = ""


@dataclass
class DictEntry:
    id: str
    surface: str
    reading: str
    source_id: str
    variants: List[AudioVariant] = field(default_factory=list)


class VoicevoxError(RuntimeError):
    pass


def sanitize_filename(text: str, max_len: int = 60) -> str:
    s = re.sub(r"[\\/:*?\"<>|\s]+", "_", text.strip())
    s = re.sub(r"_+", "_", s).strip("_")
    if not s:
        s = "word"
    return s[:max_len]


def make_stable_id(surface: str, reading: str, index: int) -> str:
    base = f"{index}\t{surface}\t{reading}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]


def load_vocoslide_dict(path: Path, limit: Optional[int]) -> List[DictEntry]:
    raw = json.loads(path.read_text(encoding="utf-8"))

    if not isinstance(raw, dict):
        raise ValueError(
            "辞書JSONは vocoslide 形式のオブジェクトを指定してください。例: {\"AI\": \"エーアイ\"}"
        )

    # vocoslide 形式: {"AI": "エーアイ", "MVP": "エムブイピー"}
    # 値が dict の VOICEVOX ユーザー辞書形式は、このツールでは対象外。
    if raw and not all(isinstance(v, str) for v in raw.values()):
        raise ValueError(
            "辞書JSONは {\"単語\": \"読み\"} 形式にしてください。"
            "VOICEVOXユーザー辞書形式（surface/pronunciation等）は対象外です。"
        )

    entries: List[DictEntry] = []
    for i, (surface, reading) in enumerate(raw.items(), start=1):
        surface = str(surface).strip()
        reading = str(reading).strip()
        if not surface:
            continue
        sid = make_stable_id(surface, reading, i)
        entries.append(DictEntry(id=sid, surface=surface, reading=reading, source_id=str(i)))
        if limit and len(entries) >= limit:
            break
    return entries


def apply_template(template: str, *, surface: str, reading: str, no: int, label: str = "") -> str:
    return template.format(
        surface=surface,
        reading=reading,
        no=no,
        label=label,
    )


def load_patterns(path: Optional[Path]) -> Dict[str, List[Dict[str, str]]]:
    """読み方調整パターンJSONを読む。

    対応形式:
      {"重複": ["ちょうふく", "じゅうふく"]}
      {"重複": [{"label": "候補1", "reading": "ちょうふく"}, {"label": "直書き", "text": "ちょーふく。"}]}
    """
    if not path:
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("--patterns は {\"単語\": [...]} 形式のJSONにしてください。")

    result: Dict[str, List[Dict[str, str]]] = {}
    for surface, items in raw.items():
        if isinstance(items, str):
            items = [items]
        if not isinstance(items, list):
            raise ValueError(f"--patterns の {surface!r} は文字列または配列にしてください。")

        variants: List[Dict[str, str]] = []
        for idx, item in enumerate(items, start=1):
            if isinstance(item, str):
                variants.append({
                    "label": f"読み方調整 {idx}",
                    "reading": item,
                    "text": "",
                })
            elif isinstance(item, dict):
                label = str(item.get("label") or f"読み方調整 {idx}")
                reading = str(item.get("reading") or item.get("yomi") or item.get("kana") or "")
                text = str(item.get("text") or "")
                if not reading and not text:
                    raise ValueError(f"--patterns の {surface!r} の {idx} 番目に reading または text がありません。")
                variants.append({"label": label, "reading": reading, "text": text})
            else:
                raise ValueError(f"--patterns の {surface!r} の {idx} 番目は文字列またはオブジェクトにしてください。")
        result[str(surface)] = variants
    return result



def kata_to_hira(text: str) -> str:
    out = []
    for ch in text:
        code = ord(ch)
        if 0x30A1 <= code <= 0x30F6:
            out.append(chr(code - 0x60))
        else:
            out.append(ch)
    return "".join(out)


def hira_to_kata(text: str) -> str:
    out = []
    for ch in text:
        code = ord(ch)
        if 0x3041 <= code <= 0x3096:
            out.append(chr(code + 0x60))
        else:
            out.append(ch)
    return "".join(out)


def reading_vowel_of_kana(ch: str) -> str:
    table = {
        "ア": "ア", "カ": "ア", "ガ": "ア", "サ": "ア", "ザ": "ア", "タ": "ア", "ダ": "ア", "ナ": "ア", "ハ": "ア", "バ": "ア", "パ": "ア", "マ": "ア", "ヤ": "ア", "ラ": "ア", "ワ": "ア",
        "イ": "イ", "キ": "イ", "ギ": "イ", "シ": "イ", "ジ": "イ", "チ": "イ", "ヂ": "イ", "ニ": "イ", "ヒ": "イ", "ビ": "イ", "ピ": "イ", "ミ": "イ", "リ": "イ", "ヰ": "イ",
        "ウ": "ウ", "ク": "ウ", "グ": "ウ", "ス": "ウ", "ズ": "ウ", "ツ": "ウ", "ヅ": "ウ", "ヌ": "ウ", "フ": "ウ", "ブ": "ウ", "プ": "ウ", "ム": "ウ", "ユ": "ウ", "ル": "ウ", "ヴ": "ウ",
        "エ": "エ", "ケ": "エ", "ゲ": "エ", "セ": "エ", "ゼ": "エ", "テ": "エ", "デ": "エ", "ネ": "エ", "ヘ": "エ", "ベ": "エ", "ペ": "エ", "メ": "エ", "レ": "エ", "ヱ": "エ",
        "オ": "オ", "コ": "オ", "ゴ": "オ", "ソ": "オ", "ゾ": "オ", "ト": "オ", "ド": "オ", "ノ": "オ", "ホ": "オ", "ボ": "オ", "ポ": "オ", "モ": "オ", "ヨ": "オ", "ロ": "オ", "ヲ": "オ",
        "ァ": "ア", "ャ": "ア",
        "ィ": "イ",
        "ゥ": "ウ", "ュ": "ウ",
        "ェ": "エ",
        "ォ": "オ", "ョ": "オ",
    }
    return table.get(ch, "")


def expand_long_mark(reading: str) -> str:
    """カタカナ/ひらがなの長音記号を母音へ展開する。例: エーアイ -> エエアイ"""
    kata = hira_to_kata(reading)
    out = []
    prev = ""
    for ch in kata:
        if ch == "ー":
            vowel = reading_vowel_of_kana(prev)
            out.append(vowel or "ー")
        else:
            out.append(ch)
            if ch not in "ッッンン":
                prev = ch
    expanded_kata = "".join(out)
    # 元がひらがな中心ならひらがなへ戻す
    hira_count = sum(1 for c in reading if 0x3040 <= ord(c) <= 0x309F)
    kata_count = sum(1 for c in reading if 0x30A0 <= ord(c) <= 0x30FF)
    return kata_to_hira(expanded_kata) if hira_count > kata_count else expanded_kata


def collapse_vowel_runs(reading: str) -> str:
    """連続母音を長音記号に寄せる簡易変換。例: エエアイ -> エーアイ"""
    kata = hira_to_kata(reading)
    result = []
    i = 0
    while i < len(kata):
        ch = kata[i]
        result.append(ch)
        if i + 1 < len(kata):
            vowel = reading_vowel_of_kana(ch)
            if vowel and kata[i + 1] == vowel:
                result.append("ー")
                i += 2
                continue
        i += 1
    collapsed = "".join(result)
    hira_count = sum(1 for c in reading if 0x3040 <= ord(c) <= 0x309F)
    kata_count = sum(1 for c in reading if 0x30A0 <= ord(c) <= 0x30FF)
    return kata_to_hira(collapsed) if hira_count > kata_count else collapsed


def insert_commas_for_acronym(reading: str) -> str:
    """エーアイ -> エー、アイ のように長音を含むカタカナ塊を軽く区切る。"""
    # 雑に「ー」を含む2〜6文字程度のカタカナ塊が連続する場合だけ候補を作る。
    parts = re.findall(r"[ァ-ヶー]{2,6}", reading)
    if len(parts) >= 2:
        return "、".join(parts)
    # エーアイのように連続しているものは「ー」の直後で区切る候補を作る。
    if "ー" in reading and "、" not in reading and len(reading) <= 12:
        return re.sub(r"ー(?=[ァ-ヶ])", "ー、", reading)
    return reading


def unique_pattern_items(items: List[Dict[str, str]], original_reading: str, max_items: int) -> List[Dict[str, str]]:
    seen_texts = {original_reading}
    out: List[Dict[str, str]] = []
    for item in items:
        reading = item.get("reading", "")
        text = item.get("text", "")
        key = text or reading
        if not key or key in seen_texts:
            continue
        seen_texts.add(key)
        out.append(item)
        if len(out) >= max_items:
            break
    return out


def auto_generate_patterns_for_entry(surface: str, reading: str, max_items: int) -> List[Dict[str, str]]:
    """読み方調整候補を自動生成する。

    目的は正解の推定ではなく、VOICEVOXに渡す文字列の微調整候補を
    まとめて聴き比べるための候補作成。
    """
    candidates: List[Dict[str, str]] = []

    hira = kata_to_hira(reading)
    kata = hira_to_kata(reading)
    if hira != reading:
        candidates.append({"label": "ひらがな化", "reading": hira})
    if kata != reading:
        candidates.append({"label": "カタカナ化", "reading": kata})

    expanded = expand_long_mark(reading)
    if expanded != reading:
        candidates.append({"label": "長音を母音化", "reading": expanded})

    collapsed = collapse_vowel_runs(reading)
    if collapsed != reading:
        candidates.append({"label": "母音連続を長音化", "reading": collapsed})

    comma = insert_commas_for_acronym(reading)
    if comma != reading:
        candidates.append({"label": "区切り追加", "text": comma + "。"})

    # 登録語と読みを合わせて読ませる文中確認。単語だけだと差が分かりにくい語の保険。
    candidates.append({"label": "文中確認", "text": f"{surface}は、{reading}と読みます。"})

    return unique_pattern_items(candidates, reading, max_items)


def auto_generate_patterns(entries: List[DictEntry], max_items: int) -> Dict[str, List[Dict[str, str]]]:
    return {
        e.surface: auto_generate_patterns_for_entry(e.surface, e.reading, max_items)
        for e in entries
    }


def merge_patterns(
    auto_patterns: Dict[str, List[Dict[str, str]]],
    manual_patterns: Dict[str, List[Dict[str, str]]],
    max_items: int,
) -> Dict[str, List[Dict[str, str]]]:
    result: Dict[str, List[Dict[str, str]]] = {}
    for key in sorted(set(auto_patterns) | set(manual_patterns)):
        merged = []
        seen = set()
        # 手動指定を先頭にする
        for item in manual_patterns.get(key, []) + auto_patterns.get(key, []):
            marker = item.get("text") or item.get("reading") or json.dumps(item, ensure_ascii=False, sort_keys=True)
            if marker in seen:
                continue
            seen.add(marker)
            merged.append(item)
            if len(merged) >= max_items:
                break
        if merged:
            result[key] = merged
    return result


def write_patterns_json(patterns: Dict[str, List[Dict[str, str]]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(patterns, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_global_variants(values: Optional[List[str]]) -> List[Dict[str, str]]:
    """--variant 'ラベル=テンプレート' を読む。全単語に追加する比較音声用。"""
    out: List[Dict[str, str]] = []
    for idx, value in enumerate(values or [], start=1):
        if "=" not in value:
            raise ValueError("--variant は 'ラベル=テンプレート' の形で指定してください。")
        label, template = value.split("=", 1)
        label = label.strip() or f"追加パターン {idx}"
        template = template.strip()
        if not template:
            raise ValueError("--variant のテンプレートが空です。")
        out.append({"label": label, "template": template})
    return out


def attach_variants(
    entries: List[DictEntry],
    out_dir: Path,
    baseline_template: str,
    custom_template: str,
    patterns: Dict[str, List[Dict[str, str]]],
    pattern_template: str,
    global_variants: List[Dict[str, str]],
) -> None:
    for no, entry in enumerate(entries, start=1):
        variants: List[AudioVariant] = []

        base_text = apply_template(
            baseline_template,
            surface=entry.surface,
            reading=entry.reading,
            no=no,
            label="辞書なし",
        )
        variants.append(AudioVariant(key="baseline", label="辞書なし", text=base_text))

        custom_text = apply_template(
            custom_template,
            surface=entry.surface,
            reading=entry.reading,
            no=no,
            label="カスタム辞書",
        )
        variants.append(AudioVariant(key="custom", label="カスタム辞書", text=custom_text))

        for idx, item in enumerate(patterns.get(entry.surface, []), start=1):
            label = item["label"]
            alt_reading = item.get("reading") or entry.reading
            text = item.get("text") or apply_template(
                pattern_template,
                surface=entry.surface,
                reading=alt_reading,
                no=no,
                label=label,
            )
            variants.append(AudioVariant(key=f"pattern{idx}", label=label, text=text))

        for idx, item in enumerate(global_variants, start=1):
            label = item["label"]
            text = apply_template(
                item["template"],
                surface=entry.surface,
                reading=entry.reading,
                no=no,
                label=label,
            )
            variants.append(AudioVariant(key=f"extra{idx}", label=label, text=text))

        for v in variants:
            name = f"{no:04d}_{sanitize_filename(entry.surface)}_{v.key}_{entry.id}.wav"
            v.audio_file = f"audio/{name}"

        entry.variants = variants


def post_json(url: str, payload: Any, timeout: int) -> Any:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else b""
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            body = res.read()
            ctype = res.headers.get("Content-Type", "")
            if "application/json" in ctype:
                return json.loads(body.decode("utf-8"))
            return body
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise VoicevoxError(f"HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise VoicevoxError(f"VOICEVOX ENGINE に接続できません: {e}") from e


def create_audio_query(base_url: str, text: str, speaker: int, timeout: int) -> Dict[str, Any]:
    params = urllib.parse.urlencode({"text": text, "speaker": speaker})
    url = f"{base_url.rstrip('/')}/audio_query?{params}"
    result = post_json(url, None, timeout)
    if not isinstance(result, dict):
        raise VoicevoxError("/audio_query の応答がJSONオブジェクトではありません。")
    return result


def synthesize_wav(base_url: str, query: Dict[str, Any], speaker: int, timeout: int) -> bytes:
    params = urllib.parse.urlencode({"speaker": speaker})
    url = f"{base_url.rstrip('/')}/synthesis?{params}"
    result = post_json(url, query, timeout)
    if not isinstance(result, (bytes, bytearray)):
        raise VoicevoxError("/synthesis の応答がWAVバイト列ではありません。")
    return bytes(result)


def generate_audio(
    entries: List[DictEntry],
    out_dir: Path,
    base_url: str,
    speaker: int,
    timeout: int,
    sleep_sec: float,
    overwrite: bool,
) -> None:
    audio_root = out_dir / "audio"
    audio_root.mkdir(parents=True, exist_ok=True)

    total = sum(len(e.variants) for e in entries)
    done = 0
    for entry in entries:
        for variant in entry.variants:
            done += 1
            wav_path = out_dir / variant.audio_file
            if wav_path.exists() and not overwrite:
                print(f"[{done}/{total}] skip {entry.surface} / {variant.label}")
                continue
            print(f"[{done}/{total}] synthesize {entry.surface} / {variant.label}: {variant.text}")
            query = create_audio_query(base_url, variant.text, speaker, timeout)
            wav = synthesize_wav(base_url, query, speaker, timeout)
            wav_path.write_bytes(wav)
            if sleep_sec > 0:
                time.sleep(sleep_sec)


def entry_to_dict(entry: DictEntry) -> Dict[str, Any]:
    return {
        "id": entry.id,
        "surface": entry.surface,
        "reading": entry.reading,
        "source_id": entry.source_id,
        "variants": [asdict(v) for v in entry.variants],
    }


def write_data_js(entries: List[DictEntry], out_dir: Path, title: str) -> None:
    data = [entry_to_dict(e) for e in entries]
    text = (
        "window.DICT_CHECK_TITLE = "
        + json.dumps(title, ensure_ascii=False)
        + ";\nwindow.DICT_ENTRIES = "
        + json.dumps(data, ensure_ascii=False, indent=2)
        + ";\n"
    )
    (out_dir / "data.js").write_text(text, encoding="utf-8")


def write_manifest(entries: List[DictEntry], out_dir: Path) -> None:
    path = out_dir / "manifest.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        fieldnames = ["no", "id", "surface", "reading", "variant_key", "variant_label", "variant_text", "audio_file"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for no, entry in enumerate(entries, start=1):
            for v in entry.variants:
                writer.writerow({
                    "no": no,
                    "id": entry.id,
                    "surface": entry.surface,
                    "reading": entry.reading,
                    "variant_key": v.key,
                    "variant_label": v.label,
                    "variant_text": v.text,
                    "audio_file": v.audio_file,
                })


def write_index_html(out_dir: Path, title: str) -> None:
    # 注意:
    # このHTMLは Python の raw f-string で生成している。
    # JavaScript 内の改行文字は、HTML生成時に実改行化しないよう '\\n' として書く。
    html_text = rf"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{ --bg:#f6f7f9; --panel:#fff; --line:#d8dde6; --text:#1f2937; --muted:#667085; --accent:#2563eb; --ok:#0f7b3b; --ng:#b42318; --hold:#a15c00; --unchecked:#667085; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background:var(--bg); color:var(--text); }}
    header {{ padding:14px 18px; border-bottom:1px solid var(--line); background:#fff; position:sticky; top:0; z-index:20; }}
    h1 {{ margin:0 0 6px; font-size:20px; }}
    .sub {{ color:var(--muted); font-size:13px; }}
    .layout {{ display:grid; grid-template-columns: 260px minmax(420px, 1fr) 520px; gap:12px; padding:12px; height:calc(100vh - 72px); }}
    .panel {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; overflow:hidden; min-height:0; }}
    .panel h2 {{ margin:0; padding:12px 14px; font-size:15px; border-bottom:1px solid var(--line); background:#fbfcfe; }}
    .filter-body, .detail-body {{ padding:12px; overflow:auto; height:calc(100% - 45px); }}
    label {{ display:block; font-size:12px; color:var(--muted); margin:10px 0 4px; }}
    input, select, textarea, button {{ font:inherit; }}
    input, select, textarea {{ width:100%; padding:8px 9px; border:1px solid var(--line); border-radius:8px; background:#fff; }}
    textarea {{ min-height:92px; resize:vertical; }}
    button {{ border:1px solid var(--line); background:#fff; border-radius:8px; padding:8px 10px; cursor:pointer; }}
    button.primary {{ background:var(--accent); color:#fff; border-color:var(--accent); }}
    button.ok {{ color:#fff; background:var(--ok); border-color:var(--ok); }}
    button.ng {{ color:#fff; background:var(--ng); border-color:var(--ng); }}
    button.hold {{ color:#fff; background:var(--hold); border-color:var(--hold); }}
    .btnrow {{ display:flex; gap:8px; flex-wrap:wrap; margin:10px 0; align-items:center; }}
    .stats {{ display:grid; grid-template-columns:1fr 1fr; gap:8px; margin:10px 0; }}
    .stat {{ border:1px solid var(--line); border-radius:9px; padding:8px; background:#fbfcfe; }}
    .stat b {{ display:block; font-size:18px; }}
    .table-wrap {{ height:calc(100% - 52px); overflow:auto; }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; }}
    th, td {{ border-bottom:1px solid #edf0f5; padding:8px 7px; vertical-align:middle; }}
    th {{ position:sticky; top:0; background:#fbfcfe; z-index:5; text-align:left; color:#475467; }}
    tr {{ cursor:pointer; }}
    tr:hover, tr.selected {{ background:#eef4ff; }}
    .pager {{ height:52px; display:flex; align-items:center; justify-content:space-between; padding:8px 10px; border-bottom:1px solid var(--line); background:#fff; }}
    .badge {{ display:inline-block; min-width:68px; text-align:center; border-radius:999px; padding:3px 8px; font-size:12px; font-weight:600; }}
    .status-unchecked {{ background:#eef1f5; color:var(--unchecked); }}
    .status-ok {{ background:#e7f6ec; color:var(--ok); }}
    .status-ng {{ background:#fdecec; color:var(--ng); }}
    .status-hold {{ background:#fff3df; color:var(--hold); }}
    .muted {{ color:var(--muted); }}
    .term {{ font-size:24px; font-weight:700; line-height:1.35; margin:4px 0 4px; }}
    .reading {{ font-size:16px; color:#344054; margin-bottom:12px; }}
    .variant-card {{ border:1px solid var(--line); border-radius:10px; padding:10px; margin:10px 0; background:#fbfcfe; }}
    .variant-head {{ display:flex; justify-content:space-between; gap:8px; align-items:center; margin-bottom:6px; }}
    .variant-label {{ font-weight:700; }}
    .variant-text {{ white-space:pre-wrap; font-size:13px; color:#344054; margin:6px 0; }}
    audio {{ width:100%; margin:6px 0; }}
    .small {{ font-size:12px; }}
    .hidden {{ display:none; }}
    @media (max-width: 1180px) {{ .layout {{ grid-template-columns:1fr; height:auto; }} .panel {{ min-height:300px; }} .table-wrap {{ height:420px; }} }}
  </style>
</head>
<body>
<header>
  <h1>{html.escape(title)}</h1>
  <div class="sub">辞書なし、カスタム辞書、読み方調整パターンを単語ごとに聴き比べできます。記録はブラウザのローカルストレージに保存されます。</div>
</header>
<div class="layout">
  <section class="panel">
    <h2>絞り込み・進捗</h2>
    <div class="filter-body">
      <label>検索</label>
      <input id="q" placeholder="語句・読みで検索">
      <label>状態</label>
      <select id="statusFilter">
        <option value="all">すべて</option>
        <option value="unchecked">未確認</option>
        <option value="ok">OK</option>
        <option value="ng">NG</option>
        <option value="hold">保留</option>
      </select>
      <div class="stats">
        <div class="stat"><span>全体</span><b id="stAll">0</b></div>
        <div class="stat"><span>表示</span><b id="stShown">0</b></div>
        <div class="stat"><span>OK</span><b id="stOk">0</b></div>
        <div class="stat"><span>NG</span><b id="stNg">0</b></div>
        <div class="stat"><span>保留</span><b id="stHold">0</b></div>
        <div class="stat"><span>未確認</span><b id="stUnchecked">0</b></div>
      </div>
      <div class="btnrow">
        <button id="exportCsv">結果CSV</button>
        <button id="exportJson">結果JSON</button>
        <button id="importBtn">結果読込</button>
        <input id="importFile" class="hidden" type="file" accept=".json,application/json">
      </div>
      <p class="small muted">ショートカット: 1=辞書なし再生、2=カスタム辞書再生、3以降=調整パターン再生、O=OK、N=NG、H=保留、←/→=前後、/=検索</p>
    </div>
  </section>

  <section class="panel">
    <div class="pager">
      <div><button id="prevPage">前頁</button> <button id="nextPage">次頁</button></div>
      <div class="small muted"><span id="pageInfo"></span></div>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>No</th><th>語句</th><th>読み</th><th>パターン</th><th>状態</th></tr></thead>
        <tbody id="tbody"></tbody>
      </table>
    </div>
  </section>

  <section class="panel">
    <h2>聴き比べ</h2>
    <div class="detail-body" id="detail">
      <p class="muted">一覧から語句を選択してください。</p>
    </div>
  </section>
</div>
<script src="./data.js"></script>
<script>
const ENTRIES = window.DICT_ENTRIES || [];
const PAGE_SIZE = 100;
const STORAGE_KEY = 'dict_voice_compare:' + (window.DICT_CHECK_TITLE || 'default') + ':' + ENTRIES.length;
let state = loadState();
let filtered = [];
let page = 0;
let selectedId = ENTRIES[0]?.id || null;

function loadState() {{
  try {{ return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{{}}'); }} catch(e) {{ return {{}}; }}
}}
function saveState() {{ localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); }}
function rec(id) {{ if (!state[id]) state[id] = {{status:'unchecked', memo:'', checked_at:''}}; return state[id]; }}
function statusLabel(s) {{ return {{unchecked:'未確認', ok:'OK', ng:'NG', hold:'保留'}}[s || 'unchecked'] || '未確認'; }}
function badge(s) {{ s = s || 'unchecked'; return `<span class="badge status-${{s}}">${{statusLabel(s)}}</span>`; }}
function esc(s) {{ return String(s ?? '').replace(/[&<>"']/g, m => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[m])); }}
function today() {{ return new Date().toISOString().slice(0,10); }}

function applyFilters() {{
  const query = q.value.trim().toLowerCase();
  const sf = statusFilter.value;
  filtered = ENTRIES.filter(e => {{
    const r = rec(e.id);
    if (sf !== 'all' && (r.status || 'unchecked') !== sf) return false;
    if (query) {{
      const hay = [e.surface, e.reading, ...(e.variants || []).map(v => v.text)].join(' ').toLowerCase();
      if (!hay.includes(query)) return false;
    }}
    return true;
  }});
  if (page * PAGE_SIZE >= filtered.length) page = 0;
  renderStats();
  renderTable();
  renderDetail();
}}
function renderStats() {{
  const counts = {{ok:0, ng:0, hold:0, unchecked:0}};
  ENTRIES.forEach(e => counts[rec(e.id).status || 'unchecked']++);
  stAll.textContent = ENTRIES.length;
  stShown.textContent = filtered.length;
  stOk.textContent = counts.ok;
  stNg.textContent = counts.ng;
  stHold.textContent = counts.hold;
  stUnchecked.textContent = counts.unchecked;
}}
function renderTable() {{
  const start = page * PAGE_SIZE;
  const rows = filtered.slice(start, start + PAGE_SIZE);
  tbody.innerHTML = rows.map((e, i) => `
    <tr data-id="${{e.id}}" class="${{e.id === selectedId ? 'selected' : ''}}">
      <td>${{start + i + 1}}</td>
      <td title="${{esc(e.surface)}}">${{esc(e.surface)}}</td>
      <td title="${{esc(e.reading)}}">${{esc(e.reading)}}</td>
      <td>${{(e.variants || []).length}}</td>
      <td>${{badge(rec(e.id).status)}}</td>
    </tr>`).join('');
  tbody.querySelectorAll('tr').forEach(tr => tr.addEventListener('click', () => {{ selectedId = tr.dataset.id; renderTable(); renderDetail(); }}));
  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  pageInfo.textContent = `${{page + 1}} / ${{totalPages}} ページ（${{filtered.length}}件）`;
}}
function currentIndex() {{ return filtered.findIndex(e => e.id === selectedId); }}
function selectByDelta(delta) {{
  if (!filtered.length) return;
  let idx = currentIndex();
  if (idx < 0) idx = 0;
  idx = Math.max(0, Math.min(filtered.length - 1, idx + delta));
  selectedId = filtered[idx].id;
  page = Math.floor(idx / PAGE_SIZE);
  renderTable(); renderDetail();
}}
function setStatus(status) {{
  if (!selectedId) return;
  const r = rec(selectedId);
  r.status = status;
  r.checked_at = today();
  const memo = document.getElementById('memo');
  if (memo) r.memo = memo.value;
  saveState();
  renderStats(); renderTable(); renderDetail();
}}
function stopOtherAudio(current) {{
  document.querySelectorAll('audio').forEach(a => {{
    if (a !== current) {{ a.pause(); a.currentTime = 0; }}
  }});
}}
function playVariant(index) {{
  const audio = document.getElementById('audio_' + index);
  if (audio) {{ stopOtherAudio(audio); audio.play(); }}
}}
function renderDetail() {{
  const e = ENTRIES.find(x => x.id === selectedId);
  if (!e) {{ detail.innerHTML = '<p class="muted">対象がありません。</p>'; return; }}
  const r = rec(e.id);
  const variants = e.variants || [];
  detail.innerHTML = `
    <div class="term">${{esc(e.surface)}}</div>
    <div class="reading">カスタム辞書での読み: ${{esc(e.reading || '未設定')}}</div>
    <div class="btnrow">
      <button id="prevItem">前へ</button>
      <button id="nextItem">次へ</button>
      <button class="ok" id="okBtn">OK</button>
      <button class="ng" id="ngBtn">NG</button>
      <button class="hold" id="holdBtn">保留</button>
      <span>${{badge(r.status)}}</span>
    </div>
    ${{variants.map((v, i) => `
      <div class="variant-card">
        <div class="variant-head">
          <div class="variant-label">${{i + 1}}. ${{esc(v.label)}}</div>
          <button class="primary" onclick="playVariant(${{i}})">再生</button>
        </div>
        <audio id="audio_${{i}}" controls src="${{esc(v.audio_file)}}" onplay="stopOtherAudio(this)"></audio>
        <div class="variant-text">${{esc(v.text)}}</div>
      </div>
    `).join('')}}
    <label>メモ</label>
    <textarea id="memo" placeholder="例: 辞書なしとの差が分かりにくい、調整2が自然、アクセントが不自然など">${{esc(r.memo || '')}}</textarea>
    <div class="btnrow"><button id="saveMemo">メモ保存</button></div>
  `;
  prevItem.onclick = () => selectByDelta(-1);
  nextItem.onclick = () => selectByDelta(1);
  okBtn.onclick = () => {{ setStatus('ok'); selectByDelta(1); }};
  ngBtn.onclick = () => {{ setStatus('ng'); selectByDelta(1); }};
  holdBtn.onclick = () => {{ setStatus('hold'); selectByDelta(1); }};
  saveMemo.onclick = () => {{ rec(e.id).memo = memo.value; saveState(); renderStats(); }};
}}
function exportRows() {{
  return ENTRIES.map((e, i) => {{
    const variants = (e.variants || []).map(v => `${{v.label}}: ${{v.text}} -> ${{v.audio_file}}`).join(' / ');
    return {{no:i+1, id:e.id, surface:e.surface, reading:e.reading, variants, ...rec(e.id)}};
  }});
}}
function download(name, text, type) {{
  const blob = new Blob([text], {{type}});
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = name; a.click(); URL.revokeObjectURL(a.href);
}}
function toCsv(rows) {{
  const cols = ['no','id','surface','reading','status','checked_at','memo','variants'];
  const line = row => cols.map(c => '"' + String(row[c] ?? '').replace(/"/g,'""') + '"').join(',');
  return '\\uFEFF' + cols.join(',') + '\\n' + rows.map(line).join('\\n');
}}

prevPage.onclick = () => {{ page = Math.max(0, page - 1); renderTable(); }};
nextPage.onclick = () => {{ page = Math.min(Math.ceil(filtered.length / PAGE_SIZE) - 1, page + 1); renderTable(); }};
[q,statusFilter].forEach(el => el.addEventListener('input', applyFilters));
exportCsv.onclick = () => download('dict_check_result.csv', toCsv(exportRows()), 'text/csv;charset=utf-8');
exportJson.onclick = () => download('dict_check_result.json', JSON.stringify(state, null, 2), 'application/json');
importBtn.onclick = () => importFile.click();
importFile.onchange = async () => {{
  const file = importFile.files[0]; if (!file) return;
  const obj = JSON.parse(await file.text());
  state = obj; saveState(); applyFilters();
}};
document.addEventListener('keydown', ev => {{
  if (['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)) {{
    if (ev.key !== 'Escape') return;
    document.activeElement.blur(); return;
  }}
  if (/^[1-9]$/.test(ev.key)) {{
    ev.preventDefault();
    playVariant(Number(ev.key) - 1);
  }}
  if (ev.key.toLowerCase() === 'o') setStatus('ok'), selectByDelta(1);
  if (ev.key.toLowerCase() === 'n') setStatus('ng'), selectByDelta(1);
  if (ev.key.toLowerCase() === 'h') setStatus('hold'), selectByDelta(1);
  if (ev.key === 'ArrowRight') selectByDelta(1);
  if (ev.key === 'ArrowLeft') selectByDelta(-1);
  if (ev.key === '/') {{ ev.preventDefault(); q.focus(); }}
}});
applyFilters();
</script>
</body>
</html>"""
    (out_dir / "index.html").write_text(html_text, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="vocoslide カスタム辞書JSONから、辞書なし/辞書適用/読み方調整の聴き比べページを生成します。"
    )
    p.add_argument("--input", "-i", required=True, help="vocoslide カスタム辞書JSON。例: {\"AI\":\"エーアイ\"}")
    p.add_argument("--out", "-o", default="dict_check", help="出力ディレクトリ")
    p.add_argument("--speaker", type=int, default=3, help="VOICEVOX speaker/style ID")
    p.add_argument("--base-url", "--engine-url", dest="base_url", default="http://127.0.0.1:50021", help="VOICEVOX ENGINE URL")
    p.add_argument("--title", default="カスタム辞書 読み上げ聴き比べ", help="HTMLタイトル")

    p.add_argument("--baseline-template", default="{surface}。", help="辞書なし音声のテンプレート。既定: {surface}。")
    p.add_argument("--custom-template", default="{reading}。", help="カスタム辞書適用音声のテンプレート。既定: {reading}。")
    p.add_argument("--pattern-template", default="{reading}。", help="--patterns の reading 値から作る音声テンプレート。既定: {reading}。")
    p.add_argument("--patterns", type=Path, default=None, help="手動指定の読み方調整パターンJSON。自動生成分とマージします。")
    p.add_argument("--auto-patterns", action=argparse.BooleanOptionalAction, default=True, help="読み方調整パターンを自動生成する。既定: 有効")
    p.add_argument("--auto-patterns-out", type=Path, default=None, help="自動生成・マージ後の追加版JSONの保存先。既定: 出力先/reading_patterns.auto.json")
    p.add_argument("--max-auto-patterns", type=int, default=5, help="1語あたりの読み方調整パターン上限。既定: 5")
    p.add_argument(
        "--variant",
        action="append",
        help="全単語に追加する比較音声。'ラベル=テンプレート' の形で複数指定可。例: --variant 'ゆっくり確認={reading}、{reading}。'",
    )

    p.add_argument("--skip-audio", action="store_true", help="音声生成を行わずHTMLだけ作る")
    p.add_argument("--overwrite-audio", action="store_true", help="既存WAVを上書きする")
    p.add_argument("--limit", type=int, default=None, help="先頭N件だけ処理。試験用")
    p.add_argument("--timeout", type=int, default=120, help="VOICEVOX APIタイムアウト秒")
    p.add_argument("--sleep", type=float, default=0.0, help="連続合成時の待ち秒")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    entries = load_vocoslide_dict(input_path, args.limit)
    if not entries:
        print("有効な辞書語が見つかりませんでした。", file=sys.stderr)
        return 2

    manual_patterns = load_patterns(args.patterns)
    if args.auto_patterns:
        auto_patterns = auto_generate_patterns(entries, args.max_auto_patterns)
        patterns = merge_patterns(auto_patterns, manual_patterns, args.max_auto_patterns)
    else:
        patterns = manual_patterns

    patterns_out = args.auto_patterns_out or (out_dir / "reading_patterns.auto.json")
    write_patterns_json(patterns, patterns_out)
    print(f"読み方調整パターンJSON: {patterns_out}")

    global_variants = parse_global_variants(args.variant)
    attach_variants(
        entries=entries,
        out_dir=out_dir,
        baseline_template=args.baseline_template,
        custom_template=args.custom_template,
        patterns=patterns,
        pattern_template=args.pattern_template,
        global_variants=global_variants,
    )

    write_data_js(entries, out_dir, args.title)
    write_index_html(out_dir, args.title)
    write_manifest(entries, out_dir)

    if not args.skip_audio:
        try:
            generate_audio(entries, out_dir, args.base_url, args.speaker, args.timeout, args.sleep, args.overwrite_audio)
        except VoicevoxError as e:
            print(f"音声生成でエラー: {e}", file=sys.stderr)
            print("HTMLとmanifestは生成済みです。VOICEVOX ENGINEを起動して再実行するか、--skip-audio を使ってください。", file=sys.stderr)
            return 1

    print(f"完了: {out_dir / 'index.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
