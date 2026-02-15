import os
import json
import sys

# プロジェクトルートをパスに追加
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.extractor import get_processor

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

def main():
    if len(sys.argv) < 2:
        print("使用法: python 01_extract_notes.py [PPTXパス]")
        sys.exit(1)

    pptx_path = sys.argv[1]
    
    # パス設定
    dict_path = "dict/custom_dict.json"
    output_json = "temp/notes.json"
    check_file = "temp/check_notes.txt"

    print(f"--- ステップ01: ノート抽出開始 ({os.path.basename(pptx_path)}) ---")

    try:
        processor = get_processor()
        raw_notes = processor.extract_notes(pptx_path)
        dictionary = load_dictionary(dict_path)

        formatted_notes = []
        
        with open(check_file, "w", encoding="utf-8-sig") as f_check:
            f_check.write("# 抽出されたノートです。字幕は漢字、読上はひらがな等で管理されます。\n")
            f_check.write("# 必要に応じて「読上:」の行を修正してください。\n\n")

            for i, raw_text in enumerate(raw_notes):
                page_num = i + 1
                original_text = raw_text.strip()
                # 辞書適用（読み上げ用）
                reading_text = apply_dictionary(raw_text, dictionary).strip()
                
                # 新しいデータ構造: text(字幕用) と reading(読み上げ用) を分ける
                formatted_notes.append({
                    "slide_number": page_num,
                    "text": original_text,
                    "reading": reading_text
                })
                
                # 確認用ファイルの書き出し（対比しやすい形式）
                f_check.write(f"--- PAGE_{page_num:03d} ---\n")
                f_check.write(f"字幕: {original_text}\n")
                f_check.write(f"読上: {reading_text}\n\n")

        # JSON保存
        os.makedirs("temp", exist_ok=True)
        with open(output_json, "w", encoding="utf-8") as f_json:
            json.dump(formatted_notes, f_json, ensure_ascii=False, indent=4)

        print(f"成功: {len(formatted_notes)} 枚のノートを処理しました。")
        print(f"中間データ: {output_json}")
        print(f"確認用ファイル: {check_file}")

    except Exception as e:
        print(f"エラー: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
    