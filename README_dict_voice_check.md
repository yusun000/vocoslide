# vocoslide カスタム辞書読み上げチェックツール

`vocoslide` のカスタム辞書 JSON を読み込み、VOICEVOX ENGINE で読み上げ音声を生成し、ブラウザで確認できる静的HTMLページを作成する補助ツールです。

辞書の語数が多くなった場合に、登録語ごとの読み上げ音声を確認し、OK / NG / 保留、メモを記録する用途を想定しています。

---

## 対応するJSON形式

このツールが対象にするのは、`vocoslide` のカスタム辞書JSONです。

形式は **「単語: 読み」** の単純なオブジェクトです。

```json
{
  "AI": "エーアイ",
  "MVP": "エムブイピー",
  "重複": "ちょうふく"
}
```

キーが **登録語**、値が **カスタム辞書で指定する読み** です。

| JSONの内容 | 意味 |
|---|---|
| `"AI"` | 登録語 |
| `"エーアイ"` | 読み |
| `"重複"` | 登録語 |
| `"ちょうふく"` | 読み |

このツールでは、上記のJSONを読み込んで、たとえば次のような読み上げ音声を生成します。

```text
No. 5。登録語、重複。カスタム辞書での読みは、ちょうふく。
```

---

## 生成される読み上げ文

既定では、各語について次のような文を読み上げます。

```text
No. 5。登録語、重複。カスタム辞書での読みは、ちょうふく。
```

登録語そのものだけでなく、カスタム辞書で指定した読みも音声で確認できるようにしています。

読み上げ文を変更したい場合は、`--template` オプションを使います。

Windows のコマンドプロンプトでは、次のように指定できます。

```bat
python tools\build_dict_voice_check.py ^
  --input dict\custom_dict.json ^
  --out dict_check ^
  --speaker 3 ^
  --template "No. {no}。登録語、{surface}。カスタム辞書での読みは、{reading}。"
```

Git Bash では次のように指定できます。

```bash
python tools/build_dict_voice_check.py \
  --input dict/custom_dict.json \
  --out dict_check \
  --speaker 3 \
  --template "No. {no}。登録語、{surface}。カスタム辞書での読みは、{reading}。"
```

テンプレートで使える値は以下です。

| プレースホルダ | 内容 |
|---|---|
| `{no}` | 連番 |
| `{surface}` | 登録語 |
| `{reading}` | カスタム辞書で指定した読み |

---

## 前提条件

- Python 3.9 以降
- VOICEVOX ENGINE が起動していること
- `dict/custom_dict.json` などの vocoslide カスタム辞書JSONがあること

VOICEVOX ENGINE は通常、以下のURLで起動しています。

```text
http://127.0.0.1:50021
```

---

## 基本的な使い方

リポジトリ直下で実行する例です。

```bash
python tools/build_dict_voice_check.py --input dict/custom_dict.json --out dict_check --speaker 3
```

最初は件数を絞って試すことをおすすめします。

```bash
python tools/build_dict_voice_check.py --input dict/custom_dict.json --out dict_check_test --speaker 3 --limit 100
```

音声を生成せず、HTMLや一覧だけ確認したい場合は次のようにします。

```bash
python tools/build_dict_voice_check.py --input dict/custom_dict.json --out dict_check --skip-audio
```

VOICEVOX ENGINE のURLを変更している場合は、`--engine-url` を指定します。

```bash
python tools/build_dict_voice_check.py \
  --input dict/custom_dict.json \
  --out dict_check \
  --speaker 3 \
  --engine-url http://127.0.0.1:50021
```

---

## 出力されるファイル

実行すると、指定した出力フォルダに以下のようなファイルが作成されます。

```text
dict_check/
  index.html
  data.js
  manifest.csv
  audio/
    0001_AI.wav
    0002_MVP.wav
    0003_重複.wav
```

| ファイル / フォルダ | 内容 |
|---|---|
| `index.html` | 読み上げチェック用の静的HTMLページ |
| `data.js` | 一覧表示用データ |
| `manifest.csv` | 生成対象一覧 |
| `audio/` | 登録語ごとの読み上げ音声 |

---

## チェックページの機能

`index.html` をブラウザで開くと、以下の操作ができます。

- 登録語・読みで検索
- 状態で絞り込み
  - 未確認
  - OK
  - NG
  - 保留
- 音声再生
- OK / NG / 保留の記録
- メモ入力
- 結果CSVのダウンロード
- 結果JSONのダウンロード
- 保存済み結果JSONの読み込み

判定結果やメモは、ブラウザのローカルストレージにも保存されます。

---

## キーボード操作

件数が多い場合は、キーボード操作を使うと確認作業がしやすくなります。

| キー | 動作 |
|---|---|
| `Space` | 音声再生 |
| `O` | OK |
| `N` | NG |
| `H` | 保留 |
| `→` | 次の語へ |
| `←` | 前の語へ |
| `/` | 検索欄へ移動 |

---

## IISで公開する場合

`dict_check` フォルダをそのままIIS配下に配置してください。

例：

```text
C:\inetpub\wwwroot\vocoslide\dict_check\
```

ブラウザでは次のように開きます。

```text
http://サーバ名/vocoslide/dict_check/index.html
```

フォルダ構成は崩さないでください。

```text
dict_check/
  index.html
  data.js
  manifest.csv
  audio/
```

---

## 結果の保存と再利用

チェック結果は、画面上でCSVまたはJSONとしてダウンロードできます。

- CSV：Excelで確認・整理したい場合
- JSON：次回のチェックページに読み戻したい場合

辞書を修正した後は、再度このツールで音声とHTMLを生成し、NG語だけ再確認する運用がしやすいです。

---

## 運用例

1. `dict/custom_dict.json` を編集する
2. 読み上げチェックページを生成する

```bash
python tools/build_dict_voice_check.py --input dict/custom_dict.json --out dict_check --speaker 3
```

3. `dict_check/index.html` を開く
4. 音声を聞いて OK / NG / 保留 を記録する
5. NG語を辞書で修正する
6. 再生成して再確認する

---

## 注意事項

- このツールは、vocoslide のカスタム辞書JSONを読み上げ確認するための補助ツールです。
- VOICEVOX ENGINE のユーザー辞書を直接編集・登録するツールではありません。
- 読みの自然さ、アクセント、文中での崩れは、最終的には実際のvocoslide生成動画でも確認してください。
