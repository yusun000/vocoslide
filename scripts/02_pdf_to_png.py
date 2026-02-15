import sys
import os

# プロジェクトルートをパスに追加
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.extractor import get_processor

def main():
    # run_all.py から PDFのパスを受け取る
    if len(sys.argv) < 2:
        print("使用法: python 02_pdf_to_png.py [PDFパス]")
        sys.exit(1)

    pdf_path = sys.argv[1]
    # 中間生成物の保存先（run_all.pyの仕様に合わせる）
    output_dir = "temp/slides"

    print(f"--- ステップ02: スライド画像化開始 ({os.path.basename(pdf_path)}) ---")

    try:
        # プロセッサの取得（WindowsでもOpenProcessor/pdf2imageを使うよう強制することも可能ですが、
        # get_processor() を使うことで環境に合わせた最適な書き出しを選択します）
        processor = get_processor()
        
        # 画像書き出し実行
        # pptx_pathは今回不要なので None、第3引数に pdf_path を渡す
        processor.export_slides(pptx_path=None, output_dir=output_dir, pdf_path=pdf_path)
        
        # 生成されたファイル数をカウント
        file_count = len([f for f in os.listdir(output_dir) if f.endswith(".png")])
        print(f"成功: {file_count} 枚のスライド画像を {output_dir} に保存しました。")

    except Exception as e:
        print(f"エラーが発生しました: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()