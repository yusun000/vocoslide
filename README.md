# VocoSlide: Automated "Kamishibai" Video Generator

An automated pipeline to generate high-quality subtitled videos from PowerPoint (PPTX) notes using VOICEVOX (Neural Text-to-Speech) and ffmpeg.

## 1. English Summary

While this tool is optimized for Japanese content creators (due to its integration with Japanese TTS), the core logic provides a robust way to:

1. **Extract Narrative**: Pulls notes from PPTX as a screenplay.
2. **Visual Export**: Convert pre-exported slide PDFs into high-resolution PNG images.
3. **Phonetic/Subtitle Separation**: Manages different text layers for audio synthesis (reading) and visual display (subtitles) to ensure natural pronunciation without compromising subtitle readability.
4. **Video Insertion**: Extracts embedded videos from PPTX or inserts external videos using note commands.
5. **Automated Assembly**: Syncs visuals, audio, inserted videos, and subtitles into a final MP4 with SRT/VTT subtitles using ffmpeg.

## 2. システム概要

本システムは、PowerPoint（PPTX）のノート欄を原稿として読み込み、AI音声（VOICEVOX）とスライド画像を組み合わせて「紙芝居動画」を全自動で生成するシステムです。  
PDFから高品質なスライド画像を生成し、それらを統合して字幕付きの動画（MP4）を出力します。

また、PPTX内に埋め込まれた動画を抽出して、対象スライドの表示後に挿入する機能や、ノート欄に動画挿入コマンドを書いて外部動画を挿入する機能にも対応しています。

※PDFはOfficeで別途出力したものを使用します。（本システムはPDF化を行いません）  
※スライドの最後には、VOICEVOXの利用規約を遵守するためのクレジットページが自動的に挿入されます。

## 3. ディレクトリ構成

```text
.
├── run_all.py              # 全行程の自動実行・環境診断（司令塔）
├── assets/                 # 固定アセット
│   └── credit.png          # クレジットページの背景画像（任意）
├── scripts/                # メイン工程スクリプト群
│   ├── 01_extract_notes.py # ノート抽出・辞書適用・動画指定解析・埋め込み動画抽出
│   ├── 02_pdf_to_png.py    # PDFから画像(PNG)を生成
│   ├── 03_generate_voice.py# 音声合成(WAV)とタイミング記録
│   └── 04_merge_video.py   # ffmpegによる動画・字幕・挿入動画の結合
├── utils/                  # 共通ユーティリティ
│   ├── extractor.py        # PPTX/PDF操作クラス
│   └── voice_engine.py     # VOICEVOX操作・原稿解析
├── dict/
│   └── custom_dict.json    # 読み上げ補正用ユーザー辞書
├── tools/
│   └── voco-dict.py        # 字幕ファイルから読み上げ辞書候補を作成する補助ツール
├── input/                  # 【配置】変換元のPPTXとPDFを置く場所
├── temp/                   # 【自動生成】中間ファイル（実行のたびに削除）
│   ├── embedded_videos/    # 【自動生成】PPTX内の埋め込み動画の抽出先
│   ├── audio_cache/        # 【自動生成】音声生成キャッシュ
│   ├── slides/             # 【自動生成】PDFから生成したスライド画像
│   ├── audio/              # 【自動生成】VOICEVOXで生成した音声
│   └── work/               # 【自動生成】結合前の一時動画ファイル
└── output/                 # 【自動生成】最終成果物（MP4, SRT, VTT）
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

例：

```text
ここでは橋の概要を説明します。//次に、点検作業の流れを説明します。
```

#### 5.1.1 ノートがないスライドの扱い

本システムでは、スライドのノート欄を原稿として音声を生成します。  
そのため、**ノートが空のスライドは動画生成時にスキップされます**。

PowerPoint上では存在するスライドであっても、ノート欄に読み上げ対象の文章がない場合は、完成動画には含まれません。

#### 無音で数秒表示したい場合のノウハウ

ノートがないスライドはスキップされるため、説明文を読み上げずに数秒だけ表示したい場合でも、ノート欄には何らかの短い記述を入れておく必要があります。

例：

```text
。
```

または、短い間を作りたい場合は、ノート内の分割記号と `--silence-duration` を組み合わせます。

```text
。//。
```

実行例：

```bash
python run_all.py input/filename.pptx --silence-duration 3
```

この場合、各音声パートの前に指定秒数の無音が入るため、短い記号だけを読ませることで、実質的に「無音に近い表示時間」を作ることができます。

※完全な無音スライドを明示的に作る専用コマンドは、現時点では未実装です。

#### 5.1.2 動画挿入機能

本システムでは、スライド画像と読み上げ音声による紙芝居動画に加えて、動画ファイルを途中に挿入できます。

動画挿入には、次の2つの方法があります。

1. PPTXに埋め込まれている動画を自動抽出して挿入する方法
2. ノート欄に動画挿入コマンドを書く方法

##### PPTXに埋め込まれた動画の挿入

PowerPointのスライド上に動画が埋め込まれている場合、Step 01でPPTX内の動画ファイルを抽出し、対象スライドの表示後に挿入します。

抽出された動画は、実行時に次のフォルダへ保存されます。

```text
temp/embedded_videos/
```

挿入タイミングは、現在の仕様では **対象スライドの読み上げ・表示が終わった後** です。

なお、ノートが空のスライドは動画生成時にスキップされるため、埋め込み動画を入れたスライドであっても、ノート欄が空の場合はそのスライド自体が処理対象になりません。  
埋め込み動画を確実に挿入したい場合は、対象スライドのノート欄に短い記述を入れてください。

例：

```text
。
```

##### ノート欄に動画挿入コマンドを書く方法

PPTXに動画を埋め込まなくても、ノート欄に制御コマンドを書くことで、外部動画ファイルを挿入できます。

対応しているコマンドは次の3種類です。

```text
@video: path/to/movie.mp4
@video-before: path/to/movie.mp4
@video-after: path/to/movie.mp4
```

意味は次のとおりです。

| コマンド | 挿入位置 |
|---|---|
| `@video:` | 対象スライドの読み上げ・表示後に動画を挿入 |
| `@video-after:` | 対象スライドの読み上げ・表示後に動画を挿入 |
| `@video-before:` | 対象スライドの読み上げ・表示前に動画を挿入 |

例：

```text
このページでは、作業状況を動画で確認します。

@video-after: videos/sample.mp4
```

この場合、スライドの読み上げが終わった後に `videos/sample.mp4` が挿入されます。

##### 動画ファイルのパス

ノート欄に書く動画ファイルのパスは、原則として **PPTXファイルが置かれているフォルダを基準** に解決されます。

例：

```text
input/
├── sample.pptx
├── sample.pdf
└── videos/
    └── bridge.mp4
```

この場合、ノート欄には次のように書けます。

```text
@video-after: videos/bridge.mp4
```

絶対パスを指定することもできますが、他のPCで実行する場合にパスが合わなくなるため、通常はPPTXからの相対パスを推奨します。

##### 複数動画の挿入

同じスライドのノート欄に複数の動画挿入コマンドを書くこともできます。

```text
このページでは、2つの動画を順番に確認します。

@video-after: videos/part1.mp4
@video-after: videos/part2.mp4
```

PPTXに複数の動画が埋め込まれている場合も、抽出できた動画はスライド表示後に順番に挿入されます。

##### 動画の変換処理

挿入される動画は、ffmpegによりスライド動画と連結しやすい形式へ自動変換されます。

- スライドと同じ画面サイズに合わせてリサイズ
- アスペクト比を維持
- 余白を追加して画面サイズを統一
- 音声をAAC形式に変換
- MP4として中間出力

中間ファイルは次のフォルダに作成されます。

```text
temp/work/
```

##### 動画ファイルが見つからない場合

ノート欄で指定した動画ファイルが見つからない場合、標準設定では警告を表示して処理を継続します。

設定ファイル `config.json` の `video_insertion` で、挙動を変更できます。

```json
{
  "video_insertion": {
    "enabled": true,
    "fps": 30,
    "missing_file": "warn"
  }
}
```

`missing_file` の考え方：

| 値 | 動作 |
|---|---|
| `warn` | 警告を表示して処理継続 |
| `ignore` | 警告を抑制して処理継続 |
| `error` | ファイルがない場合にエラー終了 |

### 5.2 データの配置

`input/` フォルダに、同じファイル名の `.pptx` と `.pdf` を配置します。

- ここでの `.pdf` は、配置した `.pptx` を PowerPoint の「名前を付けて保存」等で PDF として書き出したものを使用してください。
- ノート欄から外部動画を指定する場合は、PPTXからの相対パスで参照しやすいように、PPTXと同じフォルダまたはその配下に動画ファイルを置くことを推奨します。

例：

```text
input/
├── sample.pptx
├── sample.pdf
└── videos/
    └── bridge.mp4
```

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
| 01 | `notes.json`, `check_notes.txt`, `embedded_videos/` | ノート抽出、辞書適用、動画挿入コマンド解析、PPTX埋め込み動画の抽出 |
| 02 | `slides/slide_001.png`〜 | PDFから生成された150dpiの画像 |
| 03 | `audio/*.wav`, `timings.json` | スライド毎の音声と、その再生時間の記録 |
| 04 | `work/*.mp4`, `merge_stats_*.json` | ffmpegによるスライド動画・挿入動画・字幕の結合 |

Step 01の確認用ファイル `temp/check_notes.txt` には、抽出されたノート、読み上げ用テキスト、動画挿入指定、埋め込み動画の抽出状況が出力されます。

### 5.5 パフォーマンス改善機能（音声生成の高速化）

本システムでは、音声生成（Step 03）の処理時間を短縮するために、以下の機能が追加されています。

#### ■ 重複文章の自動再利用

同じ動画内で同じ文章が複数回出現した場合、音声を再生成せず再利用します。

例：

- 「ご視聴ありがとうございました。」が複数回出る場合 → 1回だけ生成

※以下がすべて一致した場合のみ再利用されます  

- 文章内容
- 話者（VOICEVOXキャラクター）
- 音声設定（速度・ピッチなど）

特別な設定は不要で、自動的に動作します。

---

#### ■ 並列処理（高速化）

複数の音声を同時に生成することで処理時間を短縮します。

使用例：

```bash
python run_all.py --max-workers 3
```

目安：

- 2〜4：安定（推奨）
- 5以上：環境によっては逆に遅くなる可能性あり

※GPU不要、CPUのみでも効果があります。

---

#### ■ キャッシュ機能（再利用）

一度生成した音声を保存し、同じ条件なら再利用します。

保存場所：

```text
temp/audio_cache/
```

---

##### キャッシュ関連オプション

キャッシュを使わない場合：

```bash
--no-disk-cache
```

キャッシュを削除してから実行：

```bash
--clear-cache
```

---

### 5.6 音声生成の詳細オプション

音声生成スクリプト単体でも実行可能です。

```bash
python scripts/03_generate_voice.py [オプション]
```

主なオプション：

- `--max-workers N`  
  並列処理数を指定

- `--no-disk-cache`  
  キャッシュを使用しない

- `--no-dedup`  
  重複文章の再利用を無効化

- `--clear-cache`  
  キャッシュを削除してから実行

- `--quiet`  
  ログを簡略表示

---

### 5.7 動画生成の処理時間の記録

動画生成（Step 04）では、処理時間を記録できます。

記録される内容：

- ページごとの処理時間
  - 音声処理時間
  - 動画生成時間
  - 挿入動画の変換時間
- 挿入動画の開始・終了時刻
- 全体の処理時間

出力ファイル：

```text
temp/merge_stats_ファイル名.json
```

---

### 5.8 全体実行時のオプション（run_all.py）

全体処理でも同様の設定が利用できます。

```bash
python run_all.py [オプション]
```

主なオプション：

- `--max-workers N`  
  音声生成の並列数

- `--no-disk-cache`  
  キャッシュを使わない

- `--no-dedup`  
  重複再利用を無効化

- `--clear-cache`  
  キャッシュ削除

- `--keep-temp`  
  中間ファイルを削除せず残す

- `--quiet`  
  ログを簡略表示

- `--silence-duration 秒数`  
  音声パート間の無音時間を指定

---

### 5.9 推奨設定

#### ■ 初回テスト（不具合確認）

```bash
python run_all.py --max-workers 1 --no-disk-cache --no-dedup
```

---

#### ■ 通常運用（高速）

```bash
python run_all.py --max-workers 3
```

---

#### ■ キャッシュをリセットしたい場合

```bash
python run_all.py --clear-cache
```

---

#### ■ 無音時間を長めにしたい場合

```bash
python run_all.py input/filename.pptx --silence-duration 3
```

---

### 5.10 補足（動作の考え方）

本システムは以下の順序で効率化を行っています。

1. 文章を分割
2. 同じ文章を検出
3. 重複をまとめる
4. 並列で音声生成
5. 元の順序に戻す
6. スライド動画、挿入動画、クレジットページを結合する

これにより、

- 無駄な音声生成を削減
- 処理時間を短縮
- 安定した出力
- スライド説明と実写・操作動画などの組み合わせ

を実現しています。

## 6. クレジット表記（自動生成機能）

本システムでは、動画の最後にVOICEVOXのキャラクター利用規約に基づくクレジット表記を自動で生成・挿入します。

### 6.1 クレジットの内容

- `scripts/04_merge_video.py` 内の `SPEAKER_MAP` に基づき、動画内で使用されたすべてのキャラクター名が自動的にリストアップされます。
- 表示例：「読み上げ：VOICEVOX 麒ヶ島宗麟」

### 6.2 表示形式

1. 背景画像がある場合: `assets/credit.png` を用意しておくと、その画像を背景としてクレジット文字が焼き込まれます。
2. 背景画像がない場合: 黒背景に白文字のクレジットページが自動生成されます。

### 6.3 マルチプラットフォーム対応

Linux環境とWindows環境の両方で日本語が正しく表示されるよう、システム内のフォント（MSゴシック、Noto Sans等）を自動探索して焼き込みを行います。

## 7. 最終成果物（output/）

- `[ファイル名].mp4`：完成した動画
- `[ファイル名].srt`：字幕ファイル
- `[ファイル名].vtt`：字幕ファイル。MoodleなどのLMS（学習管理システム）用 WebVTT  
  Windows環境での文字化けを防ぐため **BOM付きUTF-8** で書き出されます。

## 8. トラブルシューティング

- 画像と原稿の数が合わない / 一部のスライドが動画に出ない  
  ノート欄が空のスライドは、音声パートが生成されないため動画生成時にスキップされます。  
  無音で数秒表示したい場合でも、ノート欄に短い記述を入れてください。  
  例：`。`

- 埋め込み動画が出ない  
  PPTX内の動画は抽出後、対象スライドの表示後に挿入されます。  
  ただし、対象スライドのノート欄が空の場合、そのスライド自体がスキップされるため、埋め込み動画も挿入されません。  
  対象スライドのノート欄に短い記述を入れてから再実行してください。

- ノート欄で指定した動画が挿入されない  
  `@video-after: videos/sample.mp4` のように指定したパスが正しいか確認してください。  
  相対パスはPPTXファイルのあるフォルダを基準に解決されます。

- 挿入動画がカクつく / コマ送りのようになる  
  挿入動画はffmpegでスライド動画と連結しやすい形式へ変換されます。  
  元動画の形式やフレームレートによっては変換結果が不自然になる場合があります。  
  `config.json` の `video_insertion.fps` を調整するか、事前に一般的なMP4形式へ変換してから指定してください。

- 音声が生成されない  
  VOICEVOXが起動しているか、ポート番号（デフォルト `50021`）が正しいか確認してください。

- 文字化け  
  SRT/VTTファイルは「BOM付きUTF-8」で生成されます。VLC等では問題ありませんが、Windows標準メディアプレーヤーで化ける場合はフォント設定を確認してください。

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
- 字幕ファイルからこの辞書を自動生成するツールも用意しています（詳細は [12.1 voco-dict（字幕から読み辞書を作る）](#121-voco-dict字幕から読み辞書を作る) を参照）。

### 音声（キャラクター）の切り替え

読み上げキャラクターを変更するには、`scripts/03_generate_voice.py` 内の以下の数値を変更してください。

- `DEFAULT_SPEAKER_ID`：VOICEVOXのキャラクターIDを指定します
- 注意: 新しいキャラクターを使用する場合は、`scripts/04_merge_video.py` の `SPEAKER_MAP` にもIDと名前を追記してください。クレジット表記に正しく反映されます。

例：`3`（ずんだもん）、`21`（麒ヶ島宗麟）

## 10. ライセンス（License）

本プロジェクトは **MITライセンス** の下で公開されています。  
詳細は [LICENSE](./LICENSE) ファイルを参照してください。

## 11. コピーライト（Copyright）

Copyright (c) 2026 yusun000

### 注意事項（Disclaimer）

- **外部ツールのライセンス**：本ツールの実行には VOICEVOX / ffmpeg / Poppler 等の外部ツールが必要です。利用時は各ツールのライセンスを遵守してください。
- **音声・キャラクターの利用**：本ツールによって生成された音声および動画を公開・利用する際は、使用した VOICEVOX キャラクター（ずんだもん、麒ヶ島宗麟など）の利用規約に従い、必要なクレジット表記等を行ってください。

---

## 12. 付録：補助ツール

以下は、VocoSlide本体の動画生成処理とは別に利用できる補助ツールです。  
字幕ファイルから読み上げ辞書候補を作成したい場合に使用します。

### 12.1 voco-dict（字幕から読み辞書を作る）

`tools/voco-dict.py` は、字幕ファイル（`.srt` / `.vtt` / `.txt`）が入ったフォルダを走査し、VOICEVOXで読み間違えが起きやすい語（英字略語、英数字混在、長い漢字連結など）を抽出して、「単語 → 読み（かな）」の辞書JSONを生成する補助ツールです。

- VOICEVOX Engine がある環境では、実際の読み（kana）を問い合わせて埋めることができます。
- VOICEVOX Engine が無い環境でも、空欄（手修正前提）または簡易推定で辞書を作れます。

#### 1) 使い方

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

#### 2) 主なオプション

- `--mode voicevox|blank|guess`  
  - `voicevox` : VOICEVOX Engine に問い合わせて読みを取得
  - `blank` : 読みは空文字（エンジン不要）
  - `guess` : 簡易推定（エンジン不要）
- `--voicevox http://127.0.0.1:50021` : VOICEVOX Engine のURL
- `--speaker 3` : speaker ID（環境により異なる）
- `--fallback blank|guess|none` : `--mode voicevox` 時にエンジン不在ならどうするか（既定: blank）
- `--min-count N` : 出現回数が N 回以上の語のみ出力
- `--top N` : 頻度上位 N 件のみに絞る（0 で無制限）
- `--enc utf-8,utf-8-sig,cp932,shift_jis` : 読み込みエンコーディング候補

#### 3) 出力形式

`-o` で指定したJSONに、以下形式で出力します。

```json
{
  "LLM": "エルエルエム",
  "RTX4090": "アールティーエックスヨンゼロキュウゼロ",
  "添接": "",
  "重複": ""
}
```

- `--mode blank` の場合は値が空文字になります（人手で埋める前提）。
- `--mode guess` は雑な推定です。最終的には人手で確認してください。

#### 4) 運用のコツ

- 最初は `--min-count 2` や `--top 200` などで絞って、辞書を小さく始めるのがおすすめです。
- “正しい行”を大量に残すかどうかは運用次第ですが、修正した語だけを別ファイルに集約しておくと保守が楽です。  
  例：`reading_map.fixed.json`
