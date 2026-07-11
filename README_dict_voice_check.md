# build_dict_voice_check.py 自動読み方調整パターン版

vocoslide のカスタム辞書JSONを読み込み、以下を単語ごとに聴き比べできる静的HTMLページを生成します。

- 辞書なし
- カスタム辞書適用
- 自動生成した読み方調整パターン
- 手動で追加した読み方調整パターン

あわせて、生成・マージ後の追加版JSONファイルも保存します。

## 対応する辞書形式

```json
{
  "AI": "エーアイ",
  "MVP": "エムブイピー",
  "重複": "ちょうふく"
}
```

キーが登録語、値がカスタム辞書で指定する読みです。

## 基本実行

```bash
python tools/build_dict_voice_check.py --input dict/custom_dict.json --out dict_check --speaker 3 --limit 100
```

生成後、出力先には次のようなファイルが作られます。

```text
dict_check/
  index.html
  data.js
  manifest.csv
  reading_patterns.auto.json
  audio/
```

`reading_patterns.auto.json` が、読み方調整パターンの追加版JSONです。

## 自動生成される候補

読みごとに、以下のような候補を自動生成します。

| 候補 | 例 |
|---|---|
| ひらがな化 | `エーアイ` → `えーあい` |
| カタカナ化 | `ちょうふく` → `チョウフク` |
| 長音を母音化 | `エーアイ` → `エエアイ` |
| 母音連続を長音化 | `エエアイ` → `エーアイ` |
| 区切り追加 | `エーアイ` → `エー、アイ。` |
| 文中確認 | `重複は、ちょうふくと読みます。` |

候補は「正解を推定する」ものではなく、VOICEVOXに渡す文字列の違いを聴き比べるための機械的な候補です。

## 追加版JSONの例

```json
{
  "AI": [
    {
      "label": "ひらがな化",
      "reading": "えーあい"
    },
    {
      "label": "長音を母音化",
      "reading": "エエアイ"
    },
    {
      "label": "区切り追加",
      "text": "エー、アイ。"
    }
  ],
  "重複": [
    {
      "label": "カタカナ化",
      "reading": "チョウフク"
    },
    {
      "label": "文中確認",
      "text": "重複は、ちょうふくと読みます。"
    }
  ]
}
```

`reading` は `--pattern-template` に入れて合成されます。既定は `{reading}。` です。  
`text` はそのままVOICEVOXに渡されます。

## 手動パターンを併用する

手動パターンJSONを指定すると、自動生成分とマージします。  
手動指定は優先して先頭に入ります。

```bash
python tools/build_dict_voice_check.py \
  --input dict/custom_dict.json \
  --out dict_check \
  --speaker 3 \
  --patterns dict/reading_patterns.manual.json
```

手動パターンJSONの例です。

```json
{
  "重複": [
    {"label": "本来想定", "reading": "ちょうふく"},
    {"label": "別読み確認", "reading": "じゅうふく"}
  ],
  "AI": [
    {"label": "区切る", "text": "エー、アイ。"}
  ]
}
```

## 追加版JSONの保存先を変える

```bash
python tools/build_dict_voice_check.py \
  --input dict/custom_dict.json \
  --out dict_check \
  --speaker 3 \
  --auto-patterns-out dict/reading_patterns.generated.json
```

## 自動生成を止める

手動パターンだけ使いたい場合は、次のようにします。

```bash
python tools/build_dict_voice_check.py \
  --input dict/custom_dict.json \
  --out dict_check \
  --speaker 3 \
  --no-auto-patterns \
  --patterns dict/reading_patterns.manual.json
```

## パターン数を調整する

1語あたりの候補数を変えるには、`--max-auto-patterns` を指定します。

```bash
python tools/build_dict_voice_check.py \
  --input dict/custom_dict.json \
  --out dict_check \
  --speaker 3 \
  --max-auto-patterns 8
```

## 聴き比べ画面

HTML画面では、単語ごとに以下を再生できます。

```text
1. 辞書なし
2. カスタム辞書
3. 読み方調整候補1
4. 読み方調整候補2
...
```

キーボード操作もできます。

| キー | 動作 |
|---|---|
| `1` | 辞書なしを再生 |
| `2` | カスタム辞書を再生 |
| `3` 以降 | 読み方調整候補を再生 |
| `O` | OK |
| `N` | NG |
| `H` | 保留 |
| `←` / `→` | 前後の語へ |
| `/` | 検索欄へ |

## 注意

このツールは、VOICEVOX ENGINEのユーザー辞書へ単語を登録するものではありません。  
vocoslide のカスタム辞書で指定した読みと、機械的に作った読み方調整候補をVOICEVOXに渡して聴き比べるための補助ツールです。
