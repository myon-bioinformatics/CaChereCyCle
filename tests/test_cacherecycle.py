"""Tests for cacherecycle.py (standard library only: python -S -m unittest discover -s tests)."""
from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cacherecycle as ccr  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(64))


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.store = self.dir / "store.jsonl"
        self.cache = self.dir / "cache"

    def tearDown(self) -> None:
        for path in sorted(self.dir.rglob("*"), reverse=True):
            path.rmdir() if path.is_dir() else path.unlink()
        self.dir.rmdir()

    def file(self, name: str, data: bytes = PNG) -> Path:
        path = self.dir / name
        path.write_bytes(data)
        return path

    def cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ccr.main(["--store", str(self.store), *argv])
        return code, out.getvalue(), err.getvalue()


class StoreTests(Base):
    def test_put_then_get_returns_the_same_bytes_and_the_original_can_go(self):
        source = self.file("icon.png")
        meta = ccr.put(source, "portfolio.icon", store=self.store)
        source.unlink()
        self.assertEqual(ccr.get("portfolio.icon", store=self.store), PNG)
        self.assertEqual((meta["mime"], meta["bytes"], meta["changed"]), ("image/png", len(PNG), True))

    def test_putting_the_same_bytes_again_leaves_the_store_untouched(self):
        source = self.file("icon.png")
        ccr.put(source, "a.icon", store=self.store)
        before = self.store.read_bytes()
        self.assertFalse(ccr.put(source, "a.icon", store=self.store)["changed"])
        self.assertEqual(self.store.read_bytes(), before)

    def test_store_is_one_compact_lf_line_per_asset_sorted_by_id(self):
        ccr.put(self.file("b.png"), "b.icon", store=self.store)
        ccr.put(self.file("a.svg", b"<svg/>"), "a.diagram", store=self.store)
        raw = self.store.read_bytes()
        self.assertNotIn(b"\r", raw)
        rows = [json.loads(line) for line in raw.decode("utf-8").splitlines()]
        self.assertEqual([row["id"] for row in rows], ["a.diagram", "b.icon"])
        self.assertEqual(list(rows[0]), ["id", "sha256", "mime", "b64"])
        self.assertEqual(rows[0]["mime"], "image/svg+xml")

    def test_replacing_an_id_updates_its_hash(self):
        ccr.put(self.file("a.png"), "a.icon", store=self.store)
        old = ccr.entry("a.icon", store=self.store)["sha256"]
        meta = ccr.put(self.file("a.png", PNG + b"!"), "a.icon", store=self.store)
        self.assertTrue(meta["changed"])
        self.assertNotEqual(meta["sha256"], old)
        self.assertEqual(len(ccr.ls(store=self.store)), 1)

    def test_ids_must_be_meaningful_and_safe_as_file_names(self):
        source = self.file("a.png")
        for bad in ("", "../x", "a/b", "a\\b", ".a", "a..b", "a.", "a b", "-a", None, 5):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                ccr.put(source, bad, store=self.store)

    def test_max_bytes_keeps_big_media_out_unless_lifted(self):
        source = self.file("demo.gif", b"GIF89a" + b"\0" * 64)
        with self.assertRaises(ValueError):
            ccr.put(source, "demo.gif", store=self.store, max_bytes=8)
        self.assertEqual(ccr.put(source, "demo.gif", store=self.store, max_bytes=None)["mime"], "image/gif")

    def test_a_damaged_line_is_detected_by_its_hash(self):
        ccr.put(self.file("a.png"), "a.icon", store=self.store)
        row = json.loads(self.store.read_text(encoding="utf-8"))
        row["b64"] = base64.b64encode(PNG + b"x").decode("ascii")
        self.store.write_text(json.dumps(row) + "\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            ccr.get("a.icon", store=self.store)

    def test_duplicate_ids_in_the_store_are_rejected(self):
        ccr.put(self.file("a.png"), "a.icon", store=self.store)
        line = self.store.read_text(encoding="utf-8")
        self.store.write_text(line + line, encoding="utf-8")
        with self.assertRaises(ValueError):
            ccr.ls(store=self.store)

    def test_ids_keep_the_repository_name_as_written_and_differ_by_more_than_case(self):
        ccr.put(self.file("a.png"), "Err-o-RoadRunner.icon", store=self.store)
        ccr.put(self.file("b.png"), "myon-bioinformatics.github.io.favicon", store=self.store)
        ccr.put(self.file("a.png"), "aoi.icon", store=self.store)
        self.assertEqual([m["id"] for m in ccr.ls(store=self.store)],           # 🔤 sorted ignoring case
                         ["aoi.icon", "Err-o-RoadRunner.icon", "myon-bioinformatics.github.io.favicon"])
        with self.assertRaises(ValueError):
            ccr.put(self.file("b.png"), "err-o-roadrunner.icon", store=self.store)
        line = self.store.read_text(encoding="utf-8").splitlines()[1]
        self.store.write_text(self.store.read_text(encoding="utf-8") + line.replace("Err-o-RoadRunner", "ERR-O-ROADRUNNER") + "\n",
                              encoding="utf-8")
        with self.assertRaises(ValueError):
            ccr.ls(store=self.store)

    def test_the_fleet_store_names_each_repository_as_written(self):
        repos = {m["id"].rsplit(".", 1)[0].split(".")[0] for m in ccr.ls()} - {"myon-bioinformatics"}
        self.assertIn("Err-o-RoadRunner", repos)
        self.assertFalse({"err-o-roadrunner", "ontautology", "browsertestkit", "myon-bioinformatics-github-io"} & repos)

    def test_an_unknown_id_suggests_the_nearest_one(self):
        ccr.put(self.file("a.png"), "portfolio.favicon", store=self.store)
        with self.assertRaises(KeyError) as caught:
            ccr.get("portfolio.favicom", store=self.store)
        self.assertIn("portfolio.favicon", caught.exception.args[0])

    def test_drop_removes_only_that_id(self):
        ccr.put(self.file("a.png"), "a.icon", store=self.store)
        ccr.put(self.file("b.png"), "b.icon", store=self.store)
        self.assertEqual(ccr.drop("a.icon", store=self.store)["id"], "a.icon")
        self.assertEqual([meta["id"] for meta in ccr.ls(store=self.store)], ["b.icon"])

    def test_a_missing_store_is_empty(self):
        self.assertEqual(ccr.ls(store=self.store), [])
        self.assertFalse(self.store.exists())


class SyncTests(Base):
    def test_sync_writes_keeps_and_removes_only_its_own_files(self):
        ccr.put(self.file("a.png"), "a.icon", store=self.store)
        ccr.put(self.file("b.svg", b"<svg/>"), "b.diagram", store=self.store)
        self.cache.mkdir()
        (self.cache / "mine.txt").write_text("not written by sync", encoding="utf-8")
        first = ccr.sync(["a.icon", "b.diagram", "a.icon"], store=self.store, cache=self.cache)
        self.assertEqual(sorted(Path(p).name for p in first["written"]), ["a.icon.png", "b.diagram.svg"])
        self.assertEqual((self.cache / "a.icon.png").read_bytes(), PNG)
        second = ccr.sync(["a.icon"], store=self.store, cache=self.cache)
        self.assertEqual([Path(p).name for p in second["kept"]], ["a.icon.png"])
        self.assertEqual([Path(p).name for p in second["removed"]], ["b.diagram.svg"])
        self.assertTrue((self.cache / "mine.txt").exists())

    def test_a_stale_cache_file_is_rewritten(self):
        ccr.put(self.file("a.png"), "a.icon", store=self.store)
        ccr.sync(["a.icon"], store=self.store, cache=self.cache)
        (self.cache / "a.icon.png").write_bytes(b"old")
        report = ccr.sync(["a.icon"], store=self.store, cache=self.cache)
        self.assertEqual(len(report["written"]), 1)
        self.assertEqual((self.cache / "a.icon.png").read_bytes(), PNG)

    def test_a_tampered_index_cannot_reach_outside_the_cache(self):
        outside = self.file("keep.png")
        self.cache.mkdir()
        (self.cache / ".cacherecycle.json").write_text(json.dumps({"../keep.png": {}, "..": {}}), encoding="utf-8")
        ccr.sync([], store=self.store, cache=self.cache)
        self.assertTrue(outside.exists())


def png(width: int, height: int, pixels: list[tuple[int, int, int, int]], filters: tuple[int, ...] = (0,)) -> bytes:
    """A test PNG (RGBA, 8 bit) whose rows cycle through the given filter types, so the decoder meets all five."""
    stride, raw, previous = width * 4, b"", bytes(width * 4)
    for y in range(height):
        line = bytes(v for p in pixels[y * width:(y + 1) * width] for v in p)
        kind, out = filters[y % len(filters)], bytearray()
        for i, v in enumerate(line):
            a, b, c = (line[i - 4] if i >= 4 else 0), previous[i], (previous[i - 4] if i >= 4 else 0)
            p = a + b - c
            paeth = a if abs(p - a) <= abs(p - b) and abs(p - a) <= abs(p - c) else b if abs(p - b) <= abs(p - c) else c
            out.append((v - (0, a, b, (a + b) >> 1, paeth)[kind]) & 255)
        raw += bytes((kind,)) + bytes(out)
        previous = line
    return ccr._png_encode(1, 1, b"\0" * 4)[:8] + b"".join(
        struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
        for kind, body in ((b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)),
                           (b"IDAT", zlib.compress(raw)), (b"IEND", b"")))


RED, BLUE, CLEAR = (255, 0, 0, 255), (0, 0, 255, 255), (0, 0, 0, 0)


class SizeTests(Base):
    def test_the_decoder_reads_every_filter_type(self):
        pixels = [(x * 40, y * 50, (x * y) % 256, 255 - x) for y in range(5) for x in range(6)]
        width, height, rgba = ccr._png_decode(png(6, 5, pixels, filters=(0, 1, 2, 3, 4)))
        self.assertEqual((width, height, rgba), (6, 5, bytes(v for p in pixels for v in p)))

    def test_growing_pixel_art_stays_crisp(self):
        out = ccr._png_decode(ccr.resize(png(2, 1, [RED, BLUE]), 4))
        self.assertEqual(out[2][:16], bytes(RED * 2 + BLUE * 2))
        self.assertEqual(out[2][4 * 4 * 3:], bytes(CLEAR * 4))  # the 2:1 image is centred on a square

    def test_shrinking_averages_and_keeps_transparency(self):
        _, _, rgba = ccr._png_decode(ccr.resize(png(2, 2, [RED, CLEAR, CLEAR, CLEAR]), 1))
        self.assertEqual(rgba, bytes((255, 0, 0, 64)))

    def test_opaque_flattens_onto_the_top_left_colour_or_white(self):
        _, _, rgba = ccr._png_decode(ccr.resize(png(2, 1, [BLUE, CLEAR]), 2, opaque=True))
        self.assertEqual(set(rgba[3::4]), {255})
        self.assertEqual(rgba[:4], bytes(BLUE))
        _, _, white = ccr._png_decode(ccr.resize(png(1, 1, [CLEAR]), 1, opaque=True))
        self.assertEqual(white, b"\xff\xff\xff\xff")

    def test_ico_holds_each_size_as_png(self):
        data = ccr.ico([ccr.resize(png(1, 1, [RED]), s) for s in (16, 32, 256)])
        self.assertEqual(struct.unpack("<HHH", data[:6]), (0, 1, 3))
        sizes = [struct.unpack("<BBBBHHII", data[6 + 16 * i:22 + 16 * i])[0] for i in range(3)]
        self.assertEqual(sizes, [16, 32, 0])
        with self.assertRaises(ValueError):
            ccr.ico([b"not a png"])

    def test_specs(self):
        ccr.put(self.file("a.png", png(2, 2, [RED] * 4)), "a.icon", store=self.store)
        self.assertEqual(ccr.derive("a.icon", store=self.store), ccr.get("a.icon", store=self.store))
        self.assertEqual(ccr._png_decode(ccr.derive("a.icon@8", store=self.store))[:2], (8, 8))
        self.assertEqual(ccr.derive("a.icon@16,32", store=self.store)[:4], b"\0\0\1\0")
        for bad in ("a.icon@", "a.icon@0", "a.icon@8:dark", "a.icon:opaque", "a.icon@8,", "a.icon@99999"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                ccr.derive(bad, store=self.store)

    def test_sizes_need_a_png_source(self):
        ccr.put(self.file("a.svg", b"<svg/>"), "a.diagram", store=self.store)
        with self.assertRaises(ValueError):
            ccr.derive("a.diagram@16", store=self.store)

    def test_sync_names_variants_and_keeps_them_while_nothing_changes(self):
        ccr.put(self.file("a.png", png(2, 2, [RED] * 4)), "a.icon", store=self.store)
        items = ["a.icon@16,32", "a.icon@180:opaque", "icon-192.png=a.icon@192"]
        first = ccr.sync(items, store=self.store, cache=self.cache)
        self.assertEqual(sorted(Path(p).name for p in first["written"]),
                         ["a.icon.16-32.ico", "a.icon.180-opaque.png", "icon-192.png"])
        self.assertEqual(len(ccr.sync(items, store=self.store, cache=self.cache)["kept"]), 3)
        ccr.put(self.file("a.png", png(2, 2, [BLUE] * 4)), "a.icon", store=self.store)
        self.assertEqual(len(ccr.sync(items, store=self.store, cache=self.cache)["written"]), 3)

    def test_sync_names_must_be_plain_and_unique(self):
        ccr.put(self.file("a.png"), "a.icon", store=self.store)
        for items in (["../x.png=a.icon"], ["sub/x.png=a.icon"], [".hidden=a.icon"], ["x.png=a.icon", "x.png=a.icon@8"]):
            with self.subTest(items=items), self.assertRaises(ValueError):
                ccr.sync(items, store=self.store, cache=self.cache)


class CommittedStoreTests(unittest.TestCase):
    def test_every_committed_entry_matches_its_hash_and_every_png_can_be_sized(self):
        lines = ccr.STORE.read_text(encoding="utf-8").splitlines() if ccr.STORE.exists() else []
        ids = [json.loads(line)["id"] for line in lines]
        self.assertEqual(ids, sorted(set(ids), key=str.casefold))
        for meta in ccr.ls():
            with self.subTest(id=meta["id"]):
                raw = ccr.get(meta["id"])
                if meta["mime"] == "image/png":
                    ccr._png_decode(raw)


class GuideRunTests(unittest.TestCase):
    """📘 The ▶️ 実行 outputs in docs/guide.page.json are real: each ``$ python -S cacherecycle.py …`` runs, in order, in an
    empty folder holding only cacherecycle.py, icon.png and favicon.png (this repository's own), and prints what the guide shows."""

    def test_guide_outputs_are_what_the_cli_prints(self):
        page = json.loads((ROOT / "docs/guide.page.json").read_text(encoding="utf-8"))
        tabs = next(block for block in page["blocks"] if block.get("type") == "tabs")["tabs"]
        run = next(block for tab in tabs for block in tab["blocks"] if block.get("title") == "▶️ 実行")
        steps, prompt = [], "$ python -S cacherecycle.py "
        for card in run["blocks"]:
            for block in card.get("blocks", ()):
                for line in block["text"].splitlines() if block.get("type") == "output" else ():
                    if line.startswith(prompt):
                        steps.append((line[len(prompt):].split(), []))
                    else:
                        steps[-1][1].append(line)
        self.assertGreaterEqual(len(steps), 9)
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        with tempfile.TemporaryDirectory() as folder:
            here = Path(folder)
            (here / "cacherecycle.py").write_bytes((ROOT / "cacherecycle.py").read_bytes())
            (here / "icon.png").write_bytes(ccr.get("CaChereCyCle.icon"))
            (here / "favicon.png").write_bytes(ccr.get("CaChereCyCle.favicon"))
            for argv, expected in steps:
                with self.subTest(command=" ".join(argv)):
                    done = subprocess.run([sys.executable, "-S", "cacherecycle.py", *argv], cwd=here, capture_output=True, env=env)
                    printed = (done.stdout + done.stderr).decode("utf-8").replace("\\", "/").strip().splitlines()
                    self.assertEqual(printed, expected)


class InterfaceTests(Base):
    def test_every_result_is_json_ready_for_node_callers(self):
        ccr.put(self.file("a.png"), "a.icon", store=self.store)
        for result in (ccr.put(self.file("a.png"), "a.icon", store=str(self.store)), ccr.entry("a.icon", store=str(self.store)),
                       ccr.ls(store=str(self.store)), ccr.sync(["a.icon"], store=str(self.store), cache=str(self.cache)),
                       ccr.drop("a.icon", store=str(self.store))):
            json.dumps(result)

    def test_cli_round_trip(self):
        source = self.file("a.png")
        self.assertEqual(self.cli("put", str(source), "--id", "a.icon")[0], 0)
        self.assertIn("♻️", self.cli("put", str(source), "--id", "a.icon")[1])
        self.assertIn("🧊 a.icon", self.cli("ls")[1])
        out = self.dir / "out.png"
        self.assertEqual(self.cli("get", "a.icon", "--out", str(out))[0], 0)
        self.assertEqual(out.read_bytes(), PNG)
        self.assertIn("✍️", self.cli("sync", "a.icon", "--cache", str(self.cache))[1])
        self.assertIn("🗑 a.icon", self.cli("drop", "a.icon")[1])
        self.assertIn("🫙", self.cli("ls")[1])

    def test_cli_errors_are_one_line_and_exit_1(self):
        code, _, err = self.cli("get", "nope", "--out", str(self.dir / "x"))
        self.assertEqual((code, err.count("\n")), (1, 1))
        self.assertTrue(err.startswith("❌"))

    def test_cli_runs_without_site_packages(self):
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        done = subprocess.run([sys.executable, "-S", str(ROOT / "cacherecycle.py"), "--store", str(self.store), "ls"],
                              capture_output=True, text=True, encoding="utf-8", env=env, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("🫙", done.stdout)


if __name__ == "__main__":
    unittest.main()
