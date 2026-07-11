# build_dict_voice_check.py 採用ワークフロー版

vocoslide の `dict/custom_dict.json` から、読み上げ比較ページを生成する補助ツールです。

この版では、単に聴き比べるだけでなく、採用結果を次のファイル候補として出力できます。

- `custom_dict.proposed.json`
- `reading_overrides.proposed.json`
- `dict_check_adoptions.json`

## 基本方針

vocoslide のカスタム辞書は、基本的に次の形式です。

```json
{
  "AI": "エーアイ",
  "MVP": "エムブイピー",
  "重複": "ちょうふく"
}
```

この形式で直接反映できるのは、**登録語に対する読み** だけです。

そのため、採用案ごとの扱いは次のように分けます。

| 採用案 | 反映先 |
|---|---|
| 辞書なしが良い | `custom_dict.json` からその語を削除 |
| カスタム辞書が良い | `custom_dict.json` にそのまま残す |
| ひらがな化・カタカナ化・長音変換など | `custom_dict.json` の読みを更新 |
| 区切り追加、文中確認、アクセント、mora_pitches | `reading_overrides.json` 候補として出力 |

## 基本実行

```bash
python tools/build_dict_voice_check.py --input dict/custom_dict.json --out dict_check --speaker 3 --limit 100
```

生成されるファイル例です。

```text
dict_check/
  index.html
  data.js
  manifest.csv
  reading_patterns.auto.json
  reading_overrides.candidates.json
  audio/
```

## 画面での採用

右側の聴き比べ欄で、各候補の横にある **この案を採用** を押します。

採用すると、その語は `OK` 扱いになります。

採用先は候補ごとに表示されます。

- `custom_dictから除去`
- `custom_dict維持`
- `custom_dictの読みを更新`
- `reading_overridesへ反映`

## 出力ボタン

画面左側のボタンから、以下をダウンロードできます。

| ボタン | 内容 |
|---|---|
| 結果CSV | 確認結果の一覧 |
| 採用結果JSON | 次回生成時に渡すための結果ファイル |
| custom_dict候補 | 採用結果を反映したカスタム辞書候補 |
| reading_overrides候補 | 採用した override 候補 |

## 採用済みを次回生成対象から外す

画面から `dict_check_adoptions.json` を保存して、次回実行時に渡します。

```bash
python tools/build_dict_voice_check.py \
  --input dict/custom_dict.json \
  --out dict_check_next \
  --speaker 3 \
  --results dict_check/dict_check_adoptions.json
```

`OK` かつ何らかの案を採用済みの語は、次回以降の音声生成対象から外れます。

## 保留語に reading_overrides 候補を出す

前回結果で `保留` にした語は、次回 `--results` を渡すと、既定で `reading_overrides` 候補が追加されます。

生成される候補例です。

- アクセント先頭
- アクセント中央
- アクセント末尾
- ピッチ平坦
- ピッチ下降
- ピッチ上昇

全語に対して override 候補を出したい場合は、次のようにします。

```bash
python tools/build_dict_voice_check.py \
  --input dict/custom_dict.json \
  --out dict_check \
  --speaker 3 \
  --include-override-candidates all
```

出さない場合は、次です。

```bash
python tools/build_dict_voice_check.py \
  --input dict/custom_dict.json \
  --out dict_check \
  --speaker 3 \
  --include-override-candidates none
```

## reading_overrides 候補について

このツールが出力する `reading_overrides.proposed.json` は候補ファイルです。

既存の `dict/reading_overrides.json` にそのまま上書きする前に、現在のvocoslide側の形式に合わせて確認してください。

出力例です。

```json
{
  "重複": [
    {
      "surface": "重複",
      "reading": "ちょうふく",
      "accent": 2,
      "label": "reading_overrides: アクセント中央"
    },
    {
      "surface": "重複",
      "reading": "ちょうふく",
      "mora_pitches": [5.2, 5.12, 5.04, 4.96],
      "label": "reading_overrides: ピッチ下降"
    }
  ]
}
```

## 注意

- このツールは VOICEVOX ENGINE のユーザー辞書を直接編集しません。
- `custom_dict.json` に反映できるのは、基本的に「単語 → 読み」だけです。
- 区切り、文中の特殊補正、アクセント、mora_pitches は `reading_overrides.json` 側で扱う候補として分けます。
