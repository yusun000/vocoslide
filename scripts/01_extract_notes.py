import os
import json
import sys
import re
import shutil
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

# プロジェクトルートをパスに追加
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.extractor import get_processor

VIDEO_EXTS = {".mp4", ".mov", ".avi", ".wmv", ".m4v", ".mpg", ".mpeg"}
VIDEO_COMMAND_PATTERNS = [
    (re.compile(r"^\s*@video-before\s*:\s*(.+?)\s*$", re.IGNORECASE), "before"),
    (re.compile(r"^\s*@video-after\s*:\s*(.+?)\s*$", re.IGNORECASE), "after"),
    (re.compile(r"^\s*@video\s*:\s*(.+?)\s*$", re.IGNORECASE), "after"),
]


@dataclass
class NoteVideoCommand:
    position: str
    path: str
    raw: str


def load_dictionary(dict_path):
    """カスタム辞書の読み込み"""
    if os.path.exists(dict_path):
        with open(dict_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def apply_dictionary(text, dictionary):
    """辞書に基づいてテキストを置換（長い単語優先）"""
    sorted_keys = sorted(dictionary.keys(), key=len, reverse=True)
    for key in sorted_keys:
        text = text.replace(key, dictionary[key])
    return text


def split_long_text(text, max_len=20): # デフォルトを20程度に変更
    """
    1. 元々ある '//' を最優先の分割点とする
    2. 分割された各区間が max_len を超える場合のみ、句読点を考慮して結合分割する
    """
    user_segments = re.split(r'//|／／', text)
    final_results = []

    for segment in user_segments:
        segment = segment.strip()
        if not segment:
            continue

        if len(segment) <= max_len:
            final_results.append(segment)
        else:
            chunks = re.findall(r'[^。、？！?!\s]+[。、？！?!\s]*', segment)
            current_line = ""
            for chunk in chunks:
                if len(current_line) + len(chunk) <= max_len:
                    current_line += chunk
                else:
                    if current_line:
                        final_results.append(current_line.strip())
                    current_line = chunk
            if current_line:
                final_results.append(current_line.strip())

    return "／／".join(final_results)


def parse_note_video_commands(note_text: str):
    """
    ノート欄の @video 系制御行を抽出し、読み上げ対象本文と分離する。

    対応する制御行:
      @video: path/to/movie.mp4        -> 読み上げ後に挿入
      @video-before: path/to/movie.mp4 -> 読み上げ前に挿入
      @video-after: path/to/movie.mp4  -> 読み上げ後に挿入
    """
    commands = []
    spoken_lines = []

    for line in note_text.splitlines():
        matched = False
        for pattern, position in VIDEO_COMMAND_PATTERNS:
            m = pattern.match(line)
            if m:
                video_path = m.group(1).strip().strip('"').strip("'")
                commands.append(NoteVideoCommand(position=position, path=video_path, raw=line))
                matched = True
                break
        if not matched:
            spoken_lines.append(line)

    return "\n".join(spoken_lines).strip(), commands


def resolve_note_video_path(pptx_path: str, video_path: str) -> str:
    """ノート制御行の動画パスをPPTX所在フォルダ基準で解決する。"""
    p = Path(video_path).expanduser()
    if p.is_absolute():
        return str(p)
    return str((Path(pptx_path).resolve().parent / p).resolve())


def video_command_to_dict(cmd: NoteVideoCommand, pptx_path: str):
    resolved = resolve_note_video_path(pptx_path, cmd.path)
    return {
        "source": "note",
        "position": cmd.position,
        "path": cmd.path,
        "resolved_path": resolved,
        "raw": cmd.raw,
    }


def resolve_pptx_target(base_rels_path: str, target: str) -> str:
    """
    slideN.xml.rels 内の Target を PPTX 内部パスへ変換する。
    Target は _rels フォルダではなく、元XML（ppt/slides/slideN.xml）基準の相対パス。
    """
    if "/_rels/" in base_rels_path:
        base_dir = base_rels_path.split("/_rels/")[0]
    else:
        base_dir = os.path.dirname(base_rels_path)
    return os.path.normpath(os.path.join(base_dir, target)).replace("\\", "/").lstrip("/")


def extract_embedded_videos_from_pptx(pptx_path: str, output_dir: str):
    """PPTXに埋め込まれた動画をスライド番号ごとに抽出する。

    PowerPointのPPTXでは、同じ動画実体に対して media / video など複数の
    Relationship が作られることがある。そのため、同一スライド内では
    PPTX内部パス（ppt/media/mediaX.mp4 等）単位で重複排除する。
    """
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    videos_by_slide = {}

    with zipfile.ZipFile(pptx_path, "r") as z:
        names = set(z.namelist())
        rel_files = sorted(
            [
                name for name in names
                if name.startswith("ppt/slides/_rels/slide") and name.endswith(".xml.rels")
            ],
            key=lambda p: int(Path(p).name.replace("slide", "").replace(".xml.rels", ""))
        )
        ns = {"rel": "http://schemas.openxmlformats.org/package/2006/relationships"}

        for rel_path in rel_files:
            slide_no = int(Path(rel_path).name.replace("slide", "").replace(".xml.rels", ""))
            root = ET.fromstring(z.read(rel_path))
            video_index = 0
            seen_internal_paths = set()

            for rel in root.findall("rel:Relationship", ns):
                target = unquote(rel.attrib.get("Target", ""))
                target_mode = rel.attrib.get("TargetMode", "")
                if target_mode.lower() == "external":
                    continue

                ext = Path(target).suffix.lower()
                if ext not in VIDEO_EXTS:
                    continue

                internal_path = resolve_pptx_target(rel_path, target)
                if internal_path not in names:
                    print(f"[WARN] slide {slide_no}: 埋め込み動画の実体が見つかりません: {target}")
                    continue

                if internal_path in seen_internal_paths:
                    print(f"[SKIP] slide {slide_no}: 重複した埋め込み動画参照をスキップしました: {internal_path}")
                    continue
                seen_internal_paths.add(internal_path)

                video_index += 1
                out_name = f"slide_{slide_no:03d}_embedded_{video_index:02d}{ext}"
                out_path = output / out_name
                with z.open(internal_path) as src, open(out_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)

                item = {
                    "source": "embedded",
                    "position": "after",
                    "path": internal_path,
                    "resolved_path": str(out_path.resolve()),
                    "rel_type": rel.attrib.get("Type", ""),
                }
                videos_by_slide.setdefault(slide_no, []).append(item)
                print(f"[OK] slide {slide_no}: 埋め込み動画を抽出しました: {out_path}")

    return videos_by_slide


def main():
    if len(sys.argv) < 2:
        print("使用法: python 01_extract_notes.py [PPTXパス]")
        sys.exit(1)

    pptx_path = sys.argv[1]
    
    # パス設定
    dict_path = "dict/custom_dict.json"
    output_json = "temp/notes.json"
    check_file = "temp/check_notes.txt"
    embedded_video_dir = "temp/embedded_videos"
    
    # --- 設定値 ---
    MAX_CHARS_PER_LINE = 35  # 1行あたりの最大文字数
    # --------------

    print(f"--- ステップ01: ノート抽出開始 ({os.path.basename(pptx_path)}) ---")

    try:
        processor = get_processor()
        raw_notes = processor.extract_notes(pptx_path)
        dictionary = load_dictionary(dict_path)
        embedded_videos = extract_embedded_videos_from_pptx(pptx_path, embedded_video_dir)

        formatted_notes = []
        os.makedirs("temp", exist_ok=True)

        with open(check_file, "w", encoding="utf-8-sig") as f_check:
            f_check.write("# 抽出されたノートです。'／／' は改行・分割位置を示します。\n")
            f_check.write("# 手動の // も反映済みです。必要に応じて修正してください。\n")
            f_check.write("# @video / @video-before / @video-after の制御行は読み上げから除外されます。\n\n")

            for i, raw_text in enumerate(raw_notes):
                page_num = i + 1
                spoken_text, video_commands = parse_note_video_commands(raw_text)

                # 元の改行をスペースに置換（手動の // は維持される）
                clean_text = spoken_text.strip().replace('\n', ' ')
                
                # 1. 辞書適用（読み上げ用）
                # reading_base = apply_dictionary(clean_text, dictionary)
                
                # 2. 手動分割優先 ＋ 自動分割
                final_subtitle = split_long_text(clean_text, max_len=MAX_CHARS_PER_LINE)
                final_reading = apply_dictionary(final_subtitle, dictionary)

                note_videos_before = []
                note_videos_after = []
                for cmd in video_commands:
                    item = video_command_to_dict(cmd, pptx_path)
                    if cmd.position == "before":
                        note_videos_before.append(item)
                    else:
                        note_videos_after.append(item)

                embedded_for_slide = embedded_videos.get(page_num, [])
                
                formatted_notes.append({
                    "slide_number": page_num,
                    "text": final_subtitle,
                    "reading": final_reading,
                    "videos_before": note_videos_before,
                    "videos_after": note_videos_after,
                    "embedded_videos": embedded_for_slide,
                })
                
                f_check.write(f"--- PAGE_{page_num:03d} ---\n")
                f_check.write(f"字幕: {final_subtitle}\n")
                f_check.write(f"読上: {final_reading}\n")
                for item in note_videos_before:
                    exists = "OK" if os.path.exists(item["resolved_path"]) else "MISSING"
                    f_check.write(f"動画(before): {item['path']} -> {item['resolved_path']} [{exists}]\n")
                for item in note_videos_after:
                    exists = "OK" if os.path.exists(item["resolved_path"]) else "MISSING"
                    f_check.write(f"動画(after): {item['path']} -> {item['resolved_path']} [{exists}]\n")
                for item in embedded_for_slide:
                    f_check.write(f"埋め込み動画(after): {item['path']} -> {item['resolved_path']} [OK]\n")
                f_check.write("\n")

        # JSON保存
        with open(output_json, "w", encoding="utf-8") as f_json:
            json.dump(formatted_notes, f_json, ensure_ascii=False, indent=4)

        note_video_count = sum(len(n["videos_before"]) + len(n["videos_after"]) for n in formatted_notes)
        embedded_video_count = sum(len(n["embedded_videos"]) for n in formatted_notes)
        print(f"成功: {len(formatted_notes)} 枚のノートを処理しました。")
        print(f"設定: 最大 {MAX_CHARS_PER_LINE} 文字 (手動分割優先)")
        print(f"動画指定: ノート指定 {note_video_count} 件, PPTX埋め込み {embedded_video_count} 件")

    except Exception as e:
        print(f"エラー: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
