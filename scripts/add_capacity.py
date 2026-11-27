"""Add the file-size calculator to the existing rendered usage guide."""
from pathlib import Path
import sys


def add_capacity(page):
    html = page.read_text(encoding="utf-8")
    if 'id="capacity"' in html:
        return
    section = '''<section id="capacity"><h2>Base64容量</h2>
<label>任意のファイルを選ぶ <input type="file" id="capacity-file"></label>
<p role="status" id="capacity-result">ファイル本体を読み込まず、サイズから計算します。</p>
</section><script defer src="./capacity.js"></script>'''
    if "</body>" not in html:
        raise ValueError("guide has no closing body")
    page.write_text(html.replace("</body>", section + "</body>", 1), encoding="utf-8")


if __name__ == "__main__":
    add_capacity(Path(sys.argv[1]))
