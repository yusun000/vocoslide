import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import requests

CONFIG_FILE = "config.json"
INPUT_DIR = "input"
TEMP_DIR = "temp"
OUTPUT_DIR = "output"


def parse_args():
    parser = argparse.ArgumentParser(description="vocoslide 全処理実行")
    parser.add_argument("--max-workers", type=int, default=None, help="音声生成の並列数")
    parser.add_argument("--no-disk-cache", action="store_true", help="ディスクキャッシュを無効化")
    parser.add_argument("--no-dedup", action="store_true", help="同一動画内重複再利用を無効化")
    parser.add_argument("--clear-cache", action="store_true", help="各ファイル実行前にキャッシュ削除")
    parser.add_argument("--keep-temp", action="store_true", help="temp を掃除せず残す")
    parser.add_argument("--quiet", action="store_true", help="各ステップの詳細ログを抑制")
    return parser.parse_args()


def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def print_separator():
    print("-" * 50)


def check_environment():
    print("【環境診断中...】")
    try:
        port = 50021
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                port = json.load(f).get("voicevox", {}).get("port", 50021)

        res = requests.get(f"http://127.0.0.1:{port}/speakers", timeout=3, proxies={"http": None, "https": None})
        if res.status_code != 200:
            raise Exception
    except Exception:
        print("\n[!] エラー: VOICEVOXに接続できません。")
        print("    解説: VOICEVOXアプリが起動していないか、ポート番号が異なります。")
        print("    対策: VOICEVOXを起動してから再実行してください。")
        return False

    if shutil.which("ffmpeg") is None:
        print("\n[!] エラー: ffmpegが見つかりません。")
        print("    解説: 動画合成に必要なffmpegがシステムにインストールされていません。")
        print("    対策: ffmpegをインストールし、環境変数(PATH)を通してください。")
        return False

    if shutil.which("pdftoppm") is None:
        print("\n[!] エラー: Poppler(pdftoppm)が見つかりません。")
        print("    解説: PDFを画像化するためのツール一式が不足しています。")
        print("    対策: Popplerをインストールし、binフォルダにPATHを通してください。")
        return False

    print("--- 環境診断: 全てクリア ---")
    return True


def cleanup_temp(keep_temp: bool = False):
    if keep_temp:
        os.makedirs(TEMP_DIR, exist_ok=True)
        os.makedirs(os.path.join(TEMP_DIR, "slides"), exist_ok=True)
        os.makedirs(os.path.join(TEMP_DIR, "audio"), exist_ok=True)
        os.makedirs(os.path.join(TEMP_DIR, "audio_cache"), exist_ok=True)
        return
    if os.path.exists(TEMP_DIR):
        print(f"清掃中: {TEMP_DIR} 内の古いデータを削除しています...")
        shutil.rmtree(TEMP_DIR)
    os.makedirs(TEMP_DIR, exist_ok=True)
    os.makedirs(os.path.join(TEMP_DIR, "slides"), exist_ok=True)
    os.makedirs(os.path.join(TEMP_DIR, "audio"), exist_ok=True)
    os.makedirs(os.path.join(TEMP_DIR, "audio_cache"), exist_ok=True)


def run_step(script_name, args=None):
    cmd = [sys.executable, f"scripts/{script_name}"]
    if args:
        cmd.extend(args)

    print(f"\n実行中: {script_name}...")
    try:
        subprocess.run(cmd, check=True)
        return True
    except subprocess.CalledProcessError:
        print(f"\n[!] エラー: {script_name} の実行中に問題が発生しました。")
        print("    解説: スクリプト内部でエラーが発生しました。ログを確認してください。")
        return False


def main():
    args = parse_args()
    config = load_config()
    perf_cfg = config.get("performance", {})
    max_workers = args.max_workers or perf_cfg.get("voice_max_workers")

    if not check_environment():
        sys.exit(1)

    pptx_files = list(Path(INPUT_DIR).glob("*.pptx"))
    if not pptx_files:
        print(f"\n[!] エラー: {INPUT_DIR} フォルダに .pptx ファイルがありません。")
        sys.exit(1)

    for pptx_path in pptx_files:
        base_name = pptx_path.stem
        pdf_path = pptx_path.with_suffix(".pdf")

        print_separator()
        print(f"処理開始: {base_name}")
        print_separator()

        if not pdf_path.exists():
            print(f"警告: {base_name}.pdf が見つかりません。スキップします。")
            continue

        cleanup_temp(keep_temp=args.keep_temp)

        step03_args = []
        if max_workers:
            step03_args += ["--max-workers", str(max_workers)]
        if args.no_disk_cache:
            step03_args.append("--no-disk-cache")
        if args.no_dedup:
            step03_args.append("--no-dedup")
        if args.clear_cache:
            step03_args.append("--clear-cache")

        step04_args = [
            base_name,
            "--stats-file", os.path.join(TEMP_DIR, f"merge_stats_{base_name}.json"),
        ]
        if args.keep_temp:
            step04_args.append("--keep-work")
        if args.quiet:
            step03_args.append("--quiet")
            step04_args.append("--quiet")

        steps = [
            ("01_extract_notes.py", [str(pptx_path)]),
            ("02_pdf_to_png.py", [str(pdf_path)]),
            ("03_generate_voice.py", step03_args),
            ("04_merge_video.py", step04_args),
        ]

        success = True
        for script, script_args in steps:
            if not run_step(script, script_args):
                success = False
                break

        if success:
            print(f"\n完了: {base_name} の動画を output/ に生成しました。")
        else:
            print(f"\n中断: {base_name} の処理に失敗しました。")

    print_separator()
    print("全プロセスの実行が終了しました。")


if __name__ == "__main__":
    main()
