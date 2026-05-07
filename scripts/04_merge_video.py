import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# scripts/ からの実行でもプロジェクトルート基準の相対パスを扱いやすくする
PROJECT_ROOT = Path(__file__).resolve().parent.parent

from PIL import Image, ImageDraw, ImageFont

# キャラクターIDと表示名の対応表
SPEAKER_MAP = {
    21: "麒ヶ島宗麟",
    3: "ずんだもん",
    2: "四国めたん",
    8: "春日部つむぎ",
}

DEFAULT_FPS = 30
DEFAULT_AUDIO_RATE = 48000
DEFAULT_AUDIO_CHANNELS = 2
DEFAULT_AUDIO_BITRATE = "192k"


def load_config(config_path: str = "config.json"):
    if not os.path.exists(config_path):
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def parse_args():
    parser = argparse.ArgumentParser(description="スライド画像と音声を結合して動画生成")
    parser.add_argument("base_name", help="出力動画名のベース")
    parser.add_argument("--timing-file", default="temp/timings.json")
    parser.add_argument("--notes-file", default="temp/notes.json", help="動画挿入指定を含むノートJSON")
    parser.add_argument("--slide-dir", default="temp/slides")
    parser.add_argument("--audio-dir", default="temp/audio")
    parser.add_argument("--work-dir", default="temp/work")
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--ffmpeg-path", default=None)
    parser.add_argument("--ffprobe-path", default=None)
    parser.add_argument("--silence-duration", type=float, default=None)
    parser.add_argument("--credit-duration", type=float, default=None)
    parser.add_argument("--keep-work", action="store_true", help="work配下の中間ファイルを削除しない")
    parser.add_argument("--quiet", action="store_true", help="進捗ログを抑制")
    parser.add_argument("--stats-file", default="temp/merge_stats.json", help="処理統計の保存先")
    return parser.parse_args()


def log(message: str, quiet: bool = False):
    if not quiet:
        print(message)


def format_srt_time(seconds):
    ms = int(round((seconds % 1) * 1000))
    if ms == 1000:
        seconds += 1
        ms = 0
    s = int(seconds % 60)
    m = int((seconds // 60) % 60)
    h = int(seconds // 3600)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def save_vtt(srt_content, vtt_path):
    vtt_text = "WEBVTT\n\n" + srt_content
    vtt_text = vtt_text.replace(',', '.')
    with open(vtt_path, "w", encoding="utf-8-sig") as f:
        f.write(vtt_text)


def run_ffmpeg(cmd, quiet=False, label="ffmpeg"):
    started = time.perf_counter()
    proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
    elapsed = time.perf_counter() - started
    log(f"[{label}] {elapsed:.2f}s", quiet)
    if not quiet and proc.stderr:
        stderr = proc.stderr.strip()
        if stderr:
            tail = "\n".join(stderr.splitlines()[-5:])
            if tail:
                log(f"[{label}] ffmpeg tail:\n{tail}", quiet)
    return elapsed


def quote_concat_path(path: str) -> str:
    return str(path).replace('\\', '/').replace("'", "'\\''")


def guess_ffprobe_path(ffmpeg_path: str) -> str:
    if ffmpeg_path and Path(ffmpeg_path).name.lower().startswith("ffmpeg"):
        candidate = str(Path(ffmpeg_path).with_name("ffprobe" + Path(ffmpeg_path).suffix))
        if Path(candidate).exists():
            return candidate
    return "ffprobe"


def get_media_duration(media_path: str, ffprobe_path: str) -> float:
    cmd = [
        ffprobe_path,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        media_path,
    ]
    proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return float(proc.stdout.strip())


def has_audio_stream(media_path: str, ffprobe_path: str) -> bool:
    cmd = [
        ffprobe_path,
        "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=index",
        "-of", "csv=p=0",
        media_path,
    ]
    proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return bool(proc.stdout.strip())


def get_image_size(image_path: str):
    with Image.open(image_path) as img:
        return img.size


def fit_video_filter(width: int, height: int, fps: int) -> str:
    return (
        "setpts=PTS-STARTPTS,"
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
        f"fps={fps},format=yuv420p"
    )


def still_image_filter(width: int, height: int, fps: int) -> str:
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
        f"fps={fps},format=yuv420p"
    )


def normalize_insert_video(
    input_path: str,
    output_path: str,
    ffmpeg_path: str,
    ffprobe_path: str,
    width: int,
    height: int,
    fps: int,
    audio_rate: int,
    audio_channels: int,
    quiet: bool,
):
    """
    外部動画・埋め込み動画を、ページ動画と同じ仕様のMP4へ変換する。

    重要:
    - PTSを0始まりにリセットする
    - fps、解像度、pix_fmt、音声サンプルレート、チャンネル数を統一する
    - 音声なし動画にも無音トラックを付ける
    これをしないと concat 時にコマ送り、音声食い込み、表示時間ずれが起きやすい。
    """
    video_filter = fit_video_filter(width, height, fps)
    input_duration = get_media_duration(input_path, ffprobe_path)

    if has_audio_stream(input_path, ffprobe_path):
        filter_complex = (
            f"[0:v]{video_filter}[v];"
            f"[0:a]asetpts=PTS-STARTPTS,"
            f"aformat=sample_rates={audio_rate}:channel_layouts=stereo,apad[a]"
        )
        cmd = [
            ffmpeg_path, "-y",
            "-i", input_path,
            "-filter_complex", filter_complex,
            "-map", "[v]",
            "-map", "[a]",
            "-t", f"{input_duration:.6f}",
            "-c:v", "libx264",
            "-r", str(fps),
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-ar", str(audio_rate),
            "-ac", str(audio_channels),
            "-b:a", DEFAULT_AUDIO_BITRATE,
            "-movflags", "+faststart",
            output_path,
        ]
    else:
        cmd = [
            ffmpeg_path, "-y",
            "-i", input_path,
            "-f", "lavfi",
            "-t", f"{input_duration:.6f}",
            "-i", f"anullsrc=r={audio_rate}:cl=stereo",
            "-filter_complex", f"[0:v]{video_filter}[v]",
            "-map", "[v]",
            "-map", "1:a",
            "-t", f"{input_duration:.6f}",
            "-c:v", "libx264",
            "-r", str(fps),
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-ar", str(audio_rate),
            "-ac", str(audio_channels),
            "-b:a", DEFAULT_AUDIO_BITRATE,
            "-movflags", "+faststart",
            output_path,
        ]

    return run_ffmpeg(cmd, quiet, label=f"insert-video-{Path(output_path).stem}")


def make_page_audio(audio_inputs, parts_count: int, output_path: str, ffmpeg_path: str, silence_duration: float, audio_rate: int, audio_channels: int, quiet: bool, label: str):
    """複数の読み上げ音声を、各パート前の無音を含めて1本のWAVへまとめる。"""
    audio_filter = ""
    delay_ms = int(silence_duration * 1000)
    for i in range(parts_count):
        audio_filter += (
            f"[{i}:a]adelay={delay_ms}|{delay_ms},"
            f"aformat=sample_rates={audio_rate}:channel_layouts=stereo[a{i}];"
        )
    audio_filter += "".join([f"[a{i}]" for i in range(parts_count)]) + f"concat=n={parts_count}:v=0:a=1[aout]"

    cmd_audio = [ffmpeg_path, "-y"]
    for a_in in audio_inputs:
        cmd_audio.extend(["-i", a_in])
    cmd_audio.extend([
        "-filter_complex", audio_filter,
        "-map", "[aout]",
        "-ar", str(audio_rate),
        "-ac", str(audio_channels),
        output_path,
    ])
    return run_ffmpeg(cmd_audio, quiet, label=label)


def make_page_video(slide_img: str, audio_path: str, output_path: str, ffmpeg_path: str, duration: float, width: int, height: int, fps: int, audio_rate: int, audio_channels: int, quiet: bool, label: str):
    """スライド静止画と読み上げ音声から、挿入動画と同一仕様のページ動画を作る。"""
    cmd_video = [
        ffmpeg_path, "-y",
        "-loop", "1",
        "-framerate", str(fps),
        "-i", slide_img,
        "-i", audio_path,
        "-t", f"{duration:.6f}",
        "-vf", still_image_filter(width, height, fps),
        "-c:v", "libx264",
        "-r", str(fps),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-ar", str(audio_rate),
        "-ac", str(audio_channels),
        "-b:a", DEFAULT_AUDIO_BITRATE,
        "-movflags", "+faststart",
        output_path,
    ]
    return run_ffmpeg(cmd_video, quiet, label=label)


def load_notes_video_plan(notes_file: str):
    """notes.json からスライド番号ごとの動画挿入指定を読み込む。"""
    if not notes_file or not os.path.exists(notes_file):
        return {}
    with open(notes_file, "r", encoding="utf-8-sig") as f:
        notes = json.load(f)

    plan = {}
    for item in notes:
        slide_no = int(item.get("slide_number", 0))
        if not slide_no:
            continue
        plan[slide_no] = {
            "before": item.get("videos_before", []),
            "after": item.get("videos_after", []),
            "embedded": item.get("embedded_videos", []),
        }
    return plan


def resolve_video_item_path(item):
    if isinstance(item, str):
        return item
    return item.get("resolved_path") or item.get("path")


def append_insert_videos(
    f_list,
    video_items,
    page_num: int,
    slot: str,
    work_dir: str,
    ffmpeg_path: str,
    ffprobe_path: str,
    width: int,
    height: int,
    fps: int,
    audio_rate: int,
    audio_channels: int,
    current_total_seconds: float,
    stats: dict,
    quiet: bool,
    missing_policy: str = "warn",
):
    """指定された挿入動画を正規化し、concatリストへ追加して、経過秒数を返す。"""
    added_duration = 0.0
    for idx, item in enumerate(video_items, start=1):
        src = resolve_video_item_path(item)
        if not src:
            continue
        src = os.path.abspath(src)
        source_kind = item.get("source", "note") if isinstance(item, dict) else "note"
        if not os.path.exists(src):
            message = f"動画ファイルが見つかりません: slide={page_num}, slot={slot}, path={src}"
            if missing_policy == "error":
                raise FileNotFoundError(message)
            if missing_policy != "ignore":
                log(f"Warning: {message}", quiet)
            continue

        out_path = os.path.join(work_dir, f"page_{page_num:03d}_{slot}_{idx:02d}.mp4").replace('\\', '/')
        elapsed = normalize_insert_video(
            src.replace('\\', '/'), out_path, ffmpeg_path, ffprobe_path,
            width, height, fps, audio_rate, audio_channels, quiet,
        )
        duration = get_media_duration(out_path, ffprobe_path)
        f_list.write(f"file '{quote_concat_path(out_path)}'\n")
        added_duration += duration
        stats.setdefault("insert_videos", []).append(
            {
                "page": page_num,
                "slot": slot,
                "index": idx,
                "source": source_kind,
                "input": src,
                "output": out_path,
                "duration": duration,
                "elapsed": elapsed,
                "start": current_total_seconds + added_duration - duration,
                "end": current_total_seconds + added_duration,
            }
        )
        log(f"Inserted video: page={page_num}, slot={slot}, duration={duration:.2f}s, file={src}", quiet)
    return added_duration


def build_credit_image(credit_text: str, output_path: str, size=(1920, 1080)):
    width, height = size
    img = Image.new('RGB', (width, height), color=(0, 0, 0))
    draw = ImageDraw.Draw(img)
    font_list = [
        "msgothic.ttc", "MS Gothic",
        "NotoSansCJK-Regular.ttc", "Noto Sans CJK JP",
        "ipag.ttc", "IPAゴシック",
        "DejaVuSans.ttf"
    ]
    font = None
    font_size = max(24, int(height * 0.055))
    for f_name in font_list:
        try:
            font = ImageFont.truetype(f_name, font_size)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), credit_text, font=font)
    x = (width - (bbox[2] - bbox[0])) / 2
    y = (height - (bbox[3] - bbox[1])) / 2
    draw.text((x, y), credit_text, fill=(255, 255, 255), font=font)
    img.save(output_path)


def make_credit_video(credit_img: str, output_path: str, ffmpeg_path: str, duration: float, width: int, height: int, fps: int, audio_rate: int, audio_channels: int, quiet: bool):
    cmd = [
        ffmpeg_path, "-y",
        "-loop", "1",
        "-framerate", str(fps),
        "-i", credit_img,
        "-f", "lavfi",
        "-i", f"anullsrc=r={audio_rate}:cl=stereo",
        "-t", f"{duration:.6f}",
        "-vf", still_image_filter(width, height, fps),
        "-c:v", "libx264",
        "-r", str(fps),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-ar", str(audio_rate),
        "-ac", str(audio_channels),
        "-b:a", DEFAULT_AUDIO_BITRATE,
        "-movflags", "+faststart",
        output_path,
    ]
    return run_ffmpeg(cmd, quiet, label="credit-video")


def final_concat(concat_list_path: str, final_output: str, ffmpeg_path: str, fps: int, audio_rate: int, audio_channels: int, quiet: bool, copy_mode: bool):
    """最終連結。既定は再エンコードで、タイムスタンプと音声食い込みを安定させる。"""
    if copy_mode:
        cmd = [
            ffmpeg_path, "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_list_path.replace('\\', '/'),
            "-c", "copy",
            final_output,
        ]
    else:
        cmd = [
            ffmpeg_path, "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_list_path.replace('\\', '/'),
            "-c:v", "libx264",
            "-r", str(fps),
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-ar", str(audio_rate),
            "-ac", str(audio_channels),
            "-b:a", DEFAULT_AUDIO_BITRATE,
            "-movflags", "+faststart",
            final_output,
        ]
    return run_ffmpeg(cmd, quiet, label="final-concat")


def cleanup_work_dir(work_dir: str, keep_work: bool, quiet: bool = False):
    if keep_work:
        log(f"workディレクトリを保持します: {work_dir}", quiet)
        return
    concat_list = Path(work_dir) / "concat_list.txt"
    if concat_list.exists():
        log(f"workディレクトリを保持しない設定ですが、デバッグしやすいよう中間ファイルは残します: {work_dir}", quiet)


def main():
    args = parse_args()
    config = load_config()
    video_cfg = config.get("video", {})
    perf_cfg = config.get("performance", {})
    insert_cfg = config.get("video_insertion", {})

    base_name = args.base_name
    ffmpeg_path = args.ffmpeg_path or video_cfg.get("ffmpeg_path", "ffmpeg")
    ffprobe_path = args.ffprobe_path or video_cfg.get("ffprobe_path") or guess_ffprobe_path(ffmpeg_path)
    silence_duration = args.silence_duration if args.silence_duration is not None else video_cfg.get("silence_duration", 0.8)
    credit_duration = args.credit_duration if args.credit_duration is not None else video_cfg.get("credit_duration", 5.0)
    insert_enabled = insert_cfg.get("enabled", True)
    output_fps = int(insert_cfg.get("fps", video_cfg.get("fps", DEFAULT_FPS)))
    audio_rate = int(video_cfg.get("audio_rate", DEFAULT_AUDIO_RATE))
    audio_channels = int(video_cfg.get("audio_channels", DEFAULT_AUDIO_CHANNELS))
    missing_policy = insert_cfg.get("missing_file", "warn")
    final_copy_mode = bool(video_cfg.get("final_concat_copy", False))

    timing_file = args.timing_file
    notes_file = args.notes_file
    slide_dir = os.path.abspath(args.slide_dir)
    audio_dir = os.path.abspath(args.audio_dir)
    work_dir = os.path.abspath(args.work_dir)
    output_dir = os.path.abspath(args.output_dir)
    final_output = os.path.abspath(os.path.join(output_dir, f"{base_name}.mp4"))
    srt_path = os.path.abspath(os.path.join(output_dir, f"{base_name}.srt"))
    vtt_path = srt_path.replace('.srt', '.vtt')
    stats_file = os.path.abspath(args.stats_file)

    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(os.path.dirname(stats_file), exist_ok=True)

    if not os.path.exists(timing_file):
        print(f"Error: timing file not found: {timing_file}")
        sys.exit(1)

    with open(timing_file, "r", encoding="utf-8-sig") as f:
        all_timings = json.load(f)

    video_plan = load_notes_video_plan(notes_file) if insert_enabled else {}

    srt_lines = []
    srt_counter = 1
    total_elapsed_seconds = 0.0
    concat_list_path = os.path.join(work_dir, "concat_list.txt")
    overall_started = time.perf_counter()
    stats = {
        "base_name": base_name,
        "timing_file": timing_file,
        "notes_file": notes_file,
        "slide_count": 0,
        "audio_part_count": 0,
        "insert_video_count": 0,
        "silence_duration": silence_duration,
        "credit_duration": credit_duration,
        "pages": [],
        "insert_videos": [],
        "timings": {},
        "settings": {
            "ffmpeg_path": ffmpeg_path,
            "ffprobe_path": ffprobe_path,
            "fps": output_fps,
            "audio_rate": audio_rate,
            "audio_channels": audio_channels,
            "final_concat_copy": final_copy_mode,
            "keep_work": args.keep_work,
            "quiet": args.quiet,
            "video_perf_config": perf_cfg,
            "video_insertion_config": insert_cfg,
        },
    }

    log(f"--- Step04: Final Assembly Start ({base_name}) ---", args.quiet)
    log(
        f"設定: silence={silence_duration}s, credit={credit_duration}s, fps={output_fps}, audio={audio_rate}Hz/{audio_channels}ch, final_copy={final_copy_mode}",
        args.quiet,
    )

    try:
        first_slide_size = None
        with open(concat_list_path, "w", encoding="utf-8") as f_list:
            for page_num_str in sorted(all_timings.keys(), key=int):
                page_started = time.perf_counter()
                page_num = int(page_num_str)
                parts = all_timings[page_num_str]
                slide_img = os.path.join(slide_dir, f"slide_{page_num:03d}.png").replace('\\', '/')
                temp_audio = os.path.join(work_dir, f"page_{page_num:03d}.wav").replace('\\', '/')
                page_video = os.path.join(work_dir, f"page_{page_num:03d}.mp4").replace('\\', '/')

                if not os.path.exists(slide_img):
                    log(f"Warning: slide image not found, skip page {page_num}: {slide_img}", args.quiet)
                    continue

                slide_width, slide_height = get_image_size(slide_img)
                slide_width = max(2, (slide_width // 2) * 2)
                slide_height = max(2, (slide_height // 2) * 2)
                if first_slide_size is None:
                    first_slide_size = (slide_width, slide_height)

                plan = video_plan.get(page_num, {"before": [], "after": [], "embedded": []})
                before_items = plan.get("before", [])
                after_items = plan.get("after", []) + plan.get("embedded", [])

                before_duration = append_insert_videos(
                    f_list, before_items, page_num, "before", work_dir, ffmpeg_path, ffprobe_path,
                    slide_width, slide_height, output_fps, audio_rate, audio_channels,
                    total_elapsed_seconds, stats, args.quiet, missing_policy,
                )
                total_elapsed_seconds += before_duration

                audio_inputs = []
                current_page_duration = 0.0

                for i, part in enumerate(parts):
                    audio_file = os.path.join(audio_dir, part['file']).replace('\\', '/')
                    if not os.path.exists(audio_file):
                        raise FileNotFoundError(f"音声ファイルが見つかりません: {audio_file}")
                    audio_inputs.append(audio_file)
                    part_start = total_elapsed_seconds + current_page_duration + silence_duration
                    part_end = part_start + part['duration']
                    srt_lines.append(
                        f"{srt_counter}\n{format_srt_time(part_start)} --> {format_srt_time(part_end)}\n{part['text']}\n"
                    )
                    current_page_duration += (silence_duration + part['duration'])
                    srt_counter += 1
                    stats["audio_part_count"] += 1

                if not audio_inputs:
                    log(f"Warning: page {page_num} に音声パートがありません。スライド動画は作らず、after動画のみ処理します。", args.quiet)
                    after_duration = append_insert_videos(
                        f_list, after_items, page_num, "after", work_dir, ffmpeg_path, ffprobe_path,
                        slide_width, slide_height, output_fps, audio_rate, audio_channels,
                        total_elapsed_seconds, stats, args.quiet, missing_policy,
                    )
                    total_elapsed_seconds += after_duration
                    continue

                audio_elapsed = make_page_audio(
                    audio_inputs, len(parts), temp_audio, ffmpeg_path,
                    silence_duration, audio_rate, audio_channels, args.quiet,
                    label=f"page{page_num:03d}-audio",
                )

                video_elapsed = make_page_video(
                    slide_img, temp_audio, page_video, ffmpeg_path, current_page_duration,
                    slide_width, slide_height, output_fps, audio_rate, audio_channels,
                    args.quiet, label=f"page{page_num:03d}-video",
                )
                actual_page_duration = get_media_duration(page_video, ffprobe_path)
                f_list.write(f"file '{quote_concat_path(page_video)}'\n")
                total_elapsed_seconds += actual_page_duration

                after_duration = append_insert_videos(
                    f_list, after_items, page_num, "after", work_dir, ffmpeg_path, ffprobe_path,
                    slide_width, slide_height, output_fps, audio_rate, audio_channels,
                    total_elapsed_seconds, stats, args.quiet, missing_policy,
                )
                total_elapsed_seconds += after_duration

                page_elapsed = time.perf_counter() - page_started
                stats["slide_count"] += 1
                stats["pages"].append(
                    {
                        "page": page_num,
                        "parts": len(parts),
                        "planned_duration": current_page_duration,
                        "actual_page_duration": actual_page_duration,
                        "insert_before_duration": before_duration,
                        "insert_after_duration": after_duration,
                        "audio_elapsed": audio_elapsed,
                        "video_elapsed": video_elapsed,
                        "elapsed": page_elapsed,
                        "page_video": page_video,
                    }
                )
                log(
                    f"Processed Page {page_num}: parts={len(parts)}, slide_duration={actual_page_duration:.2f}s, insert={before_duration + after_duration:.2f}s, elapsed={page_elapsed:.2f}s",
                    args.quiet,
                )

            stats["insert_video_count"] = len(stats.get("insert_videos", []))

            actual_credit_img = os.path.join(work_dir, "generated_credit.png")
            used_ids = sorted({p.get("speaker_id") for page in all_timings.values() for p in page if p.get("speaker_id") is not None})
            names = [SPEAKER_MAP.get(sid, f"Unknown({sid})") for sid in used_ids]
            credit_text = f"読み上げ：VOICEVOX {'、'.join(names)}" if names else "読み上げ：VOICEVOX"

            asset_credit = PROJECT_ROOT / "assets" / "credit.png"
            target_size = first_slide_size or (1920, 1080)
            if asset_credit.exists():
                actual_credit_img = os.path.join(work_dir, "credit_resized.png")
                with Image.open(asset_credit) as img:
                    img.convert("RGB").resize(target_size).save(actual_credit_img)
            else:
                build_credit_image(credit_text, actual_credit_img, target_size)

            credit_video = os.path.join(work_dir, "credit_page.mp4").replace('\\', '/')
            credit_elapsed = make_credit_video(
                actual_credit_img, credit_video, ffmpeg_path, credit_duration,
                target_size[0], target_size[1], output_fps, audio_rate, audio_channels, args.quiet,
            )

            f_list.write(f"file '{quote_concat_path(credit_video)}'\n")
            srt_lines.append(
                f"{srt_counter}\n{format_srt_time(total_elapsed_seconds)} --> {format_srt_time(total_elapsed_seconds + credit_duration)}\n{credit_text}\n"
            )
            stats["timings"]["credit_elapsed"] = credit_elapsed

        srt_content = "\n".join(srt_lines)
        with open(srt_path, "w", encoding="utf-8-sig") as f:
            f.write(srt_content)
        save_vtt(srt_content, vtt_path)

        final_concat_elapsed = final_concat(
            concat_list_path, final_output, ffmpeg_path, output_fps, audio_rate, audio_channels, args.quiet, final_copy_mode,
        )

        overall_elapsed = time.perf_counter() - overall_started
        stats["timings"].update(
            {
                "final_concat_elapsed": final_concat_elapsed,
                "overall_elapsed": overall_elapsed,
                "output_video_duration_estimated": total_elapsed_seconds + credit_duration,
            }
        )
        with open(stats_file, "w", encoding="utf-8-sig") as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)

        log(f"SUCCESS!\nVideo: {final_output}", args.quiet)
        log(f"Subtitle: {srt_path}", args.quiet)
        log(f"VTT: {vtt_path}", args.quiet)
        log(
            f"統計: slides={stats['slide_count']}, audio_parts={stats['audio_part_count']}, insert_videos={stats['insert_video_count']}, total_elapsed={overall_elapsed:.2f}s, estimated_video={total_elapsed_seconds + credit_duration:.2f}s",
            args.quiet,
        )
        log(f"merge統計JSON: {stats_file}", args.quiet)

        cleanup_work_dir(work_dir, args.keep_work, args.quiet)

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
