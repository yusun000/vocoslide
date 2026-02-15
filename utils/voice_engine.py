import requests
import wave
import os
import re

class VoicevoxGenerator:
    def __init__(self, host="localhost", port=50021):
        self.base_url = f"http://{host}:{port}"

    def generate_audio(self, text, output_path, speaker_id):
        """VOICEVOXで音声を生成し、生成した音声の長さ（秒）を返す"""
        # 音声合成用クエリの作成
        query_res = requests.post(
            f"{self.base_url}/audio_query",
            params={"text": text, "speaker": speaker_id}
        )
        query_res.raise_for_status()
        query = query_res.json()

        # 音声合成の実行
        synth_res = requests.post(
            f"{self.base_url}/synthesis",
            params={"speaker": speaker_id},
            json=query
        )
        synth_res.raise_for_status()

        # ファイル保存
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(synth_res.content)

        # waveファイルを開いて長さを計測
        with wave.open(output_path, "rb") as wf:
            duration = wf.getnframes() / wf.getframerate()
        
        return duration

def parse_check_notes(file_path):
    """
    check_notes.txt 等のテキストファイルを解析する
    --- PAGE_001 --- という形式のセクションを検出し、
    さらに内容を「//」でパーツ分割して辞書で返す
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"原稿ファイルが見つかりません: {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # ページごとに分割 (正規表現で --- PAGE_001 --- 等をキャプチャ)
    pages = re.split(r'--- PAGE_(\d+) ---', content)
    page_data = {}
    
    # re.splitの結果、奇数インデックスにページ番号、偶数インデックスに本文が入る
    for i in range(1, len(pages), 2):
        page_num = int(pages[i])
        text = pages[i+1].strip()
        if text:
            # 「//」または全角の「／／」でパート分割
            parts = [p.strip() for p in re.split(r'／／|//', text) if p.strip()]
            page_data[page_num] = parts
            
    return page_data