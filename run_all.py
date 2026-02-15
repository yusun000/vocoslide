import os
import shutil
import subprocess
import json
import requests
import sys
from pathlib import Path

# --- 設定項目 ---
CONFIG_FILE = "config.json"
INPUT_DIR = "input"
TEMP_DIR = "temp"
OUTPUT_DIR = "output"

def print_separator():
    print("-" * 50)

def check_environment():
    """実行環境の診断を行い、問題があれば日本語で解説を表示する"""
    print("【環境診断中...】")
    
    # 1. VOICEVOXの接続チェック
    try:
        # configからポートを取得（デフォルト50021）
        port = 50021
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                port = json.load(f).get("voicevox", {}).get("port", 50021)
        
        res = requests.get(f"http://127.0.0.1:{port}/speakers", timeout=3)
        if res.status_code != 200:
            raise Exception
    except:
        print("\n[!] エラー: VOICEVOXに接続できません。")
        print("    解説: VOICEVOXアプリが起動していないか、ポート番号が異なります。")
        print("    対策: VOICEVOXを起動してから再実行してください。")
        return False

    # 2. ffmpegのチェック
    if shutil.which("ffmpeg") is None:
        print("\n[!] エラー: ffmpegが見つかりません。")
        print("    解説: 動画合成に必要なffmpegがシステムにインストールされていません。")
        print("    対策: ffmpegをインストールし、環境変数(PATH)を通してください。")
        return False

    # 3. Poppler (pdf2image) のチェック
    # pdftoppmはPopplerに同梱されている主要コマンド
    if shutil.which("pdftoppm") is None:
        print("\n[!] エラー: Poppler(pdftoppm)が見つかりません。")
        print("    解説: PDFを画像化するためのツール一式が不足しています。")
        print("    対策: Popplerをインストールし、binフォルダにPATHを通してください。")
        return False

    print("--- 環境診断: 全てクリア ---")
    return True

def cleanup_temp():
    """一時フォルダを完全にクリアする"""
    if os.path.exists(TEMP_DIR):
        print(f"清掃中: {TEMP_DIR} 内の古いデータを削除しています...")
        shutil.rmtree(TEMP_DIR)
    os.makedirs(TEMP_DIR, exist_ok=True)
    # 必要なサブフォルダも再作成
    os.makedirs(os.path.join(TEMP_DIR, "slides"), exist_ok=True)
    os.makedirs(os.path.join(TEMP_DIR, "audio"), exist_ok=True)

def run_step(script_name, args=None):
    """各ステップのスクリプトを実行する"""
    cmd = [sys.executable, f"scripts/{script_name}"]
    if args:
        cmd.extend(args)
    
    print(f"\n実行中: {script_name}...")
    try:
        result = subprocess.run(cmd, check=True)
        return True
    except subprocess.CalledProcessError:
        print(f"\n[!] エラー: {script_name} の実行中に問題が発生しました。")
        print(f"    解説: スクリプト内部でエラーが発生しました。ログを確認してください。")
        return False

def main():
    # 1. 環境診断
    if not check_environment():
        sys.exit(1)

    # 2. inputフォルダ内のファイル確認
    pptx_files = list(Path(INPUT_DIR).glob("*.pptx"))
    if not pptx_files:
        print(f"\n[!] エラー: {INPUT_DIR} フォルダに .pptx ファイルがありません。")
        sys.exit(1)

    # 3. 複数ファイルのループ処理
    for pptx_path in pptx_files:
        base_name = pptx_path.stem
        pdf_path = pptx_path.with_suffix(".pdf")

        print_separator()
        print(f"処理開始: {base_name}")
        print_separator()

        # PDFの存在確認
        if not pdf_path.exists():
            print(f"警告: {base_name}.pdf が見つかりません。スキップします。")
            continue

        # 一時フォルダの掃除（ファイルごとにリセット）
        cleanup_temp()

        # 各ステップの実行
        # 引数としてベース名を渡し、各スクリプトが何の設定を読むべきか伝えます
        steps = [
            ("01_extract_notes.py", [str(pptx_path)]),
            ("02_pdf_to_png.py", [str(pdf_path)]),
            ("03_generate_voice.py", []),
            ("04_merge_video.py", [base_name])
        ]

        success = True
        for script, args in steps:
            if not run_step(script, args):
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
    