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
PDFから高品質なスライド画像を生成し、それらを統合して字幕付きの動画（MP4）を出力します。

※PDFはOfficeで別途出力したものを使用します。（本システムはPDF化を行いません）
※スライドの最後には、VOICEVOXの利用規約を遵守するためのクレジットページが自動的に挿入されます。

## 3. ディレクトリ構成

```text
.
├── run_all.py              # 全行程の自動実行・環境診断（司令塔）
├── assets/                 # 固定アセット
│   └── credit.png          # クレジットページの背景画像（任意）
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

- Python 3.10+
- VOICEVOX（実行時に起動しておいてください）
- ffmpeg（PATH が通っている必要があります）
- Poppler（PDF→PNG変換に必要。`bin` フォルダに PATH を通してください）

### ライブラリのインストール

```bash
pip install python-pptx pdf2image requests tqdm Pillow
```

## 5. 使い方

### 5.1 ノートの記述

スライドのノート欄に読み上げたい原稿を記入します。

- 「`//`」または「`／／`」  
  1枚のスライド内でこれを使うと、音声と字幕を分割し、適切な「間」を挿入して再生します。

### 5.2 データの配置

`input/` フォルダに、同じファイル名の `.pptx` と `.pdf` を配置します。

- ここでの `.pdf` は、配置した `.pptx` を PowerPoint の「名前を付けて保存」等で PDF として書き出したものを使用してください。

### 5.3 実行方法と入力ファイルの指定

プロジェクトルートで以下を実行します。

- 特定のファイルを処理する場合:

```bash
python run_all.py input/filename.pptx
```

- 一括処理する場合（引数なし）:

```bash
python run_all.py
```

引数を指定しない場合、`input/` フォルダ内にあるすべての `.pptx` ファイルを自動的にリストアップし、順次処理します。  
実行時に環境診断が行われ、ツールが不足している場合は日本語で対策が表示されます。

### 5.4 各ステップの詳細と中間ファイル

| ステップ | 出力ファイル（temp/） | 内容 |
|---|---|---|
| 01 | `notes.json`, `check_notes.txt` | 抽出・辞書適用済みの原稿データ |
| 02 | `slides/slide_001.png`〜 | PDFから生成された150dpiの画像 |
| 03 | `audio/*.wav`, `timings.json` | スライド毎の音声と、その再生時間の記録 |
| 04 | `work/*.mp4` | ページごとの一時動画ファイル |

## 6. クレジット表記（自動生成機能）

本システムでは、動画の最後にVOICEVOXのキャラクター利用規約に基づくクレジット表記を自動で生成・挿入します。

### 6.1 クレジットの内容

-  scripts/04_merge_video.py 内の SPEAKER_MAP に基づき、動画内で使用されたすべてのキャラクター名が自動的にリストアップされます。
-  表示例：「読み上げ：VOICEVOX 麒ヶ島宗麟」

### 6.2 表示形式

1.  背景画像がある場合: assets/credit.png を用意しておくと、その画像を背景としてクレジット文字が焼き込まれます。
2.  背景画像がない場合: 黒背景に白文字のクレジットページが自動生成されます。

### 6.3 マルチプラットフォーム対応

Linux環境とWindows環境の両方で日本語が正しく表示されるよう、システム内のフォント（MSゴシック、Noto Sans等）を自動探索して焼き込みを行います。

## 7. 最終成果物（output/）

- `[ファイル名].mp4`：完成した動画
- `[ファイル名].srt`：字幕ファイル  
  Windows環境での文字化けを防ぐため **BOM付きUTF-8** で書き出されます。

## 8. トラブルシューティング

- 画像と原稿の数が合わない  
  スライド画像が存在しないページは、安全のためにスキップされます。

- 音声が生成されない  
  VOICEVOXが起動しているか、ポート番号（デフォルト `50021`）が正しいか確認してください。

- 文字化け  
  SRTファイルは「BOM付きUTF-8」で生成されます。VLC等では問題ありませんが、Windows標準メディアプレーヤーで化ける場合はフォント設定を確認してください。

## 9. 補足仕様

### 辞書ファイル（dict/custom_dict.json）

読み間違いやすい専門用語などの「表記」と「読み」を登録します。

- 形式：`{"単語": "よみがな"}`

```json
{
  "AI": "エーアイ",
  "MVP": "エムブイピー"
}
```

- 優先順位：文字数の長い単語から優先して置換が適用されます。
- 字幕ファイルからこの辞書を自動生成するツールも用意しています（詳細は [辞書作成ツールの使い方](#voco-dict字幕から読み辞書を作る) を参照）。

### 音声（キャラクター）の切り替え

読み上げキャラクターを変更するには、`scripts/03_generate_voice.py` 内の以下の数値を変更してください。

- `DEFAULT_SPEAKER_ID`：VOICEVOXのキャラクターIDを指定します  
- 注意: 新しいキャラクターを使用する場合は、scripts/04_merge_video.py の SPEAKER_MAP にもIDと名前を追記してください。クレジット表記に正しく反映されます。

  例：`3`（ずんだもん）、`21`（麒ヶ島宗麟）

## 10. ライセンス（License）

本プロジェクトは **MITライセンス** の下で公開されています。  
詳細は [LICENSE](./LICENSE) ファイルを参照してください。

## 11. コピーライト（Copyright）

Copyright (c) 2026 yusun000

### 注意事項（Disclaimer）

- **外部ツールのライセンス**：本ツールの実行には VOICEVOX / ffmpeg / Poppler 等の外部ツールが必要です。利用時は各ツールのライセンスを遵守してください。
- **音声・キャラクターの利用**：本ツールによって生成された音声および動画を公開・利用する際は、使用した VOICEVOX キャラクター（ずんだもん、麒ヶ島宗麟など）の利用規約に従い、必要なクレジット表記等を行ってください。

## voco-dict（字幕から読み辞書を作る）

`tools/voco-dict.py` は、字幕ファイル（`.srt` / `.vtt` / `.txt`）が入ったフォルダを走査し、
VOICEVOXで読み間違えが起きやすい語（英字略語、英数字混在、長い漢字連結など）を抽出して、
「単語 → 読み（かな）」の辞書JSONを生成する補助ツールです。

- VOICEVOX Engine がある環境では、実際の読み（kana）を問い合わせて埋めることができます
- VOICEVOX Engine が無い環境でも、空欄（手修正前提）または簡易推定で辞書を作れます

### 1) 使い方

```bash
python tools/voco-dict.py <字幕フォルダ> [オプション]
```

例：

- **VOICEVOX Engine なし**（値は空で出力。あとで人手で埋める）
```bash
python tools/voco-dict.py ./data/subs --mode blank -o reading_map.json
```

- **VOICEVOX Engine なし**（簡易推定も入れる）
```bash
python tools/voco-dict.py ./data/subs --mode guess -o reading_map.json
```

- **VOICEVOX Engine あり**（`/audio_query` を使って読みを取得）
```bash
python tools/voco-dict.py ./data/subs --mode voicevox --voicevox http://127.0.0.1:50021 --speaker 3 -o reading_map.json
```

- **Engine が無い場合のフォールバック**（`voicevox` 指定でも接続できなければ自動で切替）
```bash
python tools/voco-dict.py ./data/subs --mode voicevox --fallback blank -o reading_map.json
# --fallback guess にすると簡易推定に切替
```

### 2) 主なオプション

- `--mode voicevox|blank|guess`  
  - `voicevox` : VOICEVOX Engine に問い合わせて読みを取得  
  - `blank`    : 読みは空文字（エンジン不要）  
  - `guess`    : 簡易推定（エンジン不要）
- `--voicevox http://127.0.0.1:50021` : VOICEVOX Engine のURL
- `--speaker 3` : speaker ID（環境により異なる）
- `--fallback blank|guess|none` : `--mode voicevox` 時にエンジン不在ならどうするか（既定: blank）
- `--min-count N` : 出現回数が N 回以上の語のみ出力
- `--top N` : 頻度上位 N 件のみに絞る（0 で無制限）
- `--enc utf-8,utf-8-sig,cp932,shift_jis` : 読み込みエンコーディング候補

### 3) 出力形式

`-o` で指定したJSONに、以下形式で出力します。

```json
{
  "LLM": "エルエルエム",
  "RTX4090": "アールティーエックスヨンゼロキュウゼロ",
  "添接": "",
  "重複": ""
}
```

- `--mode blank` の場合は値が空文字になります（人手で埋める前提）
- `--mode guess` は雑な推定です。最終的には人手で確認してください

### 4) 運用のコツ

- 最初は `--min-count 2` や `--top 200` などで絞って、辞書を小さく始めるのがおすすめです
- “正しい行”を大量に残すかどうかは運用次第ですが、
  - **修正した語だけを別ファイルに集約**しておく（例: `reading_map.fixed.json`）と保守が楽です
