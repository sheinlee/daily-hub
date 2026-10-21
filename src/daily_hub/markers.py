"""Do the texts the hub's run rules look for still exist in the installed tool?

After a tool update the log line that tells the hub "the daily run finished" (or failed) may change.  Each
adapter lists those texts and the tool files that carry them; this module searches the files (UTF-8 and
UTF-16 for text and .NET/Qt binaries, the compressed PYZ archive for PyInstaller apps).  Nothing is
executed.  Only the yes/no answers are cached (per file size + mtime), never the file contents.
"""
from __future__ import annotations

import glob
import marshal
import os
import struct
import zlib

_answers: dict[tuple, bool] = {}
_PYZ_PREFIX = "pyz:"


def _read(path: str) -> bytes:
    """File bytes; for "pyz:<exe>" the decompressed modules of a PyInstaller exe."""
    if path.startswith(_PYZ_PREFIX):
        with open(path[len(_PYZ_PREFIX):], "rb") as f:
            return _pyz_text(f.read())
    with open(path, "rb") as f:
        return f.read()


def _pyz_text(data: bytes) -> bytes:
    pos = data.rfind(b"MEI\x0c\x0b\x0a\x0b\x0e")
    if pos < 0:
        return data
    _magic, pkglen, tocoff, toclen, _pyver = struct.unpack("!8sIIII", data[pos:pos + 24])
    base = pos + 88 - pkglen
    toc = data[base + tocoff: base + tocoff + toclen]
    parts, i = [], 0
    while i + 18 <= len(toc):
        esz, off, clen, _ulen, cflag, typ = struct.unpack("!IIIIBc", toc[i:i + 18])
        if esz <= 0:
            break
        blob = data[base + off: base + off + clen]
        try:
            if typ == b"z" and blob[:4] == b"PYZ\0":
                ztoc = marshal.loads(blob[struct.unpack("!I", blob[8:12])[0]:])   # TOC: plain lists/strings/ints
                for _name, e in (ztoc.items() if isinstance(ztoc, dict) else ztoc):
                    try:                 # one odd entry (e.g. a namespace package) must not hide the rest
                        parts.append(zlib.decompress(blob[e[1]:e[1] + e[2]]))
                    except (zlib.error, TypeError, IndexError):
                        pass
            elif typ == b"s":
                parts.append(zlib.decompress(blob) if cflag else blob)
        except (ValueError, zlib.error, EOFError, TypeError, struct.error):
            pass
        i += esz
    return b"\n".join(parts)


def _found(text: str, data: bytes) -> bool:
    return any(text.encode(enc) in data for enc in ("utf-8", "utf-16-le", "utf-16-be"))


def _stat_key(path: str):
    real = path[len(_PYZ_PREFIX):] if path.startswith(_PYZ_PREFIX) else path
    st = os.stat(real)
    return path, st.st_size, st.st_mtime_ns


def missing(tool_dir: str, markers: list[tuple[str, list[str]]]) -> list[str]:
    """Texts not found in any of their files (patterns are relative to tool_dir; "pyz:" for frozen exes)."""
    todo: dict[str, list[str]] = {}          # path -> texts still to look for there
    paths_of: dict[str, list[str]] = {}
    for text, patterns in markers:
        paths = []
        for pat in patterns:
            pyz = pat.startswith(_PYZ_PREFIX)
            rel = pat[len(_PYZ_PREFIX):] if pyz else pat
            for p in sorted(glob.glob(os.path.join(tool_dir, rel), recursive=True)):
                if os.path.isfile(p):
                    paths.append(_PYZ_PREFIX + p if pyz else p)
        paths_of[text] = paths
    result: dict[str, bool] = {}
    for text, paths in paths_of.items():
        for p in paths:
            try:
                key = _stat_key(p) + (text,)
            except OSError:
                continue
            if key in _answers:
                if _answers[key]:
                    result[text] = True
                    break
            else:
                todo.setdefault(p, []).append(text)
    for p, texts in todo.items():
        texts = [t for t in texts if not result.get(t)]
        if not texts:
            continue
        try:
            data, base = _read(p), _stat_key(p)
        except OSError:
            continue
        for t in texts:
            hit = _found(t, data)
            _answers[base + (t,)] = hit
            if hit:
                result[t] = True
        del data
    out = []
    for text, patterns in markers:
        if not result.get(text):
            out.append(text if paths_of[text] else f"{text}（相关文件 {'、'.join(patterns)} 都不存在）")
    return out
