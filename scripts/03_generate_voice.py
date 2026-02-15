import os
import json
import sys
import re

# プロジェクトルートをパスに追加
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.voice_engine import VoicevoxGenerator

def split_parts(text):
    """「//」または「／／」でテキストを分割する"""
    return [p.strip() for p in re.split(r'／／|//', text) if p.strip()]

def main():
    DEFAULT_SPEAKER_ID = 21   # 麒ヶ島宗麟
    
    # パス設定
    notes_json = "temp/notes.json"
    audio_dir = "temp/audio"
    timing_file = "temp/timings.json"
    
    os.makedirs(audio_dir, exist_ok=True)

    if not os.path.exists(notes_json):
        print(f"Error: {notes_json} が見つかりません。Step01を先に実行してください。")
        sys.exit(1)

    # データの読み込み
    with open(notes_json, "r", encoding="utf-8") as f:
        notes_data = json.load(f)

    generator = VoicevoxGenerator() 
    all_timings = {}

    print("--- ステップ03: 音声合成開始 (字幕/読上の分離対応) ---")
    
    try:
        for entry in notes_data:
            page_num = entry["slide_number"]
            # 字幕用(漢字)と読上用(ひらがな補正)をそれぞれ分割
            subtitle_parts = split_parts(entry["text"])
            reading_parts = split_parts(entry["reading"])
            
            # 分割数が合わない場合の警告
            if len(subtitle_parts) != len(reading_parts):
                print(f"Warning: Page {page_num} の字幕と読上の分割数が一致しません。")

            print(f"Processing Slide {page_num} ({len(reading_parts)} parts)...")
            page_timings = []
            
            for i in range(len(reading_parts)):
                # 音声生成には reading (補正後) を使用
                part_reading = reading_parts[i]
                # 字幕には text (漢字) を使用（読上パーツ数に合わせて取得）
                part_subtitle = subtitle_parts[i] if i < len(subtitle_parts) else ""
                
                file_name = f"slide_{page_num:03d}_{i:02d}.wav"
                file_path = os.path.join(audio_dir, file_name)
                
                # VOICEVOX実行
                duration = generator.generate_audio(part_reading, file_path, DEFAULT_SPEAKER_ID)
                
                # タイミング情報には「漢字の字幕」を記録
                page_timings.append({
                    "part": i,
                    "text": part_subtitle, # ここが漢字になる
                    "file": file_name,
                    "duration": duration,
                    "speaker_id": DEFAULT_SPEAKER_ID
                })
                
            all_timings[page_num] = page_timings

        # 保存
        with open(timing_file, "w", encoding="utf-8-sig") as f:
            json.dump(all_timings, f, indent=4, ensure_ascii=False)

        print(f"成功: 音声ファイルを {audio_dir} に保存しました。")
        print(f"タイミング情報を {timing_file} に保存しました。")

    except Exception as e:
        print(f"エラーが発生しました: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
    