# ♻️ CaChereCyCle

One JSONL store for small assets (icons, favicons, diagrams) kept as Base64 — standard library only.
小さな資産を Base64 で 1 か所に集約し、一次ファイルは入れたら片付け、使う分だけ手元に戻す。

| | 置き場所 | 中身 | 寿命 |
| --- | --- | --- | --- |
| 🏛 保管庫 | `store.jsonl`（このリポジトリ） | 1 行 = 1 資産 `{"id","sha256","mime","b64"}`、id 順 | 恒久 |
| 🏷 参照 | 各リポジトリの既存 JSON | id だけ（例 `"assets": ["OnTautoLogy.favicon"]`） | 恒久 |
| ⚡ 手元 | `.cache/assets/`（gitignore） | `sync` が書く `<id><ext>` | いつ消してもよい |

```sh
# 📥 入れる（同じ中身なら何もしない。既定の上限 1 MiB、--max-bytes 0 で解除）
python cacherecycle.py put docs/brand/favicon.png --id OnTautoLogy.favicon
# 📋 一覧 / 📤 取り出す / 🗑 消す
python cacherecycle.py ls
python cacherecycle.py get OnTautoLogy.favicon --out favicon.png
python cacherecycle.py drop OnTautoLogy.favicon
# ⚡ 参照している id だけを手元へ（自分が前に書いた不要分は 🧹 片付け）
python cacherecycle.py sync OnTautoLogy.favicon OnTautoLogy.icon --cache _site/brand
# 📐 サイズ別は保管せずその場で作る（NAME= で出力名を指定、元とファイルが変わらなければ作り直さない）
python cacherecycle.py sync favicon.ico=xprobe.favicon@16,32,48 icon-180.png=xprobe.icon@180:opaque icon-512.png=xprobe.icon@512 --cache _site
```

| 📐 spec | 出力 |
| --- | --- |
| `ID` | 保管したバイト列そのまま |
| `ID@192` | 192 px の正方形 PNG（透過を保つ） |
| `ID@180:opaque` | 透過を左上の色（透明なら白）で塗った PNG（ホーム画面用。iOS は透過を黒にする） |
| `ID@16,32,48` | その 3 サイズを入れた .ico |

縮小はブロック平均（透過で重み付け）、拡大は画素の複製なので、ドット絵はぼけない。元は 8 bit・非インターレースの PNG。

```python
import cacherecycle as CCCC
CCCC.put("icon.png", "OnTautoLogy.icon")      # 📥 → {"id", "sha256", "mime", "bytes", "changed"}
CCCC.get("OnTautoLogy.icon")                  # 📤 bytes（sha256 で検証）
CCCC.entry("OnTautoLogy.icon")                # 🔎 Base64 付きの 1 行（JSON のまま）
CCCC.derive("OnTautoLogy.icon@16,32,48")      # 📐 .ico の bytes（resize / ico も単体で呼べる）
CCCC.sync(["OnTautoLogy.icon", "icon-192.png=OnTautoLogy.icon@192"], cache="_site/brand")  # ⚡ {"written", "kept", "removed"}
```

```js
// 🟩 Node: 親リポジトリの py_launch.cjs で関数名を指定して呼ぶ（__main__ を通らない）
const { call } = require("./vendor/py_launch.cjs");
call("cacherecycle.py", "sync", { args: [["OnTautoLogy.icon"]], kwargs: { cache: "_site/brand" } });
```

- 🏷 id は `<リポジトリ名>.<何か>`。リポジトリ名は表記どおり（`Err-o-RoadRunner.icon`・`OnTautoLogy.favicon`・`myon-bioinformatics.github.io.icon`、`A-Z a-z 0-9 _ -` とドット区切り）。大文字小文字だけ違う id は入れない（Windows・macOS では同じファイル名になる）。そのまま手元のファイル名になる
- 🔑 `sha256` で中身を照合：同じなら書かない・壊れていれば止まる
- 📏 GIF デモなど大きいものはファイルのまま（README が data URI の画像を表示しないため）
- 🌐 公開リポジトリなので、公開してよい資産だけを置く

Python 3.11+ · 🧪 `python -S -m unittest discover -s tests`
