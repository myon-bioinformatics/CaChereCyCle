"""♻️ CaChereCyCle — one JSONL store for small assets (icons, favicons, diagrams) kept as Base64.

🏛 store.jsonl  one line per asset, sorted by id: {"id", "sha256", "mime", "b64"}. The store is the primary
                copy: once a file is put, its original can be deleted.
🏷 consumers    keep only ids in their own JSON (e.g. "assets": ["portfolio.favicon"]).
⚡ sync(ids)    writes those ids into a git-ignored cache as ``<id><ext>`` and removes the files it wrote
                earlier that are no longer wanted (files it did not write are never touched).

Python: put / get / entry / ls / drop / sync, all callable by name (Node: py_launch.cjs ``call()``).
CLI:    python cacherecycle.py {put,get,ls,drop,sync} ...    Standard library only, Python 3.11+.
"""
from __future__ import annotations

import argparse
import base64
import difflib
import hashlib
import json
import mimetypes
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Iterable

__all__ = ["put", "get", "entry", "ls", "drop", "sync", "main"]
__version__ = "0.1.0"

STORE = Path(__file__).with_name("store.jsonl")
CACHE = Path(".cache") / "assets"
_ID = re.compile(r"[a-z0-9][a-z0-9_-]*(?:\.[a-z0-9_-]+)*\Z")
_MIME = mimetypes.MimeTypes()  # a fresh table: the same answer on every OS (no Windows registry)


def _id(value: object) -> str:
    if not isinstance(value, str) or not _ID.match(value):
        raise ValueError(f"id must look like 'portfolio.favicon' (a-z 0-9 _ - and single dots): {value!r}")
    return value


def _load(store: str | os.PathLike[str]) -> dict[str, dict[str, str]]:
    path = Path(store)
    if not path.exists():
        return {}
    rows: dict[str, dict[str, str]] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            row = json.loads(line)
            if row.get("id") in rows:
                raise ValueError(f"{path}:{number}: duplicate id {row['id']!r}")
            rows[_id(row.get("id"))] = row
    return rows


def _save(store: str | os.PathLike[str], rows: dict[str, dict[str, str]]) -> None:
    path = Path(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(rows[key], ensure_ascii=False, separators=(",", ":")) + "\n" for key in sorted(rows))
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    os.replace(tmp, path)


def _row(rows: dict[str, dict[str, str]], id: str) -> dict[str, str]:
    row = rows.get(_id(id))
    if row is None:
        near = difflib.get_close_matches(id, rows, 1)
        raise KeyError(f"unknown id {id!r}" + (f" (did you mean {near[0]!r}?)" if near else ""))
    if hashlib.sha256(base64.b64decode(row["b64"])).hexdigest() != row["sha256"]:
        raise ValueError(f"{id}: sha256 does not match the stored bytes (the store is damaged)")
    return row


def _meta(row: dict[str, str]) -> dict[str, object]:
    return {"id": row["id"], "sha256": row["sha256"], "mime": row["mime"], "bytes": len(base64.b64decode(row["b64"]))}


def put(path: str | os.PathLike[str], id: str, *, mime: str | None = None,
        store: str | os.PathLike[str] = STORE, max_bytes: int | None = 1 << 20) -> dict[str, object]:
    """📥 Store the file at ``path`` under ``id`` (replacing an older copy); return its entry without the Base64.

    The same bytes leave the store untouched (``changed`` is False). ``max_bytes`` (None = no limit) keeps big
    media such as GIF demos out: those stay as files. ``mime`` defaults to a guess from the file name."""
    _id(id)
    raw = Path(path).read_bytes()
    if max_bytes is not None and len(raw) > max_bytes:
        raise ValueError(f"{path}: {len(raw)} bytes > max_bytes {max_bytes} (keep big media as files)")
    guess = getattr(_MIME, "guess_file_type", _MIME.guess_type)(os.fspath(path))[0]
    row = {"id": id, "sha256": hashlib.sha256(raw).hexdigest(), "mime": mime or guess or "application/octet-stream",
           "b64": base64.b64encode(raw).decode("ascii")}
    rows = _load(store)
    changed = rows.get(id) != row
    if changed:
        rows[id] = row
        _save(store, rows)
    return {**_meta(row), "changed": changed}


def entry(id: str, *, store: str | os.PathLike[str] = STORE) -> dict[str, str]:
    """🔎 The stored line for ``id`` (with its Base64), checked against its SHA-256. JSON-ready for Node."""
    return dict(_row(_load(store), id))


def get(id: str, *, store: str | os.PathLike[str] = STORE) -> bytes:
    """📤 The bytes stored under ``id``, checked against their SHA-256."""
    return base64.b64decode(entry(id, store=store)["b64"])


def ls(*, store: str | os.PathLike[str] = STORE) -> list[dict[str, object]]:
    """📋 Every entry without its Base64, sorted by id."""
    return [_meta(row) for _, row in sorted(_load(store).items())]


def drop(id: str, *, store: str | os.PathLike[str] = STORE) -> dict[str, object]:
    """🗑 Remove ``id`` from the store; return what was removed (without the Base64)."""
    rows = _load(store)
    row = _row(rows, id)
    del rows[id]
    _save(store, rows)
    return _meta(row)


def sync(wanted: Iterable[str], *, store: str | os.PathLike[str] = STORE,
         cache: str | os.PathLike[str] = CACHE) -> dict[str, list[str]]:
    """⚡ Make ``cache`` hold the ``wanted`` ids as ``<id><ext>`` files.

    Missing or stale files are written, matching ones kept, and files this function wrote before that are no
    longer wanted are removed. Its own files are listed in ``cache/.cacherecycle.json``; nothing else is touched.
    Returns {"written", "kept", "removed"} as POSIX paths."""
    rows = _load(store)
    folder = Path(cache)
    folder.mkdir(parents=True, exist_ok=True)
    index = folder / ".cacherecycle.json"
    before = set(json.loads(index.read_text(encoding="utf-8"))) if index.exists() else set()
    report: dict[str, list[str]] = {"written": [], "kept": [], "removed": []}
    names: list[str] = []
    for id in dict.fromkeys(wanted):
        row = _row(rows, id)
        name = id + (_MIME.guess_extension(row["mime"]) or "")
        target = folder / name
        names.append(name)
        if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == row["sha256"]:
            report["kept"].append(target.as_posix())
        else:
            target.write_bytes(base64.b64decode(row["b64"]))
            report["written"].append(target.as_posix())
    for name in sorted(before - set(names)):
        old = folder / name
        if Path(name).name == name and _ID.match(Path(name).stem) and old.is_file():
            old.unlink()
            report["removed"].append(old.as_posix())
    index.write_text(json.dumps(sorted(names)) + "\n", encoding="utf-8")
    return report


def _line(meta: dict[str, object], icon: str) -> str:
    return f"{icon} {meta['id']}  {meta['mime']}  {meta['bytes']} B  {str(meta['sha256'])[:8]}"


def main(argv: list[str] | None = None) -> int:
    """⌨️ The CLI: put / get / ls / drop / sync. Returns the exit code (0 ok, 1 error)."""
    parser = argparse.ArgumentParser(prog="cacherecycle", description="♻️ one JSONL store for small assets (Base64)")
    parser.suggest_on_error = True
    parser.add_argument("--store", default=STORE, help="🏛 store file (default: store.jsonl next to this script)")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("put", help="📥 store a file under an id")
    p.add_argument("file")
    p.add_argument("--id", required=True, help="🏷 meaningful id, e.g. portfolio.favicon")
    p.add_argument("--mime", help="default: guessed from the file name")
    p.add_argument("--max-bytes", type=int, default=1 << 20, help="📏 size limit in bytes; 0 = no limit (default 1048576)")
    p = sub.add_parser("get", help="📤 write the stored bytes to a file")
    p.add_argument("id")
    p.add_argument("--out", required=True, help="output file, or - for stdout")
    sub.add_parser("ls", help="📋 list entries")
    p = sub.add_parser("drop", help="🗑 remove an entry")
    p.add_argument("id")
    p = sub.add_parser("sync", help="⚡ write the wanted ids into the cache and tidy it")
    p.add_argument("ids", nargs="+")
    p.add_argument("--cache", default=CACHE, help="⚡ cache folder (default: .cache/assets)")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")  # emoji on a cp1252 console must not crash
    try:
        if args.command == "put":
            meta = put(args.file, args.id, mime=args.mime, store=args.store, max_bytes=args.max_bytes or None)
            print(_line(meta, "📥" if meta["changed"] else "♻️"))
        elif args.command == "get":
            data = get(args.id, store=args.store)
            if args.out == "-":
                sys.stdout.buffer.write(data)
            else:
                Path(args.out).write_bytes(data)
                print(f"📤 {args.id} → {args.out}")
        elif args.command == "ls":
            rows = ls(store=args.store)
            print("\n".join(_line(meta, "🧊") for meta in rows) if rows else "🫙 (empty)")
        elif args.command == "drop":
            print(_line(drop(args.id, store=args.store), "🗑"))
        else:
            report = sync(args.ids, store=args.store, cache=args.cache)
            for key, icon in (("written", "✍️"), ("kept", "✅"), ("removed", "🧹")):
                for path in report[key]:
                    print(f"{icon} {path}")
    except (KeyError, ValueError, OSError) as error:
        print(f"❌ {error.args[0] if isinstance(error, KeyError) else error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
