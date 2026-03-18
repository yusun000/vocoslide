import os
import json
import sys
import re

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
            # 改善ロジック：句読点（。、？！）を直前の文字とセットにして抽出
            # 例：「今日は、晴れですね。」 -> ['今日は、', '晴れですね。']
            chunks = re.findall(r'[^。、？！?!\s]+[。、？！?!\s]*', segment)
            
            current_line = ""
            for chunk in chunks:
                # この塊を足しても max_len 以内なら結合
                if len(current_line) + len(chunk) <= max_len:
                    current_line += chunk
                else:
                    # 超える場合は、現在の行を確定（空でなければ）
                    if current_line:
                        final_results.append(current_line.strip())
                    
                    # 新しい塊がそもそも max_len より長い場合は強制分割が必要だが、
                    # 基本は新しい行の開始とする
                    current_line = chunk
            
            if current_line:
                final_results.append(current_line.strip())

    return "／／".join(final_results)

def main():
    if len(sys.argv) < 2:
        print("使用法: python 01_extract_notes.py [PPTXパス]")
        sys.exit(1)

    pptx_path = sys.argv[1]
    
    # パス設定
    dict_path = "dict/custom_dict.json"
    output_json = "temp/notes.json"
    check_file = "temp/check_notes.txt"
    
    # --- 設定値 ---
    MAX_CHARS_PER_LINE = 35  # 1行あたりの最大文字数
    # --------------

    print(f"--- ステップ01: ノート抽出開始 ({os.path.basename(pptx_path)}) ---")

    try:
        processor = get_processor()
        raw_notes = processor.extract_notes(pptx_path)
        dictionary = load_dictionary(dict_path)

        formatted_notes = []
        os.makedirs("temp", exist_ok=True)

        with open(check_file, "w", encoding="utf-8-sig") as f_check:
            f_check.write("# 抽出されたノートです。'／／' は改行・分割位置を示します。\n")
            f_check.write("# 手動の // も反映済みです。必要に応じて修正してください。\n\n")

            for i, raw_text in enumerate(raw_notes):
                page_num = i + 1
                # 元の改行をスペースに置換（手動の // は維持される）
                clean_text = raw_text.strip().replace('\n', ' ')
                
                # 1. 辞書適用（読み上げ用）
                # reading_base = apply_dictionary(clean_text, dictionary)
                
                # 2. 手動分割優先 ＋ 自動分割
                final_subtitle = split_long_text(clean_text, max_len=MAX_CHARS_PER_LINE)
                final_reading = apply_dictionary(final_subtitle, dictionary)
                
                formatted_notes.append({
                    "slide_number": page_num,
                    "text": final_subtitle,
                    "reading": final_reading
                })
                
                f_check.write(f"--- PAGE_{page_num:03d} ---\n")
                f_check.write(f"字幕: {final_subtitle}\n")
                f_check.write(f"読上: {final_reading}\n\n")

        # JSON保存
        with open(output_json, "w", encoding="utf-8") as f_json:
            json.dump(formatted_notes, f_json, ensure_ascii=False, indent=4)

        print(f"成功: {len(formatted_notes)} 枚のノートを処理しました。")
        print(f"設定: 最大 {MAX_CHARS_PER_LINE} 文字 (手動分割優先)")

    except Exception as e:
        print(f"エラー: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
    