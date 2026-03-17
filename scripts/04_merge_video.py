import os
import json
import subprocess
import sys
from PIL import Image, ImageDraw, ImageFont

# キャラクターIDと表示名の対応表
SPEAKER_MAP = {
    21: "麒ヶ島宗麟",
    3: "ずんだもん",
    2: "四国めたん",
    8: "春日部つむぎ",
}

def format_srt_time(seconds):
    ms = int((seconds % 1) * 1000)
    s = int(seconds % 60)
    m = int((seconds // 60) % 60)
    h = int(seconds // 3600)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

def save_vtt(srt_content, vtt_path):
    """SRTの内容をWebVTT形式に変換して保存"""
    # 先頭に必須の文字列を追加
    vtt_text = "WEBVTT\n\n" + srt_content
    # ミリ秒の区切りをカンマからドットへ置換 (00:00:01,500 -> 00:00:01.500)
    vtt_text = vtt_text.replace(',', '.')
    
    with open(vtt_path, "w", encoding="utf-8-sig") as f:
        f.write(vtt_text)

def main():
    if len(sys.argv) < 2:
        print("Usage: python 04_merge_video.py [BASE_NAME]")
        sys.exit(1)

    base_name = sys.argv[1]
    FFMPEG_PATH = "ffmpeg"
    SILENCE_DURATION = 0.8
    
    timing_file = "temp/timings.json"
    slide_dir = os.path.abspath("temp/slides")
    audio_dir = os.path.abspath("temp/audio")
    work_dir = os.path.abspath("temp/work")
    final_output = os.path.abspath(f"output/{base_name}.mp4")
    srt_path = os.path.abspath(f"output/{base_name}.srt")
    
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs("output", exist_ok=True)

    with open(timing_file, "r", encoding="utf-8-sig") as f:
        all_timings = json.load(f)

    srt_lines = []
    srt_counter = 1
    total_elapsed_seconds = 0.0 
    concat_list_path = os.path.join(work_dir, "concat_list.txt")
    
    print(f"--- Step04: Final Assembly Start ({base_name}) ---")

    try:
        with open(concat_list_path, "w", encoding="utf-8") as f_list:
            # 1. 本編スライドの処理 (既存ロジック)
            for page_num_str in sorted(all_timings.keys(), key=int):
                page_num = int(page_num_str)
                parts = all_timings[page_num_str]
                slide_img = os.path.join(slide_dir, f"slide_{page_num:03d}.png").replace('\\', '/')
                temp_audio = os.path.join(work_dir, f"page_{page_num:03d}.wav").replace('\\', '/')
                page_video = os.path.join(work_dir, f"page_{page_num:03d}.mp4").replace('\\', '/')

                if not os.path.exists(slide_img): continue 

                audio_inputs = []
                audio_filter = ""
                current_page_duration = 0.0
                for i, part in enumerate(parts):
                    audio_inputs.append(os.path.join(audio_dir, part['file']).replace('\\', '/'))
                    audio_filter += f"[{i}:a]adelay={int(SILENCE_DURATION*1000)}|{int(SILENCE_DURATION*1000)}[a{i}];"
                    part_start = total_elapsed_seconds + current_page_duration + SILENCE_DURATION
                    part_end = part_start + part['duration']
                    srt_lines.append(f"{srt_counter}\n{format_srt_time(part_start)} --> {format_srt_time(part_end)}\n{part['text']}\n")
                    current_page_duration += (SILENCE_DURATION + part['duration'])
                    srt_counter += 1

                audio_filter += "".join([f"[a{i}]" for i in range(len(parts))]) + f"concat=n={len(parts)}:v=0:a=1[aout]"
                cmd_audio = [FFMPEG_PATH, "-y"]
                for a_in in audio_inputs: cmd_audio.extend(["-i", a_in])
                cmd_audio.extend(["-filter_complex", audio_filter, "-map", "[aout]", temp_audio])
                subprocess.run(cmd_audio, check=True, capture_output=True)

                cmd_video = [
                    FFMPEG_PATH, "-y", "-loop", "1", "-i", slide_img, "-i", temp_audio,
                    "-t", str(current_page_duration), "-c:v", "libx264", "-tune", "stillimage",
                    "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p", "-c:a", "aac", "-b:a", "192k",
                    page_video
                ]
                subprocess.run(cmd_video, check=True, capture_output=True)
                f_list.write(f"file '{page_video}'\n")
                total_elapsed_seconds += current_page_duration
                print(f"Processed Page {page_num}...")

            # 2. クレジットページの動的生成
            CREDIT_DURATION = 5.0
            actual_credit_img = os.path.join(work_dir, "generated_credit.png")
            
            used_ids = {p.get("speaker_id") for page in all_timings.values() for p in page}
            names = [SPEAKER_MAP.get(sid, f"Unknown({sid})") for sid in used_ids]
            credit_text = f"読み上げ：VOICEVOX {'、'.join(names)}"

            if os.path.exists("assets/credit.png"):
                actual_credit_img = os.path.abspath("assets/credit.png")
            else:
                # 画像自動生成 (Linux/Windows両対応フォント探索)
                img = Image.new('RGB', (1920, 1080), color=(0, 0, 0))
                draw = ImageDraw.Draw(img)
                font_list = [
                    "msgothic.ttc", "MS Gothic",               # Windows
                    "NotoSansCJK-Regular.ttc", "Noto Sans CJK JP", # Linux (Ubuntu等)
                    "ipag.ttc", "IPAゴシック",                  # Linux
                    "DejaVuSans.ttf"                          # Fallback
                ]
                font = None
                for f_name in font_list:
                    try:
                        font = ImageFont.truetype(f_name, 60)
                        break
                    except: continue
                if font is None: font = ImageFont.load_default()
                
                bbox = draw.textbbox((0, 0), credit_text, font=font)
                draw.text(((1920-(bbox[2]-bbox[0]))/2, (1080-(bbox[3]-bbox[1]))/2), credit_text, fill=(255, 255, 255), font=font)
                img.save(actual_credit_img)

            credit_video = os.path.join(work_dir, "credit_page.mp4").replace('\\', '/')
            subprocess.run([
                FFMPEG_PATH, "-y", "-loop", "1", "-i", actual_credit_img,
                "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", str(CREDIT_DURATION),
                "-c:v", "libx264", "-vf", "scale=1920:1080,format=yuv420p", "-c:a", "aac", credit_video
            ], check=True, capture_output=True)
            
            f_list.write(f"file '{credit_video}'\n")
            srt_lines.append(f"{srt_counter}\n{format_srt_time(total_elapsed_seconds)} --> {format_srt_time(total_elapsed_seconds + CREDIT_DURATION)}\n{credit_text}\n")

        # 3. 最終結合
        # 3. 最終結合
        srt_content = "\n".join(srt_lines)
        with open(srt_path, "w", encoding="utf-8-sig") as f: 
            f.write(srt_content)
    
        # VTTファイルの保存
        vtt_path = srt_path.replace('.srt', '.vtt')
        save_vtt(srt_content, vtt_path)

        subprocess.run([FFMPEG_PATH, "-y", "-f", "concat", "-safe", "0", "-i", concat_list_path.replace('\\', '/'), "-c", "copy", final_output], check=True)
        print(f"SUCCESS!\nVideo: {final_output}")

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()

