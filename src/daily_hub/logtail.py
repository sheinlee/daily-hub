"""Incremental log readers that never hold a file handle between polls.

Original tools rotate their logs at midnight by renaming the file; a reader that keeps the
file open (without FILE_SHARE_DELETE) makes that rename fail and the tool silently loses its
log.  So every poll opens, reads the new bytes and closes again.
"""
from __future__ import annotations

import glob
import os
import queue
import threading
import time
from typing import Callable, IO


class FileTail:
    """Follow whichever file `resolve()` points at; only bytes written after `since` count."""

    def __init__(self, resolve: Callable[[], list[str]], since: float):
        self._resolve = resolve
        self._since = since
        self._offsets: dict[str, int] = {}
        self._rest: dict[str, str] = {}

    def poll(self) -> list[str]:
        lines: list[str] = []
        for path in self._resolve():
            try:
                st = os.stat(path)
            except OSError:
                continue
            if path not in self._offsets:
                # A file that already existed before this run: skip what was there.
                created_before = st.st_ctime < self._since - 2
                self._offsets[path] = st.st_size if created_before else 0
            off = self._offsets[path]
            if st.st_size < off:  # truncated / replaced
                off = 0
                self._rest[path] = ""
            if st.st_size == off:
                continue
            try:
                with open(path, "rb") as f:  # short open, closed right after reading
                    f.seek(off)
                    data = f.read(st.st_size - off)
            except OSError:
                continue
            self._offsets[path] = off + len(data)
            text = self._rest.get(path, "") + data.decode("utf-8", errors="replace")
            parts = text.split("\n")
            self._rest[path] = parts.pop()
            lines.extend(p.rstrip("\r") for p in parts)
        return lines


def newest(pattern: str, since: float, limit: int = 1) -> Callable[[], list[str]]:
    """Resolver: the newest files matching a glob that were modified at/after `since`."""
    def resolve() -> list[str]:
        files = []
        for p in glob.glob(pattern):
            try:
                m = os.path.getmtime(p)
            except OSError:
                continue
            if m >= since - 2:
                files.append((m, p))
        files.sort()
        return [p for _, p in files[-limit:]]
    return resolve


class PipeReader:
    """Drain a child's stdout on a thread so the child never blocks on a full pipe."""

    def __init__(self, stream: IO[bytes]):
        self._q: queue.Queue[str] = queue.Queue()
        self._t = threading.Thread(target=self._run, args=(stream,), daemon=True)
        self._t.start()

    def _run(self, stream):
        try:
            for raw in iter(stream.readline, b""):
                self._q.put(raw.decode("utf-8", errors="replace").rstrip("\r\n"))
        except (OSError, ValueError):
            pass
        finally:
            try:
                stream.close()
            except OSError:
                pass

    def poll(self) -> list[str]:
        out = []
        while True:
            try:
                out.append(self._q.get_nowait())
            except queue.Empty:
                return out

    def drain(self, timeout: float = 2.0) -> list[str]:
        self._t.join(timeout)
        return self.poll()


def today_stamp(fmt: str, t: float | None = None) -> str:
    return time.strftime(fmt, time.localtime(t or time.time()))
