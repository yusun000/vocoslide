import argparse
import json
import os
import re
import sys
from pathlib import Path

# scripts/ からの実行でも utils/ を読めるようにする
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 互換性のため両方試す
try:
    from utils.voice_engine import Segment, VoiceParams, VoicevoxGenerator
except Exception:
    from voice_engine import Segment, VoiceParams, VoicevoxGenerator


def split_parts(text):
    """「//」または「／／」でテキストを分割する"""
    return [p.strip() for p in re.split(r'／／|//', text) if p.strip()]


def load_config(config_path: str = "config.json"):
    if not os.path.exists(config_path):
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def parse_args():
    parser = argparse.ArgumentParser(description="VOICEVOX音声生成")
    parser.add_argument("--notes-json", default="temp/notes.json")
    parser.add_argument("--audio-dir", default="temp/audio")
    parser.add_argument("--timing-file", default="temp/timings.json")
    parser.add_argument("--speaker-id", type=int, default=None)
    parser.add_argument("--voicevox-host", default=None)
    parser.add_argument("--voicevox-port", type=int, default=None)
    parser.add_argument("--max-workers", type=int, default=None)
    parser.add_argument("--cache-dir", default="temp/audio_cache")
    parser.add_argument("--no-disk-cache", action="store_true")
    parser.add_argument("--no-dedup", action="store_true")
    parser.add_argument("--clear-cache", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    config = load_config()
    voicevox_cfg = config.get("voicevox", {})
    perf_cfg = config.get("performance", {})

    default_speaker_id = args.speaker_id or voicevox_cfg.get("speaker_id", 21)
    host = args.voicevox_host or voicevox_cfg.get("host", "localhost")
    port = args.voicevox_port or voicevox_cfg.get("port", 50021)
    max_workers = args.max_workers or perf_cfg.get("voice_max_workers", 1)

    notes_json = args.notes_json
    audio_dir = args.audio_dir
    timing_file = args.timing_file

    os.makedirs(audio_dir, exist_ok=True)
    Path(args.cache_dir).mkdir(parents=True, exist_ok=True)

    if not os.path.exists(notes_json):
        print(f"Error: {notes_json} が見つかりません。Step01を先に実行してください。")
        sys.exit(1)

    with open(notes_json, "r", encoding="utf-8") as f:
        notes_data = json.load(f)

    generator = VoicevoxGenerator(
        host=host,
        port=port,
        max_workers=max_workers,
        enable_disk_cache=not args.no_disk_cache,
        enable_in_memory_dedup=not args.no_dedup,
        cache_dir=args.cache_dir,
        clear_cache_before_run=args.clear_cache,
        verbose=not args.quiet,
    )
    all_timings = {}

    print("--- ステップ03: 音声合成開始 (字幕/読上の分離対応) ---")
    print(
        f"設定: speaker={default_speaker_id}, workers={max_workers}, "
        f"disk_cache={'OFF' if args.no_disk_cache else 'ON'}, "
        f"dedup={'OFF' if args.no_dedup else 'ON'}"
    )

    total_stats = {"segments": 0, "unique_segments": 0, "cache_hit": 0, "reused_in_run": 0, "synthesized": 0, "elapsed": 0.0}

    try:
        for entry in notes_data:
            page_num = entry["slide_number"]
            subtitle_parts = split_parts(entry["text"])
            reading_parts = split_parts(entry["reading"])

            if len(subtitle_parts) != len(reading_parts):
                print(f"Warning: Page {page_num} の字幕と読上の分割数が一致しません。")

            print(f"Processing Slide {page_num} ({len(reading_parts)} parts)...")
            page_timings = []
            segments = []

            for i, part_reading in enumerate(reading_parts):
                part_subtitle = subtitle_parts[i] if i < len(subtitle_parts) else ""
                file_name = f"slide_{page_num:03d}_{i:02d}.wav"
                file_path = os.path.join(audio_dir, file_name)
                segments.append(
                    Segment(
                        text=part_reading,
                        output_path=file_path,
                        params=VoiceParams(speaker=default_speaker_id),
                        index=i,
                        subtitle=part_subtitle,
                    )
                )

            results = generator.generate_segments(segments)

            for i, result in enumerate(results):
                part_subtitle = segments[i].subtitle
                page_timings.append(
                    {
                        "part": i,
                        "text": part_subtitle,
                        "file": os.path.basename(result["path"]),
                        "duration": result["duration"],
                        "speaker_id": default_speaker_id,
                        "reused": result["reused"],
                        "cache_hit": result["cache_hit"],
                        "synthesized": result["synthesized"],
                    }
                )

            all_timings[page_num] = page_timings

            stats = generator.get_stats()
            for key in total_stats:
                total_stats[key] += stats.get(key, 0)

        with open(timing_file, "w", encoding="utf-8-sig") as f:
            json.dump(all_timings, f, indent=4, ensure_ascii=False)

        print(f"成功: 音声ファイルを {audio_dir} に保存しました。")
        print(f"タイミング情報を {timing_file} に保存しました。")
        print(
            "集計: "
            f"segments={total_stats.get('segments', 0)}, "
            f"unique={total_stats.get('unique_segments', 0)}, "
            f"cache_hit={total_stats.get('cache_hit', 0)}, "
            f"reused_in_run={total_stats.get('reused_in_run', 0)}, "
            f"synthesized={total_stats.get('synthesized', 0)}, "
            f"elapsed={total_stats.get('elapsed', 0.0):.2f}s"
        )

    except Exception as e:
        print(f"エラーが発生しました: {e}")
        raise


if __name__ == "__main__":
    main()
