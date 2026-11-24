"""♻️ CaChereCyCle — one JSONL store for small assets (icons, favicons, diagrams) kept as Base64.

🏛 store.jsonl  one line per asset, sorted by id: {"id", "sha256", "mime", "b64"}. The store is the primary
                copy: once a file is put, its original can be deleted.
🏷 consumers    keep only ids in their own JSON (e.g. "assets": ["OnTautoLogy.favicon"]).
📐 variants     sizes are made on demand, never stored: ``ID@192`` (alpha kept), ``ID@180:opaque`` (home screen),
                ``ID@16,32,48`` (one .ico holding those sizes).
⚡ sync(items)  writes ``ID`` / ``ID@…`` or ``NAME=ID@…`` into a git-ignored cache and removes the files it wrote
                earlier that are no longer wanted (files it did not write are never touched).

Python: put / get / entry / ls / drop / derive / resize / ico / sync, callable by name (Node: py_launch.cjs ``call()``).
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
import struct
import sys
import tempfile
import zlib
from pathlib import Path
from typing import Iterable

__all__ = ["put", "get", "entry", "ls", "drop", "derive", "resize", "ico", "sync", "main"]
__version__ = "0.2.0"

STORE = Path(__file__).with_name("store.jsonl")
CACHE = Path(".cache") / "assets"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)*\Z")  # <Repository name>.<what>
_SPEC = re.compile(r"(?P<id>[^@:=]+)(?:@(?P<sizes>[1-9][0-9]*(?:,[1-9][0-9]*)*)(?P<opaque>:opaque)?)?\Z")
_NAME = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]*\Z")
_MIME = mimetypes.MimeTypes()  # a fresh table: the same answer on every OS (no Windows registry)
_PNG = b"\x89PNG\r\n\x1a\n"


def _id(value: object) -> str:
    if not isinstance(value, str) or not _ID.match(value):
        raise ValueError(f"id must look like 'Err-o-RoadRunner.icon' — the repository name as written, a dot and "
                         f"what it is (A-Z a-z 0-9 _ - and single dots): {value!r}")
    return value


def _load(store: str | os.PathLike[str]) -> dict[str, dict[str, str]]:
    path = Path(store)
    if not path.exists():
        return {}
    rows: dict[str, dict[str, str]] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            row = json.loads(line)
            same = [id for id in rows if id.casefold() == str(row.get("id")).casefold()]
            if same:
                raise ValueError(f"{path}:{number}: id {row['id']!r} repeats {same[0]!r} (ids differ by more than case)")
            rows[_id(row.get("id"))] = row
    return rows


def _save(store: str | os.PathLike[str], rows: dict[str, dict[str, str]]) -> None:
    path = Path(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(rows[key], ensure_ascii=False, separators=(",", ":")) + "\n" for key in sorted(rows, key=str.casefold))
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
    same = [old for old in rows if old != id and old.casefold() == id.casefold()]
    if same:
        raise ValueError(f"id {id!r} differs from {same[0]!r} only by case (one file per name on Windows and macOS)")
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
    return [_meta(row) for _, row in sorted(_load(store).items(), key=lambda item: item[0].casefold())]


def drop(id: str, *, store: str | os.PathLike[str] = STORE) -> dict[str, object]:
    """🗑 Remove ``id`` from the store; return what was removed (without the Base64)."""
    rows = _load(store)
    row = _row(rows, id)
    del rows[id]
    _save(store, rows)
    return _meta(row)


# --- 📐 sizes: an 8-bit PNG in, square PNGs (or one .ico) out ----------------------------------------------------

def _png_decode(data: bytes) -> tuple[int, int, bytes]:
    """Width, height and RGBA bytes of a PNG with 8 bits a sample, not interlaced; anything else is refused."""
    if data[:8] != _PNG:
        raise ValueError("sizes need a PNG source")
    pos, header, palette, trns, idat = 8, None, b"", b"", []
    while pos + 12 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        chunk, crc = data[pos + 8:pos + 8 + length], data[pos + 8 + length:pos + 12 + length]
        if len(chunk) != length or len(crc) != 4 or zlib.crc32(kind + chunk) != struct.unpack(">I", crc)[0]:
            raise ValueError("the PNG is damaged")
        pos += 12 + length
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", chunk)
        elif kind == b"PLTE":
            palette = chunk
        elif kind == b"tRNS":
            trns = chunk
        elif kind == b"IDAT":
            idat.append(chunk)
        elif kind == b"IEND":
            break
    if header is None or not idat:
        raise ValueError("the PNG is damaged")
    width, height, depth, color, _, _, interlace = header
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color)
    if channels is None or depth != 8 or interlace or not 1 <= width * height <= 1 << 24:
        raise ValueError("sizes need an 8-bit, non-interlaced PNG of at most 16 MP")
    raw = zlib.decompress(b"".join(idat))
    stride = width * channels
    if len(raw) != height * (stride + 1):
        raise ValueError("the PNG is damaged")
    out, previous = bytearray(), bytearray(stride)
    for y in range(height):
        kind, line = raw[y * (stride + 1)], bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride) if kind else ():
            a = line[i - channels] if i >= channels else 0
            b, c = previous[i], (previous[i - channels] if i >= channels else 0)
            if kind == 1:
                line[i] = (line[i] + a) & 255
            elif kind == 2:
                line[i] = (line[i] + b) & 255
            elif kind == 3:
                line[i] = (line[i] + ((a + b) >> 1)) & 255
            elif kind == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
            else:
                raise ValueError("the PNG is damaged")
        previous = line
        for x in range(width):
            if color == 6:
                out += line[x * 4:x * 4 + 4]
            elif color == 2:
                out += line[x * 3:x * 3 + 3] + b"\xff"
            elif color == 0:
                out += bytes((line[x],) * 3) + b"\xff"
            elif color == 4:
                out += bytes((line[x * 2],) * 3) + line[x * 2 + 1:x * 2 + 2]
            else:
                index = line[x]
                out += palette[index * 3:index * 3 + 3] + bytes((trns[index] if index < len(trns) else 255,))
    return width, height, bytes(out)


def _png_encode(width: int, height: int, rgba: bytes) -> bytes:
    rows = b"".join(b"\0" + rgba[y * width * 4:(y + 1) * width * 4] for y in range(height))

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    return _PNG + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)) + \
        chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b"")


def resize(png: bytes, size: int, *, opaque: bool = False) -> bytes:
    """📐 One square ``size`` px PNG from an 8-bit PNG, centred on a square side.

    Shrinking averages each block (alpha-weighted, so edges stay clean); growing repeats pixels (pixel art stays
    crisp). ``opaque`` flattens transparency onto the top-left colour (white if that is see-through — iOS paints it
    black), as home-screen icons need; otherwise transparency is kept."""
    if not isinstance(size, int) or not 1 <= size <= 4096:
        raise ValueError(f"size must be 1-4096 px: {size!r}")
    width, height, rgba = _png_decode(png)
    side = max(width, height)
    ox, oy = (side - width) // 2, (side - height) // 2
    bg = rgba[0:3] if rgba[3] == 255 else b"\xff\xff\xff"
    spans = [(i * side // size, max((i + 1) * side // size, i * side // size + 1)) for i in range(size)]
    out = bytearray()
    for y0, y1 in spans:
        for x0, x1 in spans:
            r = g = b = a = 0
            for sy in range(y0 - oy, y1 - oy):
                if 0 <= sy < height:
                    for sx in range(max(x0 - ox, 0), min(x1 - ox, width)):
                        i = (sy * width + sx) * 4
                        w = rgba[i + 3]
                        r, g, b, a = r + rgba[i] * w, g + rgba[i + 1] * w, b + rgba[i + 2] * w, a + w
            count = (y1 - y0) * (x1 - x0)
            if opaque:
                out += bytes(((r + bg[0] * (255 * count - a)) // (255 * count),
                              (g + bg[1] * (255 * count - a)) // (255 * count),
                              (b + bg[2] * (255 * count - a)) // (255 * count), 255))
            else:
                out += bytes((r // a, g // a, b // a, (a + count // 2) // count)) if a else b"\0\0\0\0"
    return _png_encode(size, size, bytes(out))


def ico(pngs: Iterable[bytes]) -> bytes:
    """🪟 One .ico holding the given square PNGs (each up to 256 px), stored as PNG as every browser reads."""
    images = list(pngs)
    entries, offset = [], 6 + 16 * len(images)
    for png in images:
        width, height = struct.unpack(">II", png[16:24]) if png[:8] == _PNG else (0, 0)
        if not 1 <= width <= 256 or width != height:
            raise ValueError("an .ico holds square PNGs of 1-256 px")
        entries.append(struct.pack("<BBBBHHII", width % 256, height % 256, 0, 0, 1, 32, len(png), offset))
        offset += len(png)
    return struct.pack("<HHH", 0, 1, len(images)) + b"".join(entries) + b"".join(images)


def _spec(spec: str) -> tuple[str, list[int], bool]:
    match = _SPEC.match(spec) if isinstance(spec, str) else None
    if not match:
        raise ValueError(f"spec must look like 'ID', 'ID@192', 'ID@180:opaque' or 'ID@16,32,48': {spec!r}")
    sizes = [int(size) for size in match["sizes"].split(",")] if match["sizes"] else []
    return _id(match["id"]), sizes, bool(match["opaque"])


def _derive(rows: dict[str, dict[str, str]], spec: str) -> tuple[bytes, str, str]:
    id, sizes, opaque = _spec(spec)
    row = _row(rows, id)
    raw = base64.b64decode(row["b64"])
    if not sizes:
        return raw, row["mime"], row["sha256"]
    pngs = [resize(raw, size, opaque=opaque) for size in sizes]
    return (pngs[0], "image/png", row["sha256"]) if len(pngs) == 1 else (ico(pngs), "image/vnd.microsoft.icon", row["sha256"])


def derive(spec: str, *, store: str | os.PathLike[str] = STORE) -> bytes:
    """📐 The bytes for a spec: ``ID`` as stored, ``ID@192`` one PNG, ``ID@180:opaque`` flattened, ``ID@16,32,48`` an .ico."""
    return _derive(_load(store), spec)[0]


# --- ⚡ the cache ---------------------------------------------------------------------------------------------------

def _item(item: str) -> tuple[str | None, str]:
    name, sep, spec = item.partition("=") if isinstance(item, str) else (None, "", item)
    if not sep:
        return None, item
    if not _NAME.match(name):
        raise ValueError(f"file name must be plain (A-Z a-z 0-9 . _ -, no folders): {name!r}")
    return name, spec


def sync(items: Iterable[str], *, store: str | os.PathLike[str] = STORE,
         cache: str | os.PathLike[str] = CACHE) -> dict[str, list[str]]:
    """⚡ Make ``cache`` hold the wanted items: ``ID`` / ``ID@…`` (named after the spec) or ``NAME=ID@…``.

    Missing or stale files are written, matching ones kept (variants are not remade while their source and file are
    unchanged), and files this function wrote before that are no longer wanted are removed. Its own files are
    recorded in ``cache/.cacherecycle.json``; nothing else is touched. Returns {"written", "kept", "removed"}."""
    rows = _load(store)
    folder = Path(cache)
    folder.mkdir(parents=True, exist_ok=True)
    index_path = folder / ".cacherecycle.json"
    before = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
    before = before if isinstance(before, dict) else {}
    report: dict[str, list[str]] = {"written": [], "kept": [], "removed": []}
    index: dict[str, dict[str, str]] = {}
    for item in dict.fromkeys(items):
        name, spec = _item(item)
        id, sizes, opaque = _spec(spec)
        source = _row(rows, id)["sha256"]
        if name is None:
            ext = (".png" if len(sizes) == 1 else ".ico") if sizes else (_MIME.guess_extension(rows[id]["mime"]) or "")
            name = id + ("." + "-".join(map(str, sizes)) + ("-opaque" if opaque else "") if sizes else "") + ext
        if name in index:
            raise ValueError(f"two items write {name!r}")
        target = folder / name
        old = before.get(name)
        if (target.is_file() and isinstance(old, dict) and old.get("from") == spec and old.get("source") == source
                and old.get("sha256") == hashlib.sha256(target.read_bytes()).hexdigest()):
            index[name] = old
            report["kept"].append(target.as_posix())
            continue
        data = _derive(rows, spec)[0]
        target.write_bytes(data)
        index[name] = {"from": spec, "source": source, "sha256": hashlib.sha256(data).hexdigest()}
        report["written"].append(target.as_posix())
    for name in sorted(set(before) - set(index)):
        old = folder / name
        if isinstance(name, str) and _NAME.match(name) and old.is_file():
            old.unlink()
            report["removed"].append(old.as_posix())
    index_path.write_text(json.dumps(index, indent=1, sort_keys=True) + "\n", encoding="utf-8")
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
    p.add_argument("--id", required=True, help="🏷 id: the repository name as written, a dot and what it is, e.g. OnTautoLogy.favicon")
    p.add_argument("--mime", help="default: guessed from the file name")
    p.add_argument("--max-bytes", type=int, default=1 << 20, help="📏 size limit in bytes; 0 = no limit (default 1048576)")
    p = sub.add_parser("get", help="📤 write the bytes of a spec (ID, ID@192, ID@180:opaque, ID@16,32,48) to a file")
    p.add_argument("spec")
    p.add_argument("--out", required=True, help="output file, or - for stdout")
    sub.add_parser("ls", help="📋 list entries")
    p = sub.add_parser("drop", help="🗑 remove an entry")
    p.add_argument("id")
    p = sub.add_parser("sync", help="⚡ write the wanted items (ID@… or NAME=ID@…) into the cache and tidy it")
    p.add_argument("items", nargs="+")
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
            data = derive(args.spec, store=args.store)
            if args.out == "-":
                sys.stdout.buffer.write(data)
            else:
                Path(args.out).write_bytes(data)
                print(f"📤 {args.spec} → {args.out}")
        elif args.command == "ls":
            rows = ls(store=args.store)
            print("\n".join(_line(meta, "🧊") for meta in rows) if rows else "🫙 (empty)")
        elif args.command == "drop":
            print(_line(drop(args.id, store=args.store), "🗑"))
        else:
            report = sync(args.items, store=args.store, cache=args.cache)
            for key, icon in (("written", "✍️"), ("kept", "✅"), ("removed", "🧹")):
                for path in report[key]:
                    print(f"{icon} {path}")
    except (KeyError, ValueError, OSError, zlib.error) as error:
        print(f"❌ {error.args[0] if isinstance(error, KeyError) else error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
