#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_dict_voice_check.py

vocoslide のカスタム辞書 JSON（{"単語":"読み"}）を読み込み、辞書検証用の静的 HTML ページと音声 WAV を生成します。

前提:
  - VOICEVOX ENGINE が http://127.0.0.1:50021 で起動していること
  - Python 標準ライブラリのみで動作

使い方例:
  python build_dict_voice_check.py --input dict/custom_dict.json --out dict_check --speaker 3
  python build_dict_voice_check.py --input dict/custom_dict.json --out dict_check --speaker 3 --limit 100
  python build_dict_voice_check.py --input dict/custom_dict.json --out dict_check --speaker 3 --skip-audio

生成物:
  dict_check/
    index.html       静的チェックページ
    data.js          辞書データ
    audio/*.wav      各単語の検証音声
    manifest.csv     元データ確認用CSV
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


SURFACE_KEYS = ("surface", "term", "word", "text", "表記", "単語", "語句")
READING_KEYS = ("pronunciation", "reading", "yomi", "kana", "読み", "読み方")
CATEGORY_KEYS = ("category", "word_type", "type", "品詞", "分類")
PRIORITY_KEYS = ("priority", "重要度", "優先度")
ACCENT_KEYS = ("accent_type", "accent", "アクセント")


@dataclass
class DictEntry:
    id: str
    surface: str
    reading: str = ""
    category: str = ""
    priority: str = ""
    accent_type: str = ""
    source_id: str = ""
    audio_file: str = ""
    test_text: str = ""


def pick(obj: Dict[str, Any], keys: Iterable[str], default: str = "") -> str:
    for key in keys:
        if key in obj and obj[key] is not None:
            value = obj[key]
            if isinstance(value, (dict, list)):
                return json.dumps(value, ensure_ascii=False)
            return str(value)
    return default


def normalize_rows(raw: Any) -> List[Tuple[str, Dict[str, Any]]]:
    """辞書JSONを内部行形式へ変換する。

    vocoslide の標準形式は次のシンプルなオブジェクト。
        {"AI": "エーアイ", "MVP": "エムブイピー"}

    互換用に、配列形式や VOICEVOX user_dict 風の形式も最低限受け付ける。
    """
    if isinstance(raw, list):
        rows: List[Tuple[str, Dict[str, Any]]] = []
        for i, row in enumerate(raw, start=1):
            if isinstance(row, dict):
                rows.append((str(i), row))
        return rows

    if isinstance(raw, dict):
        # よくあるラップ: {"words": [...]}, {"items": [...]}, {"dict": {...}}
        for key in ("words", "items", "entries", "data"):
            if key in raw and isinstance(raw[key], list):
                return normalize_rows(raw[key])
        for key in ("user_dict", "dict", "dictionary"):
            if key in raw and isinstance(raw[key], dict):
                return normalize_rows(raw[key])

        # vocoslide 標準形式: {"表記": "読み"}
        if raw and all(isinstance(v, str) for v in raw.values()):
            return [
                (str(i), {"surface": str(surface), "reading": reading})
                for i, (surface, reading) in enumerate(raw.items(), start=1)
            ]

        # VOICEVOX GET /user_dict 風: {uuid: {surface:..., pronunciation:...}}
        if raw and all(isinstance(v, dict) for v in raw.values()):
            return [(str(k), v) for k, v in raw.items()]

        # 単一行
        if any(k in raw for k in SURFACE_KEYS):
            return [("1", raw)]

    raise ValueError(
        '辞書JSONの形式を解釈できませんでした。vocoslide形式 {"単語":"読み"} のJSONを指定してください。'
    )


def sanitize_filename(text: str, max_len: int = 60) -> str:
    s = re.sub(r"[\\/:*?\"<>|\s]+", "_", text.strip())
    s = re.sub(r"_+", "_", s).strip("_")
    if not s:
        s = "word"
    return s[:max_len]


def make_stable_id(source_id: str, surface: str, reading: str, index: int) -> str:
    base = f"{source_id}\t{surface}\t{reading}\t{index}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]


def build_test_text(no: int, surface: str, reading: str, template: str) -> str:
    return template.format(no=no, surface=surface, reading=reading or "未設定")


def load_entries(path: Path, template: str, limit: Optional[int] = None) -> List[DictEntry]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = normalize_rows(raw)
    entries: List[DictEntry] = []
    for i, (source_id, obj) in enumerate(rows, start=1):
        surface = pick(obj, SURFACE_KEYS).strip()
        if not surface:
            continue
        reading = pick(obj, READING_KEYS).strip()
        category = pick(obj, CATEGORY_KEYS).strip()
        priority = pick(obj, PRIORITY_KEYS).strip()
        accent = pick(obj, ACCENT_KEYS).strip()
        sid = make_stable_id(source_id, surface, reading, i)
        audio_name = f"{i:04d}_{sanitize_filename(surface)}_{sid}.wav"
        entry = DictEntry(
            id=sid,
            surface=surface,
            reading=reading,
            category=category,
            priority=priority,
            accent_type=accent,
            source_id=source_id,
            audio_file=f"audio/{audio_name}",
            test_text=build_test_text(i, surface, reading, template),
        )
        entries.append(entry)
        if limit and len(entries) >= limit:
            break
    return entries


class VoicevoxError(RuntimeError):
    pass


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


def generate_audio(entries: List[DictEntry], out_dir: Path, base_url: str, speaker: int, timeout: int, sleep_sec: float, overwrite: bool) -> None:
    audio_root = out_dir / "audio"
    audio_root.mkdir(parents=True, exist_ok=True)
    total = len(entries)
    for idx, entry in enumerate(entries, start=1):
        wav_path = out_dir / entry.audio_file
        if wav_path.exists() and not overwrite:
            print(f"[{idx}/{total}] skip {entry.surface}")
            continue
        print(f"[{idx}/{total}] synthesize {entry.surface}")
        query = create_audio_query(base_url, entry.test_text, speaker, timeout)
        wav = synthesize_wav(base_url, query, speaker, timeout)
        wav_path.write_bytes(wav)
        if sleep_sec > 0:
            time.sleep(sleep_sec)


def write_manifest(entries: List[DictEntry], out_dir: Path) -> None:
    path = out_dir / "manifest.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(entries[0]).keys()) if entries else ["id"])
        writer.writeheader()
        for e in entries:
            writer.writerow(asdict(e))


def js_string(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def write_data_js(entries: List[DictEntry], out_dir: Path, title: str) -> None:
    data = [asdict(e) for e in entries]
    text = "window.DICT_CHECK_TITLE = " + js_string(title) + ";\n" + "window.DICT_ENTRIES = " + js_string(data) + ";\n"
    (out_dir / "data.js").write_text(text, encoding="utf-8")


def write_index_html(out_dir: Path, title: str) -> None:
    html_text = f"""<!doctype html>
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
    .layout {{ display:grid; grid-template-columns: 260px minmax(420px, 1fr) 420px; gap:12px; padding:12px; height:calc(100vh - 72px); }}
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
    .btnrow {{ display:flex; gap:8px; flex-wrap:wrap; margin:10px 0; }}
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
    .term {{ font-size:24px; font-weight:700; line-height:1.35; margin:4px 0 8px; }}
    .reading {{ font-size:16px; color:#344054; margin-bottom:10px; }}
    .meta {{ display:grid; grid-template-columns:100px 1fr; gap:6px 10px; font-size:13px; margin:12px 0; }}
    .meta div:nth-child(odd) {{ color:var(--muted); }}
    audio {{ width:100%; margin:8px 0; }}
    .test-text {{ white-space:pre-wrap; border:1px solid var(--line); background:#fbfcfe; border-radius:8px; padding:10px; line-height:1.65; }}
    .small {{ font-size:12px; }}
    .hidden {{ display:none; }}
    @media (max-width: 1100px) {{ .layout {{ grid-template-columns:1fr; height:auto; }} .panel {{ min-height:300px; }} .table-wrap {{ height:420px; }} }}
  </style>
</head>
<body>
<header>
  <h1>{html.escape(title)}</h1>
  <div class="sub">検索、絞り込み、音声再生、OK/NG/保留の記録ができます。記録はブラウザのローカルストレージに保存されます。</div>
</header>
<div class="layout">
  <section class="panel">
    <h2>絞り込み・進捗</h2>
    <div class="filter-body">
      <label>検索</label>
      <input id="q" placeholder="語句・読み・カテゴリで検索">
      <label>状態</label>
      <select id="statusFilter">
        <option value="all">すべて</option>
        <option value="unchecked">未確認</option>
        <option value="ok">OK</option>
        <option value="ng">NG</option>
        <option value="hold">保留</option>
      </select>
      <label>カテゴリ</label>
      <select id="categoryFilter"><option value="all">すべて</option></select>
      <label>優先度</label>
      <select id="priorityFilter"><option value="all">すべて</option></select>
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
      <p class="small muted">ショートカット: Space=再生、O=OK、N=NG、H=保留、←/→=前後、/=検索</p>
    </div>
  </section>

  <section class="panel">
    <div class="pager">
      <div><button id="prevPage">前頁</button> <button id="nextPage">次頁</button></div>
      <div class="small muted"><span id="pageInfo"></span></div>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>No</th><th>語句</th><th>読み</th><th>分類</th><th>優先度</th><th>状態</th></tr></thead>
        <tbody id="tbody"></tbody>
      </table>
    </div>
  </section>

  <section class="panel">
    <h2>詳細確認</h2>
    <div class="detail-body" id="detail">
      <p class="muted">一覧から語句を選択してください。</p>
    </div>
  </section>
</div>
<script src="data.js"></script>
<script>
const ENTRIES = window.DICT_ENTRIES || [];
const PAGE_SIZE = 100;
const STORAGE_KEY = 'dict_voice_check:' + (window.DICT_CHECK_TITLE || 'default') + ':' + ENTRIES.length;
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

function initFilters() {{
  const cats = [...new Set(ENTRIES.map(e => e.category).filter(Boolean))].sort();
  const pris = [...new Set(ENTRIES.map(e => e.priority).filter(Boolean))].sort();
  categoryFilter.innerHTML += cats.map(v => `<option value="${{esc(v)}}">${{esc(v)}}</option>`).join('');
  priorityFilter.innerHTML += pris.map(v => `<option value="${{esc(v)}}">${{esc(v)}}</option>`).join('');
}}
function applyFilters() {{
  const query = q.value.trim().toLowerCase();
  const sf = statusFilter.value;
  const cf = categoryFilter.value;
  const pf = priorityFilter.value;
  filtered = ENTRIES.filter(e => {{
    const r = rec(e.id);
    if (sf !== 'all' && (r.status || 'unchecked') !== sf) return false;
    if (cf !== 'all' && e.category !== cf) return false;
    if (pf !== 'all' && e.priority !== pf) return false;
    if (query) {{
      const hay = [e.surface, e.reading, e.category, e.priority, e.accent_type].join(' ').toLowerCase();
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
      <td>${{esc(e.category)}}</td>
      <td>${{esc(e.priority)}}</td>
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
function renderDetail() {{
  const e = ENTRIES.find(x => x.id === selectedId);
  if (!e) {{ detail.innerHTML = '<p class="muted">対象がありません。</p>'; return; }}
  const r = rec(e.id);
  detail.innerHTML = `
    <div class="term">${{esc(e.surface)}}</div>
    <div class="reading">読み: ${{esc(e.reading || '未設定')}}</div>
    <audio id="audio" controls src="${{esc(e.audio_file)}}"></audio>
    <div class="btnrow">
      <button class="primary" id="playBtn">再生</button>
      <button id="prevItem">前へ</button>
      <button id="nextItem">次へ</button>
    </div>
    <div class="btnrow">
      <button class="ok" id="okBtn">OK</button>
      <button class="ng" id="ngBtn">NG</button>
      <button class="hold" id="holdBtn">保留</button>
      <span>${{badge(r.status)}}</span>
    </div>
    <div class="meta">
      <div>分類</div><div>${{esc(e.category)}}</div>
      <div>優先度</div><div>${{esc(e.priority)}}</div>
      <div>アクセント</div><div>${{esc(e.accent_type)}}</div>
      <div>確認日</div><div>${{esc(r.checked_at || '')}}</div>
      <div>ID</div><div>${{esc(e.source_id)}}</div>
    </div>
    <label>検証文</label>
    <div class="test-text">${{esc(e.test_text)}}</div>
    <label>メモ</label>
    <textarea id="memo" placeholder="例: 文中でアクセントが不自然、読みが違う、保留理由など">${{esc(r.memo || '')}}</textarea>
    <div class="btnrow"><button id="saveMemo">メモ保存</button></div>
  `;
  playBtn.onclick = () => document.getElementById('audio').play();
  prevItem.onclick = () => selectByDelta(-1);
  nextItem.onclick = () => selectByDelta(1);
  okBtn.onclick = () => {{ setStatus('ok'); selectByDelta(1); }};
  ngBtn.onclick = () => {{ setStatus('ng'); selectByDelta(1); }};
  holdBtn.onclick = () => {{ setStatus('hold'); selectByDelta(1); }};
  saveMemo.onclick = () => {{ rec(e.id).memo = memo.value; saveState(); renderStats(); }};
}}
function exportRows() {{
  return ENTRIES.map((e, i) => ({{no:i+1, ...e, ...rec(e.id)}}));
}}
function download(name, text, type) {{
  const blob = new Blob([text], {{type}});
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = name; a.click(); URL.revokeObjectURL(a.href);
}}
function toCsv(rows) {{
  const cols = ['no','id','surface','reading','category','priority','accent_type','status','checked_at','memo','audio_file','test_text','source_id'];
  const line = row => cols.map(c => '"' + String(row[c] ?? '').replace(/"/g,'""') + '"').join(',');
  return '\ufeff' + cols.join(',') + '\\n' + rows.map(line).join('\\n');
}}

prevPage.onclick = () => {{ page = Math.max(0, page - 1); renderTable(); }};
nextPage.onclick = () => {{ page = Math.min(Math.ceil(filtered.length / PAGE_SIZE) - 1, page + 1); renderTable(); }};
[q,statusFilter,categoryFilter,priorityFilter].forEach(el => el.addEventListener('input', applyFilters));
exportCsv.onclick = () => download('dict_check_result.csv', toCsv(exportRows()), 'text/csv');
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
  if (ev.key === ' ') {{ ev.preventDefault(); document.getElementById('audio')?.play(); }}
  if (ev.key.toLowerCase() === 'o') setStatus('ok'), selectByDelta(1);
  if (ev.key.toLowerCase() === 'n') setStatus('ng'), selectByDelta(1);
  if (ev.key.toLowerCase() === 'h') setStatus('hold'), selectByDelta(1);
  if (ev.key === 'ArrowRight') selectByDelta(1);
  if (ev.key === 'ArrowLeft') selectByDelta(-1);
  if (ev.key === '/') {{ ev.preventDefault(); q.focus(); }}
}});
initFilters();
applyFilters();
</script>
</body>
</html>"""
    (out_dir / "index.html").write_text(html_text, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="VOICEVOX辞書JSONから静的HTML型の読み上げチェックページを生成します。")
    p.add_argument("--input", "-i", required=True, help="カスタム辞書JSONファイル")
    p.add_argument("--out", "-o", default="dict_check", help="出力ディレクトリ")
    p.add_argument("--speaker", type=int, default=3, help="VOICEVOX speaker/style ID")
    p.add_argument("--base-url", default="http://127.0.0.1:50021", help="VOICEVOX ENGINE URL")
    p.add_argument("--title", default="カスタム辞書 読み上げチェック", help="HTMLタイトル")
    p.add_argument("--template", default="No. {no}。登録語、{surface}。読みは、{reading}。確認文です。{surface}を読み上げます。", help="検証文テンプレート。{no} {surface} {reading} が使えます。")
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

    entries = load_entries(input_path, args.template, args.limit)
    if not entries:
        print("有効な辞書語が見つかりませんでした。", file=sys.stderr)
        return 2

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
