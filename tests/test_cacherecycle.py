"""Tests for cacherecycle.py (standard library only: python -S -m unittest discover -s tests)."""
from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
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
        for bad in ("", "A", "../x", "a/b", "a\\b", ".a", "a..b", "a.", "a b", None, 5):
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
        (self.cache / ".cacherecycle.json").write_text(json.dumps(["../keep.png"]), encoding="utf-8")
        ccr.sync([], store=self.store, cache=self.cache)
        self.assertTrue(outside.exists())


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
