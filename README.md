# VocoSlide: Automated "Kamishibai" Video Generator

An automated pipeline to generate high-quality subtitled videos from PowerPoint (PPTX) notes using VOICEVOX (Neural Text-to-Speech) and ffmpeg.


## 1. English Summary
While this tool is optimized for Japanese content creators (due to its integration with Japanese TTS), the core logic provides a robust way to:
1. **Extract Narrative**: Pulls notes from PPTX as a screenplay.
2. **Visual Export**: Convert pre-exported slide PDFs into high-resolution PNG images.
3. **Phonetic/Subtitle Separation**: Manages different text layers for audio synthesis (reading) and visual display (subtitles) to ensure natural pronunciation without compromising subtitle readability.
4. **Automated Assembly**: Syncs visuals and audio into a final MP4 with SRT subtitles using ffmpeg.


## 2. システム概要

本システムは、PowerPoint（PPTX）のノート欄を原稿として読み込み、AI音声（VOICEVOX）とスライド画像を組み合わせて「紙芝居動画」を全自動で生成するシステムです。
PDFから高品質なスライド画像を生成、それらを統合して字幕付きの動画（MP4）を出力します。
※PDFはOfficeで別途出力したものを使用



## 3. ディレクトリ構成

```text
.
├── run_all.py              # 全行程の自動実行・環境診断（司令塔）
├── scripts/                # メイン工程スクリプト群
│   ├── 01_extract_notes.py # ノート抽出・辞書適用
│   ├── 02_pdf_to_png.py    # PDFから画像(PNG)を生成
│   ├── 03_generate_voice.py# 音声合成(WAV)とタイミング記録
│   └── 04_merge_video.py   # ffmpegによる動画・字幕の結合
├── utils/                  # 共通ユーティリティ
│   ├── extractor.py        # PPTX/PDF操作クラス
│   └── voice_engine.py     # VOICEVOX操作・原稿解析
├── dict/
│   └── custom_dict.json    # 読み上げ補正用ユーザー辞書
├── input/                  # 【配置】変換元のPPTXとPDFを置く場所
├── temp/                   # 【自動生成】中間ファイル（実行のたびに削除）
└── output/                 # 【自動生成】最終成果物（MP4, SRT）
```

## 4. 事前準備

### 必要ツール

    Python 3.10+

    VOICEVOX: 実行時に起動しておいてください。

    ffmpeg: パス（PATH）が通っている必要があります。

    Poppler: PDFをpngに変換するのに必要。bin フォルダにパスを通してください。


### ライブラリのインストール

    pip install python-pptx pdf2image requests tqdm

## 5\. 使い方
### 5.1 ノートの記述

スライドのノート欄に読み上げたい原稿を記入します。

    「//」または「／／」: 1枚のスライド内でこれを使うと、音声と字幕を分割し、適切な「間」を挿入して再生します。

### 5.2 データの配置

input/ フォルダに、同じファイル名の .pptx と .pdf を配置します。

    ここでの .pdf は、配置した .pptx をPowerPointの「名前を付けて保存」等でPDFとして書き出したもの を使用してください。
### 5.3 実行方法と入力ファイルの指定

プロジェクトルートで以下を実行します。

    特定のファイルを処理する場合:

    python run_all.py input/filename.pptx

    一括処理する場合（引数なし）:

    python run_all.py

    引数を指定しない場合、input/ フォルダ内にあるすべての .pptx ファイルを自動的にリストアップし、順次処理します。

実行時に環境診断が行われ、ツールが不足している場合は日本語で対策が表示されます。

### 5.4 各ステップの詳細と中間ファイル
    ステップ	出力ファイル（temp/）	内容
    01	notes.json, check_notes.txt	抽出・辞書適用済みの原稿データ。
    02	slides/slide_001.png〜	PDFから生成された150dpiの画像。
    03	audio/*.wav, timings.json	スライド毎の音声と、その再生時間の記録。
    04	work/*.mp4	ページごとの一時動画ファイル。

## 6. 最終成果物 (output/)

    [ファイル名].mp4: 完成した動画。

    [ファイル名].srt: 字幕ファイル。Windows環境での文字化けを防ぐため BOM付きUTF-8 で書き出されます。

## 7. トラブルシューティング

    画像と原稿の数が合わない:
    スライド画像が存在しないページは、安全のためにスキップされます。

    音声が生成されない:
    VOICEVOXが起動しているか、ポート番号（デフォルト50021）が正しいか確認してください。

    文字化け:
    SRTファイルは「BOM付きUTF-8」で生成されます。VLC等では問題ありませんが、Windows標準メディアプレーヤーで化ける場合はフォント設定を確認してください。

## 8. 補足仕様

### 辞書ファイル (dict/custom_dict.json)

読み間違いやすい専門用語などの「表記」と「読み」を登録します。

    内容形式: {"単語": "よみがな"} の形式で記述します。

    {
      "AI": "エーアイ",
      "MVP": "エムブイピー"
    }

    優先順位: 文字数の長い単語から優先して置換が適用されます。

### 音声（キャラクター）の切り替え

読み上げキャラクターを変更するには、scripts/03_generate_voice.py 内の以下の数値を変更してください。

    DEFAULT_SPEAKER_ID: VOICEVOXのキャラクターIDを指定します。

        例: 3 (ずんだもん), 21 (麒ヶ島宗麟)


## 9. ライセンス (License)
本プロジェクトは **MITライセンス** の下で公開されています。
詳細は [LICENSE](./LICENSE) ファイルを参照してください。

## 10. コピーライト (Copyright)
Copyright (c) 2026 yusun000

### 注意事項 (Disclaimer)
* **外部ツールのライセンス**: 本ツールの実行には VOICEVOX, ffmpeg, Poppler 等の外部ツールが必要です。これらを利用する際は、各ツールのライセンスを遵守してください。
* **音声・キャラクターの利用**: 本ツールによって生成された音声および動画を公開・利用する際は、使用した VOICEVOX キャラクター（ずんだもん、麒ヶ島宗麟など）の利用規約に従い、必要なクレジット表記等を行ってください。
