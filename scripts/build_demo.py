#!/usr/bin/env python3
"""Build a self-contained HTML live-demo page from real cacherecycle.py runs (stdlib only).

Runs put / ls / derive / sync against a temporary store and cache (never mutating the repo store),
collects representative icons from store.jsonl, and emits a static, zero-network HTML page for
BrowserTestKit's evidence_gif.py.
"""
from __future__ import annotations

import argparse
import base64
import html
import io
import json
import struct
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cacherecycle as CCCC  # noqa: E402

FEATURED_IDS = (
    "CaChereCyCle.icon",
    "OnTautoLogy.icon",
    "BrowserTestKit.validation-buddy.icon",
    "Err-o-RoadRunner.icon",
    "cli_args.icon",
    "xprobe.icon",
    "markdown.icon",
    "ascii_artist.icon",
    "NagoyAction.icon",
    "SakAnalytics.icon",
    "PySaiyanWeBoo.icon",
    "gh_identity.icon",
)


def _run_cli(*argv: str) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = CCCC.main(list(argv))
    if code != 0:
        raise RuntimeError(f"cacherecycle failed ({code}): {' '.join(argv)}")
    return buf.getvalue().strip()


def _data_uri(mime: str, raw: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def _extract_ico_pngs(ico_bytes: bytes) -> list[tuple[int, bytes]]:
    count = struct.unpack("<HHH", ico_bytes[:6])[2]
    out = []
    for i in range(count):
        width, _height, _c, _r, _p, _b, size, offset = struct.unpack("<BBBBHHII", ico_bytes[6 + 16 * i:22 + 16 * i])
        out.append((width or 256, ico_bytes[offset:offset + size]))
    return out


def build_demo_html() -> str:
    all_meta = CCCC.ls()
    total_assets = len(all_meta)
    store_kb = CCCC.STORE.stat().st_size // 1024

    # 1) Featured gallery from real store.jsonl
    meta_by_id = {m["id"]: m for m in all_meta}
    gallery_cards = []
    for asset_id in FEATURED_IDS:
        meta = meta_by_id.get(asset_id)
        if not meta:
            continue
        raw = CCCC.get(asset_id)
        uri = _data_uri(str(meta["mime"]), raw)
        short_sha = str(meta["sha256"])[:8]
        gallery_cards.append(
            f'<div class="asset-tile">'
            f'<img src="{uri}" alt="{html.escape(asset_id)}" width="48" height="48">'
            f'<div class="asset-meta"><div class="asset-id">{html.escape(asset_id)}</div>'
            f'<div class="asset-sub">{meta["bytes"]} B · {short_sha}</div></div></div>'
        )

    # 2) Real CLI run in temp directory for put / ls / derive / sync
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        tmp_store = tmp / "store.jsonl"
        cache_dir = tmp / ".cache" / "assets"
        src_icon = tmp / "icon.png"
        src_fav = tmp / "favicon.png"
        src_icon.write_bytes(CCCC.get("CaChereCyCle.icon"))
        src_fav.write_bytes(CCCC.get("CaChereCyCle.favicon"))

        put_1 = _run_cli("--store", str(tmp_store), "put", str(src_icon), "--id", "CaChereCyCle.icon")
        put_2 = _run_cli("--store", str(tmp_store), "put", str(src_icon), "--id", "CaChereCyCle.icon")
        put_3 = _run_cli("--store", str(tmp_store), "put", str(src_fav), "--id", "CaChereCyCle.favicon")
        ls_out = _run_cli("--store", str(tmp_store), "ls")

        # Derive variants from real CaChereCyCle.icon & favicon
        raw_icon = CCCC.derive("CaChereCyCle.icon", store=tmp_store)
        png_192 = CCCC.derive("CaChereCyCle.icon@192", store=tmp_store)
        png_180_op = CCCC.derive("CaChereCyCle.icon@180:opaque", store=tmp_store)
        ico_bytes = CCCC.derive("CaChereCyCle.favicon@16,32,48", store=tmp_store)
        ico_layers = _extract_ico_pngs(ico_bytes)

        # Sync 3 steps (with an unrelated user file in cache to show it is never touched)
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / "README.local.txt").write_text("user file", encoding="utf-8")
        rel_cache = ".cache/assets"
        import os
        old_cwd = Path.cwd()
        os.chdir(tmp)
        try:
            sync_1 = _run_cli(
                "--store", str(tmp_store), "sync",
                "CaChereCyCle.icon", "favicon.ico=CaChereCyCle.favicon@16,32,48", "icon-180.png=CaChereCyCle.icon@180:opaque",
                "--cache", rel_cache,
            )
            files_1 = sorted(p.name for p in cache_dir.iterdir() if p.name != ".cacherecycle.json")

            sync_2 = _run_cli(
                "--store", str(tmp_store), "sync",
                "CaChereCyCle.icon", "favicon.ico=CaChereCyCle.favicon@16,32,48", "icon-180.png=CaChereCyCle.icon@180:opaque",
                "--cache", rel_cache,
            )

            sync_3 = _run_cli(
                "--store", str(tmp_store), "sync",
                "favicon.ico=CaChereCyCle.favicon@16,32,48", "icon-180.png=CaChereCyCle.icon@180:opaque",
                "--cache", rel_cache,
            )
            files_3 = sorted(p.name for p in cache_dir.iterdir() if p.name != ".cacherecycle.json")
        finally:
            os.chdir(old_cwd)

    put_cli_text = (
        "$ python -S cacherecycle.py put icon.png --id CaChereCyCle.icon\n"
        f"{put_1}\n"
        "$ python -S cacherecycle.py put icon.png --id CaChereCyCle.icon\n"
        f"{put_2}  # same bytes -> untouched\n"
        "$ python -S cacherecycle.py put favicon.png --id CaChereCyCle.favicon\n"
        f"{put_3}\n"
        "$ python -S cacherecycle.py ls\n"
        f"{ls_out}"
    )

    sync_cli_text = (
        "# 1) First sync -> writes assets & records .cacherecycle.json\n"
        "$ python -S cacherecycle.py sync CaChereCyCle.icon favicon.ico=CaChereCyCle.favicon@16,32,48 icon-180.png=CaChereCyCle.icon@180:opaque\n"
        f"{sync_1}\n\n"
        "# 2) Re-run with same specs -> kept (variants not rebuilt)\n"
        f"{sync_2}\n\n"
        "# 3) Drop CaChereCyCle.icon from list -> removed cleanly; user files untouched\n"
        "$ python -S cacherecycle.py sync favicon.ico=CaChereCyCle.favicon@16,32,48 icon-180.png=CaChereCyCle.icon@180:opaque\n"
        f"{sync_3}"
    )

    ico_imgs = "".join(
        f'<span class="ico-badge"><img src="{_data_uri("image/png", b)}" width="{sz}" height="{sz}" alt="{sz}px"><span>{sz}px</span></span>'
        for sz, b in ico_layers
    )

    icon_uri = _data_uri("image/png", raw_icon)
    favicon_uri = _data_uri("image/png", CCCC.get("CaChereCyCle.favicon"))

    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="generator" content="CaChereCyCle build_demo.py">
<title>♻️ CaChereCyCle Live Demo</title>
<link rel="icon" type="image/png" href="{favicon_uri}">
<style>
:root {{
  --bg: #0d1117;
  --panel: #161b22;
  --card: #1c222b;
  --border: #30363d;
  --text: #e6edf3;
  --muted: #8b949e;
  --accent: #2ea043;
  --cyan: #38bdf8;
  --code-bg: #0b0f14;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  padding: 20px 24px;
  background: var(--bg);
  color: var(--text);
  font-family: Meiryo, "Hiragino Kaku Gothic ProN", system-ui, sans-serif;
  line-height: 1.5;
}}
.wrap {{ max-width: 1120px; margin: 0 auto; }}
header {{
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 16px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--border);
}}
header img {{ border-radius: 14px; flex-shrink: 0; }}
h1 {{ margin: 0 0 4px; font-size: 1.45rem; }}
.sub {{ margin: 0; color: var(--muted); font-size: 0.92rem; }}
.pills {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }}
.pill {{
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 2px 10px;
  font-size: 0.78rem;
  color: var(--cyan);
}}
.tabs {{
  display: flex;
  gap: 8px;
  margin-bottom: 14px;
}}
.tab-btn {{
  appearance: none;
  border: 1px solid var(--border);
  background: var(--panel);
  color: var(--muted);
  padding: 8px 16px;
  border-radius: 8px;
  font: inherit;
  font-size: 0.92rem;
  font-weight: 600;
  cursor: pointer;
}}
.tab-btn[aria-selected="true"] {{
  background: #1f2d27;
  color: #7ee787;
  border-color: var(--accent);
}}
.panel {{ display: none; }}
.panel.active {{ display: block; }}
.grid-2 {{
  display: grid;
  grid-template-columns: 1.15fr 1fr;
  gap: 14px;
}}
.card {{
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 14px 16px;
}}
.card h2 {{
  margin: 0 0 8px;
  font-size: 1.02rem;
  color: #f0f6fc;
}}
.card p {{ margin: 0 0 10px; color: var(--muted); font-size: 0.86rem; }}
pre {{
  margin: 0;
  background: var(--code-bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.78rem;
  line-height: 1.45;
  overflow-x: auto;
  color: #c9d1d9;
  white-space: pre-wrap;
  word-break: break-all;
}}
.gallery {{
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}}
.asset-tile {{
  display: flex;
  align-items: center;
  gap: 8px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 6px 8px;
  min-width: 0;
}}
.asset-tile img {{ border-radius: 8px; flex-shrink: 0; background: #0d1117; }}
.asset-meta {{ min-width: 0; }}
.asset-id {{
  font-size: 0.75rem;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}}
.asset-sub {{ font-size: 0.7rem; color: var(--muted); font-family: ui-monospace, monospace; }}
.derive-grid {{
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}}
.derive-card {{
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 12px;
  text-align: center;
}}
.preview-box {{
  height: 112px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  border-radius: 8px;
  margin-bottom: 10px;
  background-image:
    linear-gradient(45deg, #21262d 25%, transparent 25%),
    linear-gradient(-45deg, #21262d 25%, transparent 25%),
    linear-gradient(45deg, transparent 75%, #21262d 75%),
    linear-gradient(-45deg, transparent 75%, #21262d 75%);
  background-size: 16px 16px;
  background-position: 0 0, 0 8px, 8px -8px, -8px 0;
  background-color: #161b22;
}}
.derive-spec {{
  font-family: ui-monospace, monospace;
  font-size: 0.82rem;
  color: var(--cyan);
  font-weight: 700;
  margin-bottom: 4px;
}}
.derive-note {{ font-size: 0.76rem; color: var(--muted); }}
.ico-badge {{
  display: inline-flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  font-size: 0.7rem;
  color: var(--muted);
}}
.cache-row {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-top: 12px;
}}
.file-list {{
  list-style: none;
  margin: 0;
  padding: 8px 10px;
  background: var(--code-bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  font-family: ui-monospace, monospace;
  font-size: 0.78rem;
}}
.file-list li {{ padding: 2px 0; }}
.keep-tag {{ color: #7ee787; }}
.user-tag {{ color: #d2a8ff; }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <img src="{icon_uri}" width="64" height="64" alt="CaChereCyCle icon">
    <div>
      <h1>♻️ CaChereCyCle — 動作デモ</h1>
      <p class="sub">1 つの JSONL 保管庫（store.jsonl）に資産を集約し、使う分だけサイズ展開・同期して不要分を片付ける。</p>
      <div class="pills">
        <span class="pill">🏛 store.jsonl: {total_assets} 件 ({store_kb} KiB)</span>
        <span class="pill">🔑 SHA-256 照合</span>
        <span class="pill">🧰 Python 3.11+ 標準ライブラリのみ</span>
      </div>
    </div>
  </header>

  <div class="tabs" role="tablist">
    <button class="tab-btn" id="tab-store" role="tab" aria-selected="true" data-target="panel-store">📥 put &amp; 📋 ls（保管庫）</button>
    <button class="tab-btn" id="tab-derive" role="tab" aria-selected="false" data-target="panel-derive">📐 derive（サイズ展開 / .ico）</button>
    <button class="tab-btn" id="tab-sync" role="tab" aria-selected="false" data-target="panel-sync">⚡ sync（同期と掃除）</button>
  </div>

  <section id="panel-store" class="panel active">
    <div class="grid-2">
      <div class="card" id="step-put">
        <h2>📥 put → ♻️ 重複スキップ → 📋 ls</h2>
        <p>同じバイト列の再登録ではストアを書き換えません。取り出し時は SHA-256 で照合します。</p>
        <pre>{html.escape(put_cli_text)}</pre>
      </div>
      <div class="card" id="step-gallery">
        <h2>🏛 store.jsonl 内の各リポジトリ資産（抜粋）</h2>
        <p>全 {total_assets} 件から代表 {len(gallery_cards)} 件をデコード表示。各リポジトリは ID のみを参照します。</p>
        <div class="gallery">{"".join(gallery_cards)}</div>
      </div>
    </div>
  </section>

  <section id="panel-derive" class="panel">
    <div class="card" id="step-derive">
      <h2>📐 1 枚の PNG からサイズ別・用途別をオンデマンド生成</h2>
      <p>サイズ別の複製は store.jsonl に保存せず、sync / get の時点でブロック平均縮小・透過充填（:opaque）・.ico 化を行います。</p>
      <div class="derive-grid">
        <div class="derive-card">
          <div class="preview-box"><img src="{_data_uri('image/png', raw_icon)}" width="88" height="88" alt="source"></div>
          <div class="derive-spec">CaChereCyCle.icon</div>
          <div class="derive-note">保管原本 512×512<br>{len(raw_icon):,} B · 透過あり</div>
        </div>
        <div class="derive-card">
          <div class="preview-box"><img src="{_data_uri('image/png', png_192)}" width="76" height="76" alt="192"></div>
          <div class="derive-spec">@192</div>
          <div class="derive-note">192×192 正方形 PNG<br>{len(png_192):,} B · 透過保持</div>
        </div>
        <div class="derive-card">
          <div class="preview-box"><img src="{_data_uri('image/png', png_180_op)}" width="72" height="72" alt="180 opaque"></div>
          <div class="derive-spec">@180:opaque</div>
          <div class="derive-note">180×180 不透明 PNG<br>{len(png_180_op):,} B · ホーム画面用</div>
        </div>
        <div class="derive-card">
          <div class="preview-box">{ico_imgs}</div>
          <div class="derive-spec">@16,32,48</div>
          <div class="derive-note">マルチサイズ .ico<br>{len(ico_bytes):,} B · 3 サイズ格納</div>
        </div>
      </div>
    </div>
  </section>

  <section id="panel-sync" class="panel">
    <div class="card" id="step-sync">
      <h2>⚡ sync: ✍️ 書き出し → ✅ 変更なし保持 → 🧹 不要分だけ掃除</h2>
      <p>sync は自身が書いたファイルだけを .cacherecycle.json で管理し、手元の既存ファイルには一切触れません。</p>
      <pre>{html.escape(sync_cli_text)}</pre>
      <div class="cache-row">
        <div>
          <p style="margin-bottom:4px;"><strong>Step 1 後の .cache/assets/</strong></p>
          <ul class="file-list">{"".join(f'<li class="{"user-tag" if f == "README.local.txt" else "keep-tag"}">{"📄 " + html.escape(f) + " (user file)" if f == "README.local.txt" else "📦 " + html.escape(f)}</li>' for f in files_1)}</ul>
        </div>
        <div>
          <p style="margin-bottom:4px;"><strong>Step 3 後（CaChereCyCle.icon 削除後）</strong></p>
          <ul class="file-list">{"".join(f'<li class="{"user-tag" if f == "README.local.txt" else "keep-tag"}">{"📄 " + html.escape(f) + " (untouched)" if f == "README.local.txt" else "📦 " + html.escape(f)}</li>' for f in files_3)}</ul>
        </div>
      </div>
    </div>
  </section>
</div>
<script>
for (const btn of document.querySelectorAll('.tab-btn')) {{
  btn.addEventListener('click', () => {{
    for (const b of document.querySelectorAll('.tab-btn')) b.setAttribute('aria-selected', String(b === btn));
    for (const p of document.querySelectorAll('.panel')) p.classList.toggle('active', p.id === btn.dataset.target);
  }});
}}
</script>
</body>
</html>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="Output HTML file path")
    args = parser.parse_args(argv)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(build_demo_html(), encoding="utf-8")
    print(f"✍️ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
