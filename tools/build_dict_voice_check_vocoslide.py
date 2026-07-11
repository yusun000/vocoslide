#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_dict_voice_check.py

vocoslide のカスタム辞書 JSON（{"単語": "読み"}）を読み込み、
辞書なし / カスタム辞書 / 自動読み方調整 / reading_overrides 候補を
聴き比べできる静的HTMLページとVOICEVOX音声を生成します。

主な考え方:
  - 辞書なしが良ければ custom_dict から除去候補
  - カスタム辞書が良ければ custom_dict 維持候補
  - ひらがな化・長音化など reading で表現できるものは custom_dict の値更新候補
  - 区切りやアクセント・mora_pitches などは reading_overrides 候補
  - 採用済みの語は --results を渡すことで次回生成対象から除外
  - 保留語には reading_overrides 候補を追加生成
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
from typing import Any, Dict, List, Optional


@dataclass
class AudioVariant:
    key: str
    label: str
    text: str
    audio_file: str = ""
    action: str = "none"  # remove_custom_dict / keep_custom_dict / update_custom_dict / reading_override
    reading: str = ""
    source: str = ""      # baseline / custom / auto / override
    query_adjust: Dict[str, Any] = field(default_factory=dict)
    override: Dict[str, Any] = field(default_factory=dict)


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
    return (s or "word")[:max_len]


def make_stable_id(surface: str, reading: str, index: int) -> str:
    # indexを含めると同一単語でも順序変更でIDが変わるが、同一辞書の再実行では安定する。
    base = f"{index}\t{surface}\t{reading}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]


def load_vocoslide_dict(path: Path, limit: Optional[int]) -> List[DictEntry]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError('辞書JSONは {"単語": "読み"} 形式にしてください。')
    if raw and not all(isinstance(v, str) for v in raw.values()):
        raise ValueError('辞書JSONは {"単語": "読み"} 形式にしてください。VOICEVOXユーザー辞書形式は対象外です。')

    entries: List[DictEntry] = []
    for i, (surface, reading) in enumerate(raw.items(), start=1):
        surface = str(surface).strip()
        reading = str(reading).strip()
        if not surface:
            continue
        entries.append(DictEntry(
            id=make_stable_id(surface, reading, i),
            surface=surface,
            reading=reading,
            source_id=str(i),
        ))
        if limit and len(entries) >= limit:
            break
    return entries


def load_results(path: Optional[Path]) -> Dict[str, Any]:
    if not path:
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "items" in raw and isinstance(raw["items"], dict):
        return raw["items"]
    if isinstance(raw, dict):
        return raw
    raise ValueError("--results はチェックページから出力したJSONを指定してください。")


def result_for(entry: DictEntry, results: Dict[str, Any]) -> Dict[str, Any]:
    r = results.get(entry.id)
    if isinstance(r, dict):
        return r
    # 旧版や手編集用に surface キーも軽く見る
    r = results.get(entry.surface)
    return r if isinstance(r, dict) else {}


def is_confirmed(entry: DictEntry, results: Dict[str, Any]) -> bool:
    r = result_for(entry, results)
    return r.get("status") == "ok" and bool(r.get("adopted_variant_key"))


def is_hold(entry: DictEntry, results: Dict[str, Any]) -> bool:
    return result_for(entry, results).get("status") == "hold"


def kata_to_hira(text: str) -> str:
    return "".join(chr(ord(c) - 0x60) if 0x30A1 <= ord(c) <= 0x30F6 else c for c in text)


def hira_to_kata(text: str) -> str:
    return "".join(chr(ord(c) + 0x60) if 0x3041 <= ord(c) <= 0x3096 else c for c in text)


def vowel_of_kana(ch: str) -> str:
    groups = {
        "ア": "アカガサザタダナハバパマヤラワァャ",
        "イ": "イキギシジチヂニヒビピミリヰィ",
        "ウ": "ウクグスズツヅヌフブプムユルヴゥュ",
        "エ": "エケゲセゼテデネヘベペメレヱェ",
        "オ": "オコゴソゾトドノホボポモヨロヲォョ",
    }
    for v, chars in groups.items():
        if ch in chars:
            return v
    return ""


def expand_long_mark(reading: str) -> str:
    kata = hira_to_kata(reading)
    out: List[str] = []
    prev = ""
    for ch in kata:
        if ch == "ー":
            out.append(vowel_of_kana(prev) or "ー")
        else:
            out.append(ch)
            if ch not in "ンッ":
                prev = ch
    converted = "".join(out)
    hira_count = sum(1 for c in reading if 0x3040 <= ord(c) <= 0x309F)
    kata_count = sum(1 for c in reading if 0x30A0 <= ord(c) <= 0x30FF)
    return kata_to_hira(converted) if hira_count > kata_count else converted


def collapse_vowel_runs(reading: str) -> str:
    kata = hira_to_kata(reading)
    out: List[str] = []
    i = 0
    while i < len(kata):
        ch = kata[i]
        out.append(ch)
        if i + 1 < len(kata) and vowel_of_kana(ch) and kata[i + 1] == vowel_of_kana(ch):
            out.append("ー")
            i += 2
            continue
        i += 1
    converted = "".join(out)
    hira_count = sum(1 for c in reading if 0x3040 <= ord(c) <= 0x309F)
    kata_count = sum(1 for c in reading if 0x30A0 <= ord(c) <= 0x30FF)
    return kata_to_hira(converted) if hira_count > kata_count else converted


def comma_variant(reading: str) -> str:
    if "、" in reading:
        return reading
    if "ー" in reading and len(reading) <= 16:
        return re.sub(r"ー(?=[ァ-ヶぁ-ゖ])", "ー、", reading)
    return reading


def rough_mora_count(reading: str) -> int:
    kata = hira_to_kata(reading)
    small = set("ァィゥェォャュョヮぁぃぅぇぉゃゅょゎ")
    return max(1, sum(1 for c in kata if c not in small and c not in "。、，,. 　"))


def make_pitch_list(count: int, mode: str) -> List[float]:
    count = max(1, count)
    if mode == "flat":
        return [5.0 for _ in range(count)]
    if mode == "falling":
        return [round(5.2 - 0.08 * i, 2) for i in range(count)]
    if mode == "rising":
        return [round(4.7 + 0.08 * i, 2) for i in range(count)]
    return [5.0 for _ in range(count)]


def auto_patterns(entry: DictEntry, max_items: int) -> List[AudioVariant]:
    r = entry.reading
    candidates: List[AudioVariant] = []

    def add_reading(label: str, reading: str, key: str) -> None:
        if reading and reading != r:
            candidates.append(AudioVariant(
                key=key,
                label=label,
                text=f"{reading}。",
                action="update_custom_dict",
                reading=reading,
                source="auto",
            ))

    add_reading("ひらがな化", kata_to_hira(r), "auto_hira")
    add_reading("カタカナ化", hira_to_kata(r), "auto_kata")
    add_reading("長音を母音化", expand_long_mark(r), "auto_expand_long")
    add_reading("母音連続を長音化", collapse_vowel_runs(r), "auto_collapse_vowel")

    comma = comma_variant(r)
    if comma != r:
        candidates.append(AudioVariant(
            key="auto_comma",
            label="区切り追加",
            text=f"{comma}。",
            action="reading_override",
            reading=r,
            source="auto",
            override={"surface": entry.surface, "reading": r, "text": f"{comma}。", "note": "区切り追加候補"},
        ))

    candidates.append(AudioVariant(
        key="auto_sentence",
        label="文中確認",
        text=f"{entry.surface}は、{r}と読みます。",
        action="reading_override",
        reading=r,
        source="auto",
        override={"surface": entry.surface, "reading": r, "text": f"{entry.surface}は、{r}と読みます。", "note": "文中確認候補"},
    ))

    # 重複除去
    out: List[AudioVariant] = []
    seen = {r}
    for v in candidates:
        marker = v.text or v.reading
        if marker in seen:
            continue
        seen.add(marker)
        out.append(v)
        if len(out) >= max_items:
            break
    return out


def override_candidates(entry: DictEntry) -> List[AudioVariant]:
    r = entry.reading
    count = rough_mora_count(r)
    accents = [1, max(1, (count + 1) // 2), count]
    labels = ["アクセント先頭", "アクセント中央", "アクセント末尾"]
    out: List[AudioVariant] = []
    for idx, (accent, label) in enumerate(zip(accents, labels), start=1):
        override = {"surface": entry.surface, "reading": r, "accent": accent}
        out.append(AudioVariant(
            key=f"override_accent_{idx}",
            label=f"reading_overrides: {label}",
            text=f"{r}。",
            action="reading_override",
            reading=r,
            source="override",
            query_adjust={"accent": accent},
            override=override,
        ))
    for mode, label in [("flat", "ピッチ平坦"), ("falling", "ピッチ下降"), ("rising", "ピッチ上昇")]:
        pitches = make_pitch_list(count, mode)
        override = {"surface": entry.surface, "reading": r, "mora_pitches": pitches}
        out.append(AudioVariant(
            key=f"override_pitch_{mode}",
            label=f"reading_overrides: {label}",
            text=f"{r}。",
            action="reading_override",
            reading=r,
            source="override",
            query_adjust={"mora_pitches": pitches},
            override=override,
        ))
    return out


def load_manual_patterns(path: Optional[Path]) -> Dict[str, List[AudioVariant]]:
    if not path:
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("--patterns は {\"単語\": [...]} 形式のJSONにしてください。")
    result: Dict[str, List[AudioVariant]] = {}
    for surface, items in raw.items():
        if isinstance(items, str):
            items = [items]
        if not isinstance(items, list):
            raise ValueError(f"--patterns の {surface!r} は文字列または配列にしてください。")
        variants: List[AudioVariant] = []
        for idx, item in enumerate(items, start=1):
            if isinstance(item, str):
                variants.append(AudioVariant(
                    key=f"manual_{idx}", label=f"手動候補 {idx}", text=f"{item}。",
                    action="update_custom_dict", reading=item, source="manual"
                ))
            elif isinstance(item, dict):
                label = str(item.get("label") or f"手動候補 {idx}")
                reading = str(item.get("reading") or item.get("yomi") or item.get("kana") or "")
                text = str(item.get("text") or (f"{reading}。" if reading else ""))
                action = str(item.get("action") or ("update_custom_dict" if reading and not item.get("text") else "reading_override"))
                override = item.get("override") if isinstance(item.get("override"), dict) else {}
                query_adjust = item.get("query_adjust") if isinstance(item.get("query_adjust"), dict) else {}
                variants.append(AudioVariant(
                    key=f"manual_{idx}", label=label, text=text,
                    action=action, reading=reading, source="manual",
                    override=override, query_adjust=query_adjust,
                ))
            else:
                raise ValueError(f"--patterns の {surface!r} の {idx} 番目は文字列またはオブジェクトにしてください。")
        result[str(surface)] = variants
    return result


def attach_variants(
    entries: List[DictEntry],
    manual: Dict[str, List[AudioVariant]],
    results: Dict[str, Any],
    max_auto_patterns: int,
    include_override_candidates: str,
) -> List[DictEntry]:
    out: List[DictEntry] = []
    for no, e in enumerate(entries, start=1):
        if is_confirmed(e, results):
            continue
        variants: List[AudioVariant] = [
            AudioVariant(
                key="baseline", label="辞書なし", text=f"{e.surface}。",
                action="remove_custom_dict", reading="", source="baseline",
                override={"surface": e.surface, "action": "remove_custom_dict"},
            ),
            AudioVariant(
                key="custom", label="カスタム辞書", text=f"{e.reading}。",
                action="keep_custom_dict", reading=e.reading, source="custom",
                override={"surface": e.surface, "reading": e.reading, "action": "keep_custom_dict"},
            ),
        ]
        variants.extend(manual.get(e.surface, []))
        variants.extend(auto_patterns(e, max_auto_patterns))
        if include_override_candidates == "all" or (include_override_candidates == "hold" and is_hold(e, results)):
            variants.extend(override_candidates(e))

        # key重複対策
        used: Dict[str, int] = {}
        for v in variants:
            base_key = v.key
            used[base_key] = used.get(base_key, 0) + 1
            if used[base_key] > 1:
                v.key = f"{base_key}_{used[base_key]}"
            name = f"{no:04d}_{sanitize_filename(e.surface)}_{v.key}_{e.id}.wav"
            v.audio_file = f"audio/{name}"
        e.variants = variants
        out.append(e)
    return out


def write_pattern_files(entries: List[DictEntry], out_dir: Path) -> None:
    patterns: Dict[str, List[Dict[str, Any]]] = {}
    overrides: Dict[str, List[Dict[str, Any]]] = {}
    for e in entries:
        for v in e.variants:
            if v.source in {"auto", "manual"}:
                item: Dict[str, Any] = {"label": v.label}
                if v.action == "update_custom_dict":
                    item["reading"] = v.reading
                else:
                    item["text"] = v.text
                    if v.reading:
                        item["reading"] = v.reading
                    item["action"] = v.action
                patterns.setdefault(e.surface, []).append(item)
            if v.source == "override" or (v.action == "reading_override" and v.override):
                ov = dict(v.override)
                ov.setdefault("label", v.label)
                ov.setdefault("source", v.source)
                overrides.setdefault(e.surface, []).append(ov)
    (out_dir / "reading_patterns.auto.json").write_text(json.dumps(patterns, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "reading_overrides.candidates.json").write_text(json.dumps(overrides, ensure_ascii=False, indent=2), encoding="utf-8")


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
    result = post_json(f"{base_url.rstrip('/')}/audio_query?{params}", None, timeout)
    if not isinstance(result, dict):
        raise VoicevoxError("/audio_query の応答がJSONオブジェクトではありません。")
    return result


def apply_query_adjust(query: Dict[str, Any], adjust: Dict[str, Any]) -> Dict[str, Any]:
    if not adjust:
        return query
    q = json.loads(json.dumps(query, ensure_ascii=False))
    phrases = q.get("accent_phrases") or []
    if phrases:
        phrase = phrases[0]
        moras = phrase.get("moras") or []
        if "accent" in adjust:
            try:
                phrase["accent"] = max(1, min(int(adjust["accent"]), max(1, len(moras))))
            except Exception:
                pass
        if "mora_pitches" in adjust and isinstance(adjust["mora_pitches"], list):
            for mora, pitch in zip(moras, adjust["mora_pitches"]):
                if isinstance(mora, dict):
                    try:
                        mora["pitch"] = float(pitch)
                    except Exception:
                        pass
    for key in ["speedScale", "pitchScale", "intonationScale", "volumeScale", "prePhonemeLength", "postPhonemeLength"]:
        if key in adjust:
            try:
                q[key] = float(adjust[key])
            except Exception:
                pass
    return q


def synthesize_wav(base_url: str, query: Dict[str, Any], speaker: int, timeout: int) -> bytes:
    params = urllib.parse.urlencode({"speaker": speaker})
    result = post_json(f"{base_url.rstrip('/')}/synthesis?{params}", query, timeout)
    if not isinstance(result, (bytes, bytearray)):
        raise VoicevoxError("/synthesis の応答がWAVバイト列ではありません。")
    return bytes(result)


def generate_audio(entries: List[DictEntry], out_dir: Path, base_url: str, speaker: int, timeout: int, sleep_sec: float, overwrite: bool) -> None:
    (out_dir / "audio").mkdir(parents=True, exist_ok=True)
    total = sum(len(e.variants) for e in entries)
    done = 0
    for e in entries:
        for v in e.variants:
            done += 1
            wav_path = out_dir / v.audio_file
            if wav_path.exists() and not overwrite:
                print(f"[{done}/{total}] skip {e.surface} / {v.label}")
                continue
            print(f"[{done}/{total}] synthesize {e.surface} / {v.label}: {v.text}")
            query = create_audio_query(base_url, v.text, speaker, timeout)
            query = apply_query_adjust(query, v.query_adjust)
            wav_path.write_bytes(synthesize_wav(base_url, query, speaker, timeout))
            if sleep_sec > 0:
                time.sleep(sleep_sec)


def entry_to_dict(e: DictEntry) -> Dict[str, Any]:
    return {"id": e.id, "surface": e.surface, "reading": e.reading, "source_id": e.source_id, "variants": [asdict(v) for v in e.variants]}


def write_data_js(entries: List[DictEntry], out_dir: Path, title: str) -> None:
    text = "window.DICT_CHECK_TITLE = " + json.dumps(title, ensure_ascii=False) + ";\n"
    text += "window.DICT_ENTRIES = " + json.dumps([entry_to_dict(e) for e in entries], ensure_ascii=False, indent=2) + ";\n"
    (out_dir / "data.js").write_text(text, encoding="utf-8")


def write_manifest(entries: List[DictEntry], out_dir: Path) -> None:
    with (out_dir / "manifest.csv").open("w", encoding="utf-8-sig", newline="") as f:
        cols = ["no", "id", "surface", "reading", "variant_key", "variant_label", "variant_text", "action", "source", "audio_file", "override"]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for no, e in enumerate(entries, start=1):
            for v in e.variants:
                w.writerow({
                    "no": no, "id": e.id, "surface": e.surface, "reading": e.reading,
                    "variant_key": v.key, "variant_label": v.label, "variant_text": v.text,
                    "action": v.action, "source": v.source, "audio_file": v.audio_file,
                    "override": json.dumps(v.override, ensure_ascii=False),
                })


def write_index_html(out_dir: Path, title: str) -> None:
    html_text = r'''<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg:#f6f7f9;--panel:#fff;--line:#d8dde6;--text:#1f2937;--muted:#667085;--accent:#2563eb;--ok:#0f7b3b;--ng:#b42318;--hold:#a15c00;--unchecked:#667085}*{box-sizing:border-box}body{margin:0;font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:var(--bg);color:var(--text)}header{padding:14px 18px;border-bottom:1px solid var(--line);background:#fff;position:sticky;top:0;z-index:20}h1{margin:0 0 6px;font-size:20px}.sub{color:var(--muted);font-size:13px}.layout{display:grid;grid-template-columns:260px minmax(420px,1fr) 560px;gap:12px;padding:12px;height:calc(100vh - 72px)}.panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;overflow:hidden;min-height:0}.panel h2{margin:0;padding:12px 14px;font-size:15px;border-bottom:1px solid var(--line);background:#fbfcfe}.filter-body,.detail-body{padding:12px;overflow:auto;height:calc(100% - 45px)}label{display:block;font-size:12px;color:var(--muted);margin:10px 0 4px}input,select,textarea,button{font:inherit}input,select,textarea{width:100%;padding:8px 9px;border:1px solid var(--line);border-radius:8px;background:#fff}textarea{min-height:92px;resize:vertical}button{border:1px solid var(--line);background:#fff;border-radius:8px;padding:8px 10px;cursor:pointer}button.primary{background:var(--accent);color:#fff;border-color:var(--accent)}button.ok{color:#fff;background:var(--ok);border-color:var(--ok)}button.ng{color:#fff;background:var(--ng);border-color:var(--ng)}button.hold{color:#fff;background:var(--hold);border-color:var(--hold)}.btnrow{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0;align-items:center}.stats{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:10px 0}.stat{border:1px solid var(--line);border-radius:9px;padding:8px;background:#fbfcfe}.stat b{display:block;font-size:18px}.table-wrap{height:calc(100% - 52px);overflow:auto}table{width:100%;border-collapse:collapse;font-size:13px}th,td{border-bottom:1px solid #edf0f5;padding:8px 7px;vertical-align:middle}th{position:sticky;top:0;background:#fbfcfe;z-index:5;text-align:left;color:#475467}tr{cursor:pointer}tr:hover,tr.selected{background:#eef4ff}.pager{height:52px;display:flex;align-items:center;justify-content:space-between;padding:8px 10px;border-bottom:1px solid var(--line);background:#fff}.badge{display:inline-block;min-width:68px;text-align:center;border-radius:999px;padding:3px 8px;font-size:12px;font-weight:600}.status-unchecked{background:#eef1f5;color:var(--unchecked)}.status-ok{background:#e7f6ec;color:var(--ok)}.status-ng{background:#fdecec;color:var(--ng)}.status-hold{background:#fff3df;color:var(--hold)}.muted{color:var(--muted)}.term{font-size:24px;font-weight:700;line-height:1.35;margin:4px 0}.reading{font-size:16px;color:#344054;margin-bottom:12px}.variant-card{border:1px solid var(--line);border-radius:10px;padding:10px;margin:10px 0;background:#fbfcfe}.variant-card.adopted{outline:2px solid var(--ok);background:#f3fbf6}.variant-head{display:flex;justify-content:space-between;gap:8px;align-items:center;margin-bottom:6px}.variant-label{font-weight:700}.variant-text{white-space:pre-wrap;font-size:13px;color:#344054;margin:6px 0}.action{font-size:12px;color:#475467}audio{width:100%;margin:6px 0}.small{font-size:12px}.hidden{display:none}@media(max-width:1180px){.layout{grid-template-columns:1fr;height:auto}.panel{min-height:300px}.table-wrap{height:420px}}
</style>
</head>
<body>
<header><h1>__TITLE__</h1><div class="sub">採用した案をもとに custom_dict 候補と reading_overrides 候補を出力できます。採用済みJSONを次回 --results に渡すと生成対象から除外できます。</div></header>
<div class="layout">
<section class="panel"><h2>絞り込み・進捗</h2><div class="filter-body">
<label>検索</label><input id="q" placeholder="語句・読みで検索"><label>状態</label><select id="statusFilter"><option value="all">すべて</option><option value="unchecked">未確認</option><option value="ok">OK</option><option value="ng">NG</option><option value="hold">保留</option></select>
<div class="stats"><div class="stat"><span>全体</span><b id="stAll">0</b></div><div class="stat"><span>表示</span><b id="stShown">0</b></div><div class="stat"><span>OK</span><b id="stOk">0</b></div><div class="stat"><span>NG</span><b id="stNg">0</b></div><div class="stat"><span>保留</span><b id="stHold">0</b></div><div class="stat"><span>未確認</span><b id="stUnchecked">0</b></div></div>
<div class="btnrow"><button id="exportCsv">結果CSV</button><button id="exportJson">採用結果JSON</button><button id="exportDict">custom_dict候補</button><button id="exportOverrides">reading_overrides候補</button><button id="importBtn">結果読込</button><input id="importFile" class="hidden" type="file" accept=".json,application/json"></div>
<p class="small muted">1=辞書なし、2=カスタム辞書、3以降=候補再生。採用ボタンでその案をOK採用。H=保留、N=NG、←/→=前後、/=検索。</p>
</div></section>
<section class="panel"><div class="pager"><div><button id="prevPage">前頁</button> <button id="nextPage">次頁</button></div><div class="small muted"><span id="pageInfo"></span></div></div><div class="table-wrap"><table><thead><tr><th>No</th><th>語句</th><th>読み</th><th>採用</th><th>状態</th></tr></thead><tbody id="tbody"></tbody></table></div></section>
<section class="panel"><h2>聴き比べ・採用</h2><div class="detail-body" id="detail"><p class="muted">一覧から語句を選択してください。</p></div></section>
</div>
<script src="./data.js"></script>
<script>
const ENTRIES=window.DICT_ENTRIES||[];const PAGE_SIZE=100;const STORAGE_KEY='dict_voice_workflow:'+(window.DICT_CHECK_TITLE||'default')+':'+ENTRIES.length;let state=loadState();let filtered=[];let page=0;let selectedId=ENTRIES[0]?.id||null;
function loadState(){try{const obj=JSON.parse(localStorage.getItem(STORAGE_KEY)||'{}');return obj.items||obj}catch(e){return {}}}function saveState(){localStorage.setItem(STORAGE_KEY,JSON.stringify({version:2,items:state},null,2))}function rec(id){if(!state[id])state[id]={status:'unchecked',memo:'',checked_at:'',adopted_variant_key:'',adopted_variant:null};return state[id]}function statusLabel(s){return {unchecked:'未確認',ok:'OK',ng:'NG',hold:'保留'}[s||'unchecked']||'未確認'}function badge(s){s=s||'unchecked';return `<span class="badge status-${s}">${statusLabel(s)}</span>`}function esc(s){return String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}function today(){return new Date().toISOString().slice(0,10)}
function actionLabel(a){return {remove_custom_dict:'custom_dictから除去',keep_custom_dict:'custom_dict維持',update_custom_dict:'custom_dictの読みを更新',reading_override:'reading_overridesへ反映',none:'反映なし'}[a]||a}
function applyFilters(){const query=q.value.trim().toLowerCase();const sf=statusFilter.value;filtered=ENTRIES.filter(e=>{const r=rec(e.id);if(sf!=='all'&&(r.status||'unchecked')!==sf)return false;if(query){const hay=[e.surface,e.reading,r.adopted_variant?.label||'',...(e.variants||[]).map(v=>v.text+' '+v.label)].join(' ').toLowerCase();if(!hay.includes(query))return false}return true});if(page*PAGE_SIZE>=filtered.length)page=0;renderStats();renderTable();renderDetail()}
function renderStats(){const c={ok:0,ng:0,hold:0,unchecked:0};ENTRIES.forEach(e=>c[rec(e.id).status||'unchecked']++);stAll.textContent=ENTRIES.length;stShown.textContent=filtered.length;stOk.textContent=c.ok;stNg.textContent=c.ng;stHold.textContent=c.hold;stUnchecked.textContent=c.unchecked}
function renderTable(){const start=page*PAGE_SIZE;const rows=filtered.slice(start,start+PAGE_SIZE);tbody.innerHTML=rows.map((e,i)=>{const r=rec(e.id);return `<tr data-id="${e.id}" class="${e.id===selectedId?'selected':''}"><td>${start+i+1}</td><td title="${esc(e.surface)}">${esc(e.surface)}</td><td title="${esc(e.reading)}">${esc(e.reading)}</td><td>${esc(r.adopted_variant?.label||'')}</td><td>${badge(r.status)}</td></tr>`}).join('');tbody.querySelectorAll('tr').forEach(tr=>tr.addEventListener('click',()=>{selectedId=tr.dataset.id;renderTable();renderDetail()}));const totalPages=Math.max(1,Math.ceil(filtered.length/PAGE_SIZE));pageInfo.textContent=`${page+1} / ${totalPages} ページ（${filtered.length}件）`}
function currentIndex(){return filtered.findIndex(e=>e.id===selectedId)}function selectByDelta(delta){if(!filtered.length)return;let idx=currentIndex();if(idx<0)idx=0;idx=Math.max(0,Math.min(filtered.length-1,idx+delta));selectedId=filtered[idx].id;page=Math.floor(idx/PAGE_SIZE);renderTable();renderDetail()}
function setStatus(status){if(!selectedId)return;const r=rec(selectedId);r.status=status;r.checked_at=today();const memo=document.getElementById('memo');if(memo)r.memo=memo.value;if(status!=='ok'){r.adopted_variant_key='';r.adopted_variant=null}saveState();renderStats();renderTable();renderDetail()}
function adoptVariant(key){const e=ENTRIES.find(x=>x.id===selectedId);if(!e)return;const v=(e.variants||[]).find(x=>x.key===key);if(!v)return;const r=rec(e.id);r.status='ok';r.checked_at=today();r.adopted_variant_key=v.key;r.adopted_variant={key:v.key,label:v.label,action:v.action,reading:v.reading||'',text:v.text||'',override:v.override||{},source:v.source||''};r.surface=e.surface;r.original_reading=e.reading;const memo=document.getElementById('memo');if(memo)r.memo=memo.value;saveState();renderStats();renderTable();renderDetail()}
function stopOtherAudio(current){document.querySelectorAll('audio').forEach(a=>{if(a!==current){a.pause();a.currentTime=0}})}function playVariant(index){const audio=document.getElementById('audio_'+index);if(audio){stopOtherAudio(audio);audio.play()}}
function renderDetail(){const e=ENTRIES.find(x=>x.id===selectedId);if(!e){detail.innerHTML='<p class="muted">対象がありません。</p>';return}const r=rec(e.id);const variants=e.variants||[];detail.innerHTML=`<div class="term">${esc(e.surface)}</div><div class="reading">現在のカスタム辞書読み: ${esc(e.reading||'未設定')}</div><div class="btnrow"><button id="prevItem">前へ</button><button id="nextItem">次へ</button><button class="ng" id="ngBtn">NG</button><button class="hold" id="holdBtn">保留</button><span>${badge(r.status)}</span></div>${r.adopted_variant?`<p class="small">採用中: <b>${esc(r.adopted_variant.label)}</b> / ${esc(actionLabel(r.adopted_variant.action))}</p>`:''}${variants.map((v,i)=>`<div class="variant-card ${r.adopted_variant_key===v.key?'adopted':''}"><div class="variant-head"><div class="variant-label">${i+1}. ${esc(v.label)}</div><div><button onclick="playVariant(${i})" class="primary">再生</button> <button onclick="adoptVariant('${esc(v.key)}')">この案を採用</button></div></div><div class="action">反映先: ${esc(actionLabel(v.action))}</div><audio id="audio_${i}" controls src="${esc(v.audio_file)}" onplay="stopOtherAudio(this)"></audio><div class="variant-text">${esc(v.text)}</div>${v.override&&Object.keys(v.override).length?`<details><summary>override候補</summary><pre>${esc(JSON.stringify(v.override,null,2))}</pre></details>`:''}</div>`).join('')}<label>メモ</label><textarea id="memo" placeholder="例: override候補の中央アクセントが自然、辞書なしで十分など">${esc(r.memo||'')}</textarea><div class="btnrow"><button id="saveMemo">メモ保存</button></div>`;prevItem.onclick=()=>selectByDelta(-1);nextItem.onclick=()=>selectByDelta(1);ngBtn.onclick=()=>setStatus('ng');holdBtn.onclick=()=>setStatus('hold');saveMemo.onclick=()=>{rec(e.id).memo=memo.value;saveState();renderStats()}}
function exportRows(){return ENTRIES.map((e,i)=>{const r=rec(e.id);return {no:i+1,id:e.id,surface:e.surface,reading:e.reading,status:r.status||'unchecked',checked_at:r.checked_at||'',adopted_label:r.adopted_variant?.label||'',adopted_action:r.adopted_variant?.action||'',adopted_reading:r.adopted_variant?.reading||'',memo:r.memo||''}})}function download(name,text,type){const blob=new Blob([text],{type});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=name;a.click();URL.revokeObjectURL(a.href)}function toCsv(rows){const cols=['no','id','surface','reading','status','checked_at','adopted_label','adopted_action','adopted_reading','memo'];const line=row=>cols.map(c=>'"'+String(row[c]??'').replace(/"/g,'""')+'"').join(',');return '\uFEFF'+cols.join(',')+'\n'+rows.map(line).join('\n')}
function proposedCustomDict(){const out={};ENTRIES.forEach(e=>{const r=rec(e.id);const v=r.adopted_variant;if(v&&v.action==='remove_custom_dict')return;if(v&&v.action==='update_custom_dict'&&v.reading){out[e.surface]=v.reading;return}out[e.surface]=e.reading});return out}
function proposedOverrides(){const out={};ENTRIES.forEach(e=>{const r=rec(e.id);const v=r.adopted_variant;if(v&&v.action==='reading_override'){const ov=Object.assign({surface:e.surface,reading:v.reading||e.reading,label:v.label,text:v.text},v.override||{});if(!out[e.surface])out[e.surface]=[];out[e.surface].push(ov)}});return out}
prevPage.onclick=()=>{page=Math.max(0,page-1);renderTable()};nextPage.onclick=()=>{page=Math.min(Math.ceil(filtered.length/PAGE_SIZE)-1,page+1);renderTable()};[q,statusFilter].forEach(el=>el.addEventListener('input',applyFilters));exportCsv.onclick=()=>download('dict_check_result.csv',toCsv(exportRows()),'text/csv;charset=utf-8');exportJson.onclick=()=>download('dict_check_adoptions.json',JSON.stringify({version:2,items:state},null,2),'application/json');exportDict.onclick=()=>download('custom_dict.proposed.json',JSON.stringify(proposedCustomDict(),null,2),'application/json');exportOverrides.onclick=()=>download('reading_overrides.proposed.json',JSON.stringify(proposedOverrides(),null,2),'application/json');importBtn.onclick=()=>importFile.click();importFile.onchange=async()=>{const file=importFile.files[0];if(!file)return;const obj=JSON.parse(await file.text());state=obj.items||obj;saveState();applyFilters()};document.addEventListener('keydown',ev=>{if(['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)){if(ev.key!=='Escape')return;document.activeElement.blur();return}if(/^[1-9]$/.test(ev.key)){ev.preventDefault();playVariant(Number(ev.key)-1)}if(ev.key.toLowerCase()==='n')setStatus('ng');if(ev.key.toLowerCase()==='h')setStatus('hold');if(ev.key==='ArrowRight')selectByDelta(1);if(ev.key==='ArrowLeft')selectByDelta(-1);if(ev.key==='/'){ev.preventDefault();q.focus()}});applyFilters();
</script>
</body></html>'''
    html_text = html_text.replace("__TITLE__", html.escape(title))
    (out_dir / "index.html").write_text(html_text, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="vocoslide カスタム辞書の読み上げ確認・採用ワークフローを生成します。")
    p.add_argument("--input", "-i", required=True, help='vocoslide カスタム辞書JSON。例: {"AI":"エーアイ"}')
    p.add_argument("--out", "-o", default="dict_check", help="出力ディレクトリ")
    p.add_argument("--speaker", type=int, default=3, help="VOICEVOX speaker/style ID")
    p.add_argument("--base-url", "--engine-url", dest="base_url", default="http://127.0.0.1:50021", help="VOICEVOX ENGINE URL")
    p.add_argument("--title", default="カスタム辞書 読み上げ採用チェック", help="HTMLタイトル")
    p.add_argument("--patterns", type=Path, default=None, help="手動指定の読み方調整パターンJSON")
    p.add_argument("--results", type=Path, default=None, help="前回チェックページから出力した dict_check_adoptions.json。OK採用済み語は生成対象外、保留語はoverride候補生成対象になります。")
    p.add_argument("--include-override-candidates", choices=["none", "hold", "all"], default="hold", help="reading_overrides候補を生成する対象。既定: hold")
    p.add_argument("--max-auto-patterns", type=int, default=5, help="1語あたりの自動読み方調整候補数")
    p.add_argument("--skip-audio", action="store_true", help="音声生成を行わずHTMLだけ作る")
    p.add_argument("--overwrite-audio", action="store_true", help="既存WAVを上書きする")
    p.add_argument("--limit", type=int, default=None, help="先頭N件だけ処理。試験用")
    p.add_argument("--timeout", type=int, default=120, help="VOICEVOX APIタイムアウト秒")
    p.add_argument("--sleep", type=float, default=0.0, help="連続合成時の待ち秒")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    entries = load_vocoslide_dict(Path(args.input), args.limit)
    results = load_results(args.results)
    manual = load_manual_patterns(args.patterns)
    entries = attach_variants(entries, manual, results, args.max_auto_patterns, args.include_override_candidates)
    if not entries:
        print("生成対象がありません。前回結果で全件が採用済みの可能性があります。")
        return 0
    write_pattern_files(entries, out_dir)
    write_data_js(entries, out_dir, args.title)
    write_index_html(out_dir, args.title)
    write_manifest(entries, out_dir)
    print(f"読み方調整案JSON: {out_dir / 'reading_patterns.auto.json'}")
    print(f"reading_overrides候補JSON: {out_dir / 'reading_overrides.candidates.json'}")
    if not args.skip_audio:
        try:
            generate_audio(entries, out_dir, args.base_url, args.speaker, args.timeout, args.sleep, args.overwrite_audio)
        except VoicevoxError as e:
            print(f"音声生成でエラー: {e}", file=sys.stderr)
            print("HTMLと候補JSONは生成済みです。VOICEVOX ENGINEを起動して再実行するか、--skip-audio を使ってください。", file=sys.stderr)
            return 1
    print(f"完了: {out_dir / 'index.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
