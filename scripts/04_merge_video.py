import os
import json
import subprocess
import sys

def format_srt_time(seconds):
    """秒数をSRT形式のタイムコード (00:00:00,000) に変換"""
    ms = int((seconds % 1) * 1000)
    s = int(seconds % 60)
    m = int((seconds // 60) % 60)
    h = int(seconds // 3600)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

def main():
    if len(sys.argv) < 2:
        print("Usage: python 04_merge_video.py [BASE_NAME]")
        sys.exit(1)

    base_name = sys.argv[1]
    
    # パス設定
    FFMPEG_PATH = "ffmpeg"
    SILENCE_DURATION = 0.8 # 各パート前の「間」
    
    timing_file = "temp/timings.json"
    slide_dir = os.path.abspath("temp/slides")
    audio_dir = os.path.abspath("temp/audio")
    work_dir = os.path.abspath("temp/work")
    
    final_output = os.path.abspath(f"output/{base_name}.mp4")
    srt_path = os.path.abspath(f"output/{base_name}.srt")
    
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs("output", exist_ok=True)

    if not os.path.exists(timing_file):
        print(f"Error: {timing_file} not found.")
        sys.exit(1)

    with open(timing_file, "r", encoding="utf-8-sig") as f:
        all_timings = json.load(f)

    srt_lines = []
    srt_counter = 1
    total_elapsed_seconds = 0.0 
    concat_list_path = os.path.join(work_dir, "concat_list.txt")
    
    print(f"--- Step04: Final Assembly Start ({base_name}) ---")

    try:
        with open(concat_list_path, "w", encoding="utf-8") as f_list:
            for page_num_str in sorted(all_timings.keys(), key=int):
                page_num = int(page_num_str)
                parts = all_timings[page_num_str]
                
                # ffmpegでの誤認を防ぐためパスをスラッシュに統一
                slide_img = os.path.join(slide_dir, f"slide_{page_num:03d}.png").replace('\\', '/')
                temp_audio = os.path.join(work_dir, f"page_{page_num:03d}.wav").replace('\\', '/')
                page_video = os.path.join(work_dir, f"page_{page_num:03d}.mp4").replace('\\', '/')

                # --- 防衛策：画像が存在しない場合はスキップ ---
                if not os.path.exists(slide_img):
                    print(f"Warning: slide_{page_num:03d}.png not found. Skipping page {page_num}.")
                    continue 

                # --- 音声処理 ---
                audio_inputs = []
                audio_filter = ""
                current_page_duration = 0.0

                for i, part in enumerate(parts):
                    audio_inputs.append(os.path.join(audio_dir, part['file']).replace('\\', '/'))
                    audio_filter += f"[{i}:a]adelay={int(SILENCE_DURATION*1000)}|{int(SILENCE_DURATION*1000)}[a{i}];"
                    
                    # 漢字のテキスト（part['text']）で字幕タイミングを計算
                    part_start = total_elapsed_seconds + current_page_duration + SILENCE_DURATION
                    part_end = part_start + part['duration']
                    
                    srt_lines.append(f"{srt_counter}\n{format_srt_time(part_start)} --> {format_srt_time(part_end)}\n{part['text']}\n")
                    
                    current_page_duration += (SILENCE_DURATION + part['duration'])
                    srt_counter += 1

                audio_filter += "".join([f"[a{i}]" for i in range(len(parts))]) + f"concat=n={len(parts)}:v=0:a=1[aout]"
                cmd_audio = [FFMPEG_PATH, "-y"]
                for a_in in audio_inputs: cmd_audio.extend(["-i", a_in])
                cmd_audio.extend(["-filter_complex", audio_filter, "-map", "[aout]", temp_audio])
                subprocess.run(cmd_audio, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                # --- 動画生成 ---
                cmd_video = [
                    FFMPEG_PATH, "-y",
                    "-loop", "1", "-i", slide_img,
                    "-i", temp_audio,
                    "-t", str(current_page_duration),
                    "-c:v", "libx264", "-tune", "stillimage",
                    "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p",
                    "-c:a", "aac", "-b:a", "192k",
                    page_video
                ]
                subprocess.run(cmd_video, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                
                f_list.write(f"file '{page_video}'\n")
                total_elapsed_seconds += current_page_duration
                print(f"Processed Page {page_num}...")

        # --- 文字化け対策：BOM付きUTF-8で保存 ---
        with open(srt_path, "w", encoding="utf-8-sig") as f:
            f.write("\n".join(srt_lines))

        # 最終結合
        subprocess.run([
            FFMPEG_PATH, "-y", "-f", "concat", "-safe", "0",
            "-i", concat_list_path.replace('\\', '/'),
            "-c", "copy", final_output
        ], check=True)
        
        print(f"SUCCESS!\nVideo: {final_output}\nSubtitles: {srt_path}")

    except Exception as e:
        print(f"Error during video assembly: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
    