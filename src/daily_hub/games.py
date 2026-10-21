"""Game client control: start, wait for a usable window, keep it un-minimized, close."""
from __future__ import annotations

import os
import shlex
import subprocess
import time
from typing import Callable

import psutil

from . import procs, winapi
from .config import GameConfig

Log = Callable[[str], None]
Abort = Callable[[], bool]

MIN_W, MIN_H = 640, 360


def game_processes(g: GameConfig) -> list[dict]:
    return procs.by_names(g.game_processes)


def is_running(g: GameConfig) -> bool:
    return bool(game_processes(g))


def find_window(g: GameConfig) -> winapi.WindowInfo | None:
    """The game's main window: owned by a game process, matching class/title, largest area."""
    pids = {p["pid"] for p in game_processes(g)}
    if not pids:
        return None
    cands = []
    for w in winapi.top_level_windows(pids):
        if g.window_class and w.cls != g.window_class:
            continue
        if g.window_title and g.window_title not in w.title:
            continue
        cands.append(w)
    return _pick(cands)


def _pick(cands: list[winapi.WindowInfo]) -> winapi.WindowInfo | None:
    """Largest usable window; else a minimized one (0x0 client area, can be restored); else anything."""
    if not cands:
        return None
    usable = [w for w in cands if not w.minimized and w.width >= MIN_W and w.height >= MIN_H]
    pool = usable or [w for w in cands if w.minimized] or cands
    return max(pool, key=lambda w: w.width * w.height)


def find_launcher_window(g: GameConfig) -> winapi.WindowInfo | None:
    pids = {p["pid"] for p in procs.by_names(g.launcher_processes)}
    if not pids:
        return None
    cands = [w for w in winapi.top_level_windows(pids)
             if (not g.launcher_window_class or w.cls == g.launcher_window_class)
             and (not g.launcher_window_title or g.launcher_window_title in w.title)]
    if not cands:
        return None
    usable = [w for w in cands if not w.minimized and w.width >= 300 and w.height >= 200]
    return max(usable or cands, key=lambda w: w.width * w.height)


def parse_click(spec: str) -> tuple[float, float] | None:
    try:
        x, y = (float(v) for v in spec.replace("，", ",").split(","))
    except ValueError:
        return None
    return (x, y) if 0 < x < 1 and 0 < y < 1 else None


def launch_via_launcher(g: GameConfig, log: Log, abort: Abort, probe) -> None:
    """Open the official launcher and click its "开始游戏" button, but only on positive evidence.

    `probe` reads the launcher's own log (see adapters/hypergryph_launcher.py): the click happens only
    when the page shown is this game and its button has been "start_game" for a while; afterwards the
    log must show the click landed on this game and the game process being created.  One click only.
    """
    if probe is None:
        raise RuntimeError("这款游戏的启动器读不到按钮状态，不支持自动点击")
    point = parse_click(g.launcher_click)
    if point is None:
        raise RuntimeError("未标定启动器“开始游戏”按钮位置（设置页 → 标定）")
    probe.begin()
    w = find_launcher_window(g)
    newly_started = w is None
    if newly_started:
        if not os.path.isfile(g.launcher_exe):
            raise RuntimeError(f"启动器程序不存在：{g.launcher_exe}")
        args = shlex.split(g.launcher_args, posix=False) if g.launcher_args.strip() else []
        pid = procs.launch_detached([g.launcher_exe, *args], cwd=os.path.dirname(g.launcher_exe))
        log(f"已打开启动器：{os.path.basename(g.launcher_exe)}（PID {pid}），等待窗口")
        end = time.monotonic() + 90
        while w is None and time.monotonic() < end and not abort():
            time.sleep(1)
            w = find_launcher_window(g)
        if w is None:
            raise RuntimeError("90 秒内没有等到启动器窗口")
    else:
        log("启动器已经开着，直接使用")
    # Bring the window up.
    end = time.monotonic() + 20
    while not abort():
        w = find_launcher_window(g)
        if w is not None and not w.minimized and w.width >= 300 and w.height >= 200:
            break
        if time.monotonic() >= end:
            raise RuntimeError("启动器窗口无法恢复到正常显示状态")
        if w is not None:
            winapi.restore_and_focus(w.hwnd)
        time.sleep(1)
    # Wait until its log proves the right page and a stable "start_game" button.
    end = time.monotonic() + 120
    last_why = ""
    while not abort():
        ok, why = probe.ready(newly_started)
        if ok is False:
            raise RuntimeError(why)
        if ok:
            break
        if why != last_why:
            log(f"启动器：{why}")
            last_why = why
        if time.monotonic() >= end:
            raise RuntimeError(f"120 秒内无法确认启动器可以开始游戏（{why}）")
        time.sleep(2)
    if abort():
        return
    # Re-check the window right before clicking; never click blind.
    w = find_launcher_window(g)
    if w is None or not winapi.is_window(w.hwnd) or not winapi.is_visible(w.hwnd) or w.minimized \
            or w.hung or w.width < 300 or w.height < 200:
        raise RuntimeError("点击前启动器窗口不可用（最小化、隐藏或无响应），已放弃点击")
    if not winapi.restore_and_focus(w.hwnd):
        raise RuntimeError("启动器窗口无响应，已放弃点击")
    time.sleep(1)
    left, top, width, height = winapi.client_rect_on_screen(w.hwnd)
    x, y = left + int(width * point[0]), top + int(height * point[1])
    if winapi.foreground_window() != w.hwnd:
        raise RuntimeError("无法把启动器切到前台，已放弃点击")
    if winapi.root_window_at(x, y) != w.hwnd:
        raise RuntimeError("点击位置不在启动器窗口上（被遮挡或在屏幕外），已放弃点击")
    ok, why = probe.ready(newly_started)
    if ok is not True:
        raise RuntimeError(f"点击前复核未通过：{why}")
    probe.mark_click()
    log(f"点击启动器“开始游戏”：屏幕坐标 {x},{y}")
    winapi.left_click(x, y)
    # Verify through the launcher log; no second click.
    clicked_at = time.monotonic()
    end = clicked_at + 60
    while not abort():
        res, why = probe.clicked()
        if res is False:
            raise RuntimeError(why)
        if res and is_running(g):
            log("游戏进程已出现")
            return
        if res is None and why.startswith("等待启动器响应") and time.monotonic() - clicked_at > 8:
            raise RuntimeError("启动器没有收到点击（按钮位置可能需要重新标定）")
        if time.monotonic() >= end:
            raise RuntimeError(f"点击后 60 秒内游戏没有启动（{why}）")
        time.sleep(1)


def launch(g: GameConfig, log: Log) -> None:
    exe = g.game_exe
    if not os.path.isfile(exe):
        raise RuntimeError(f"游戏程序不存在：{exe}")
    args = shlex.split(g.game_args, posix=False) if g.game_args.strip() else []
    try:
        pid = procs.launch_detached([exe, *args], cwd=os.path.dirname(exe))
    except OSError as e:
        if getattr(e, "winerror", None) == 740:
            raise RuntimeError("游戏需要管理员权限，请以管理员身份运行总控") from e
        raise RuntimeError(f"启动游戏失败：{e}") from e
    log(f"已启动游戏：{os.path.basename(exe)} {' '.join(args)}（PID {pid}）")


def wait_ready(g: GameConfig, timeout: float, abort: Abort, log: Log) -> winapi.WindowInfo | None:
    """Wait until the main window exists, is restored and its size has been stable for ~3 s."""
    deadline = time.monotonic() + timeout
    last_size = None
    stable = 0
    announced = False
    while time.monotonic() < deadline:
        if abort():
            return None
        w = find_window(g)
        if w is not None:
            if not announced:
                log(f"找到游戏窗口：{w.title!r} [{w.cls}] {w.width}x{w.height}")
                announced = True
            if w.hung:
                stable = 0
            elif w.minimized:
                log("游戏窗口处于最小化，正在恢复")
                winapi.restore_and_focus(w.hwnd)
                stable = 0
            elif w.width >= MIN_W and w.height >= MIN_H:
                size = (w.width, w.height)
                stable = stable + 1 if size == last_size else 0
                last_size = size
                if stable >= 3:
                    return w
        time.sleep(1)
    return None


HUNG = "hung"
MINIMIZED = "minimized"


def ensure_visible(g: GameConfig) -> str | None:
    """None = fine (or no window yet); MINIMIZED = restore was requested; HUNG = not responding."""
    w = find_window(g)
    if w is None:
        return None
    if w.hung:
        return HUNG
    if w.minimized:
        winapi.restore_and_focus(w.hwnd)
        return MINIMIZED
    return None


def focus(g: GameConfig) -> None:
    w = find_window(g)
    if w is not None and not w.hung:
        winapi.restore_and_focus(w.hwnd)


def close(g: GameConfig, timeout: float, log: Log, abort: Abort | None = None) -> bool:
    """Close command (if any) -> WM_CLOSE the game's windows -> wait -> terminate the rest. True if all gone."""
    running = game_processes(g)
    if not running:
        return True
    if g.close_cmd.strip():
        argv = shlex.split(g.close_cmd, posix=False)
        argv = [a[1:-1] if len(a) > 1 and a[0] == a[-1] == '"' else a for a in argv]
        try:
            r = subprocess.run(argv, cwd=os.path.dirname(argv[0]) or None, env=procs.clean_env(),
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=procs.CREATE_NO_WINDOW, timeout=60)
            log(f"已执行关闭命令（退出码 {r.returncode}），等待退出…")
        except (OSError, subprocess.SubprocessError) as e:
            log(f"关闭命令执行失败：{e}")
        if not procs.wait_gone(g.game_processes, timeout, should_abort=abort):
            log("已退出")
            return True
        running = game_processes(g)
    pids = {p["pid"] for p in running}
    wins = winapi.top_level_windows(pids)
    main = [w for w in wins if not g.window_class or w.cls == g.window_class]
    for w in main or wins:
        winapi.post_close(w.hwnd)
    log(f"已向游戏发送关闭请求（{len(main or wins)} 个窗口），等待退出…")
    left = procs.wait_gone(g.game_processes, timeout, should_abort=abort)
    if left:
        log(f"游戏未在 {int(timeout)} 秒内退出，强制结束：{procs.describe(left)}")
        wanted = {n.lower() for n in g.game_processes}
        handles = []
        for i in left:
            try:
                p = psutil.Process(i["pid"])
                if p.name().lower() not in wanted:   # PID reused by something else meanwhile
                    continue
                p.kill()
                handles.append(p)
            except psutil.Error:
                continue
        psutil.wait_procs(handles, timeout=10)
        left = game_processes(g)
    if left:
        log(f"仍有游戏进程未退出：{procs.describe(left)}")
        return False
    log("游戏已退出")
    return True
