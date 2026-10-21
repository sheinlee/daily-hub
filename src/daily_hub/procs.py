"""Process helpers built on psutil: find, launch, track descendants, terminate."""
from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable

import psutil

from . import winapi

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
DETACHED_PROCESS = 0x00000008

# Variables that must never leak from the hub (or the shell that started it) into a tool.
_STRIP_EXACT = {
    "PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONUSERBASE", "PYTHONCASEOK", "PYTHONHASHSEED",
    "PYTHONOPTIMIZE", "PYTHONVERBOSE", "PYTHONDEBUG", "PYTHONWARNINGS", "PYTHONINSPECT",
    "PYTHONEXECUTABLE", "VIRTUAL_ENV", "VIRTUAL_ENV_PROMPT", "CONDA_PREFIX", "CONDA_DEFAULT_ENV",
    "PYTHONUTF8", "OK_DISABLE_FILE_LOG", "__PYVENV_LAUNCHER__",
}
_STRIP_PREFIX = ("PYAPPIFY_",)


def clean_env(extra: dict[str, str] | None = None, path: str | None = None,
              drop: tuple[str, ...] = (), drop_prefixes: tuple[str, ...] = ()) -> dict[str, str]:
    """Copy of the hub environment without Python/venv/pyappify leftovers (plus `drop`/`drop_prefixes`)."""
    exact = _STRIP_EXACT | {d.upper() for d in drop}
    prefixes = _STRIP_PREFIX + tuple(d.upper() for d in drop_prefixes)
    env = {k: v for k, v in os.environ.items()
           if k.upper() not in exact and not k.upper().startswith(prefixes)}
    if path is not None:
        env["PATH"] = path
    if extra:
        env.update(extra)
    return env


def system_path() -> str:
    root = os.environ.get("SystemRoot", r"C:\Windows")
    return os.pathsep.join([
        os.path.join(root, "system32"), root, os.path.join(root, "System32", "Wbem"),
        os.path.join(root, "System32", "WindowsPowerShell", "v1.0"), os.path.join(root, "System32", "OpenSSH"),
    ])


def norm(p: str | None) -> str:
    return os.path.normcase(os.path.normpath(p)) if p else ""


def is_python_image(name: str) -> bool:
    n = name.lower()
    return n.startswith("python") and n.endswith(".exe")


def safe_info(p: psutil.Process, deep: bool | None = None) -> dict:
    """name / exe / create_time for every process; cmdline / cwd only when `deep`.

    exe and create_time only need PROCESS_QUERY_LIMITED_INFORMATION.  cmdline and cwd need
    PROCESS_VM_READ, which must never be used on game clients guarded by anti-cheat (ACE), so by
    default they are read for python*.exe images only.
    """
    info = {"pid": p.pid, "name": "", "exe": "", "cmdline": [], "cwd": "", "create_time": 0.0}
    try:
        info["name"] = p.name()
        info["create_time"] = p.create_time()
    except (psutil.Error, OSError):
        return info
    try:
        info["exe"] = p.exe()
    except (psutil.Error, OSError):
        pass
    if deep is None:
        deep = is_python_image(info["name"])
    if deep:
        try:
            info["cmdline"] = p.cmdline()
        except (psutil.Error, OSError):
            info["cmdline"] = ["<无法读取>"]
        try:
            info["cwd"] = p.cwd()
        except (psutil.Error, OSError):
            pass
    return info


def _fresh_scan() -> None:
    """process_iter() caches Process objects (and their name/exe/create_time) by PID; a PID that was
    reused since the last scan would otherwise be reported as the old process."""
    try:
        psutil.process_iter.cache_clear()
    except AttributeError:
        pass


def find_processes(pred: Callable[[dict], bool]) -> list[dict]:
    out = []
    _fresh_scan()
    for p in psutil.process_iter():
        if p.pid in (0, 4) or p.pid == os.getpid():
            continue
        info = safe_info(p)
        try:
            if pred(info):
                out.append(info)
        except Exception:
            continue
    return out


def by_names(names: Iterable[str]) -> list[dict]:
    """Match by image name only (at most PROCESS_QUERY_LIMITED_INFORMATION; process memory is never read)."""
    wanted = {n.lower() for n in names if n}
    out = []
    _fresh_scan()
    for p in psutil.process_iter(["name"]):
        name = p.info.get("name") or ""
        if name.lower() in wanted and p.pid != os.getpid():
            out.append({"pid": p.pid, "name": name, "exe": "", "cmdline": [], "cwd": "", "create_time": 0.0})
    return out


def under_dir(directory: str, names: Iterable[str] | None = None) -> list[dict]:
    """Processes whose executable lives inside `directory` (optionally restricted by image name)."""
    d = norm(directory)
    if not d:                       # tool folder not set yet: nothing can be "inside" it
        return []
    wanted = {n.lower() for n in names} if names else None

    def pred(i):
        if wanted is not None and i["name"].lower() not in wanted:
            return False
        exe = norm(i["exe"])
        return bool(exe) and (exe == d or exe.startswith(d + os.sep))
    return find_processes(pred)


def describe(infos: list[dict]) -> str:
    return "、".join(f'{i["name"]}(PID {i["pid"]})' for i in infos)


@dataclass
class Tracked:
    """A launched tool process plus every descendant seen while it ran (pid -> create_time)."""
    popen: subprocess.Popen
    seen: dict[int, float] = field(default_factory=dict)
    exclude_names: set[str] = field(default_factory=set)

    @property
    def pid(self) -> int:
        return self.popen.pid

    def refresh(self) -> None:
        try:
            root = psutil.Process(self.pid)
            self.seen.setdefault(self.pid, root.create_time())
            for c in root.children(recursive=True):
                try:
                    if c.name().lower() in self.exclude_names:
                        continue
                    self.seen.setdefault(c.pid, c.create_time())
                except psutil.Error:
                    continue
        except psutil.Error:
            pass

    def alive(self) -> list[psutil.Process]:
        """Recorded processes that still exist with the same identity (pid + create_time)."""
        res = []
        for pid, ct in list(self.seen.items()):
            try:
                p = psutil.Process(pid)
                if abs(p.create_time() - ct) < 0.01:
                    res.append(p)
            except psutil.Error:
                continue
        return res

    def root_running(self) -> bool:
        return self.popen.poll() is None

    def close_windows(self) -> int:
        pids = {p.pid for p in self.alive()}
        n = 0
        for w in winapi.top_level_windows(pids):
            if winapi.post_close(w.hwnd):
                n += 1
        return n

    def kill_all(self, wait: float = 10.0) -> list[str]:
        """Terminate every recorded process still alive. Returns names of anything that survived."""
        self.refresh()
        procs = self.alive()
        for p in procs:
            try:
                p.kill()
            except psutil.Error:
                pass
        _, still = psutil.wait_procs(procs, timeout=wait)
        out = []
        for p in still:
            if _exists(p):
                try:
                    out.append(f"{p.name()}(PID {p.pid})")
                except psutil.Error:
                    out.append(f"PID {p.pid}")
        return out


def _exists(p: psutil.Process) -> bool:
    try:
        return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def launch(argv: list[str], cwd: str, env: dict[str, str], console: bool,
           capture: bool) -> subprocess.Popen:
    flags = (CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP) if console else 0
    return subprocess.Popen(
        argv, cwd=cwd, env=env, creationflags=flags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.STDOUT if capture else subprocess.DEVNULL,
        close_fds=True,
    )


def launch_detached(argv: list[str], cwd: str) -> int:
    p = subprocess.Popen(argv, cwd=cwd, env=clean_env(),
                         creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True)
    return p.pid


def wait_gone(names: Iterable[str], timeout: float, poll: float = 1.0,
              should_abort: Callable[[], bool] | None = None) -> list[dict]:
    deadline = time.monotonic() + timeout
    while True:
        left = by_names(names)
        if not left or time.monotonic() >= deadline:
            return left
        if should_abort and should_abort():
            return left
        time.sleep(poll)
