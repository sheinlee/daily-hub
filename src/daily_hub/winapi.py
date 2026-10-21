"""Win32 helpers via ctypes: admin check, windows, mutex probe."""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys
from dataclasses import dataclass

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

WM_CLOSE = 0x0010
SW_RESTORE = 9
SW_SHOW = 5
SYNCHRONIZE = 0x00100000
ERROR_FILE_NOT_FOUND = 2
ERROR_ACCESS_DENIED = 5

WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsIconic.argtypes = [wt.HWND]
user32.IsHungAppWindow.argtypes = [wt.HWND]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetWindow.argtypes = [wt.HWND, wt.UINT]
user32.GetWindow.restype = wt.HWND
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.ShowWindowAsync.argtypes = [wt.HWND, ctypes.c_int]
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.BringWindowToTop.argtypes = [wt.HWND]
user32.GetForegroundWindow.restype = wt.HWND
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
kernel32.OpenMutexW.argtypes = [wt.DWORD, wt.BOOL, wt.LPCWSTR]
kernel32.OpenMutexW.restype = wt.HANDLE
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.GetCurrentThreadId.restype = wt.DWORD
shell32.ShellExecuteW.argtypes = [wt.HWND, wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, ctypes.c_int]
shell32.ShellExecuteW.restype = wt.HINSTANCE

GW_OWNER = 4
HWND_TOP = 0
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_ASYNCWINDOWPOS = 0x4000


def is_admin() -> bool:
    try:
        return bool(shell32.IsUserAnAdmin())
    except OSError:
        return False


def is_gui_exe(path: str) -> bool:
    """True if the PE file uses the Windows GUI subsystem (starting it opens no console window)."""
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
        pe = int.from_bytes(head[0x3C:0x40], "little")
        return head[pe:pe + 4] == b"PE\0\0" and int.from_bytes(head[pe + 92:pe + 94], "little") == 2
    except (OSError, ValueError):
        return False


def set_app_id(app_id: str) -> None:
    """Explicit AppUserModelID: the taskbar shows this app's window icon instead of pythonw.exe's."""
    try:
        shell32.SetCurrentProcessExplicitAppUserModelID(ctypes.c_wchar_p(app_id))
    except (AttributeError, OSError):
        pass


def file_version(path: str) -> str | None:
    """ProductVersion (else FileVersion) string of an .exe/.dll, e.g. "v6.19.0+d274123c"; None if absent."""
    version = ctypes.WinDLL("version")
    version.GetFileVersionInfoSizeW.argtypes = [wt.LPCWSTR, ctypes.POINTER(wt.DWORD)]
    version.GetFileVersionInfoW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p]
    version.VerQueryValueW.argtypes = [ctypes.c_void_p, wt.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
                                       ctypes.POINTER(wt.UINT)]
    size = version.GetFileVersionInfoSizeW(path, None)
    if not size:
        return None
    buf = ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(path, 0, size, buf):
        return None
    ptr, n = ctypes.c_void_p(), wt.UINT()
    if not version.VerQueryValueW(buf, r"\VarFileInfo\Translation", ctypes.byref(ptr), ctypes.byref(n)) or n.value < 4:
        return None
    lang, cp = ctypes.cast(ptr, ctypes.POINTER(wt.WORD * 2)).contents
    for name in ("ProductVersion", "FileVersion"):
        sub = rf"\StringFileInfo\{lang:04x}{cp:04x}\{name}"
        if version.VerQueryValueW(buf, sub, ctypes.byref(ptr), ctypes.byref(n)) and n.value:
            text = ctypes.wstring_at(ptr, n.value).split("\0", 1)[0].strip()
            if text:
                return text
    return None


def gui_python() -> str | None:
    """A GUI-subsystem pythonw.exe for this Python.  uv's .venv\\Scripts\\pythonw.exe is a copy of its
    console trampoline, so the base installation's pythonw.exe comes first."""
    for exe in (getattr(sys, "_base_executable", ""), sys.executable):
        w = os.path.join(os.path.dirname(exe), "pythonw.exe") if exe else ""
        if w and is_gui_exe(w):
            return w
    return None


def relaunch_as_admin(exe: str, argv: list[str]) -> bool:
    """Run exe + argv elevated. Returns True if the UAC launch was accepted."""
    params = " ".join(_quote(a) for a in argv)
    rc = shell32.ShellExecuteW(None, "runas", exe, params, os.getcwd(), SW_SHOW)
    return int(rc or 0) > 32


def _quote(a: str) -> str:
    return f'"{a}"' if (" " in a or not a) else a


def set_dpi_aware() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass


@dataclass
class WindowInfo:
    hwnd: int
    pid: int
    cls: str
    title: str
    width: int
    height: int
    minimized: bool
    hung: bool


def _text(hwnd) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _cls(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def window_info(hwnd: int) -> WindowInfo:
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    rect = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    return WindowInfo(
        hwnd=int(hwnd), pid=int(pid.value), cls=_cls(hwnd), title=_text(hwnd),
        width=rect.right - rect.left, height=rect.bottom - rect.top,
        minimized=bool(user32.IsIconic(hwnd)), hung=bool(user32.IsHungAppWindow(hwnd)),
    )


def top_level_windows(pids: set[int] | None = None, visible_only: bool = True) -> list[WindowInfo]:
    """Visible, unowned top-level windows, optionally filtered by owning PID."""
    found: list[WindowInfo] = []

    def cb(hwnd, _lparam):
        if visible_only and not user32.IsWindowVisible(hwnd):
            return True
        if user32.GetWindow(hwnd, GW_OWNER):
            return True
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pids is not None and pid.value not in pids:
            return True
        found.append(window_info(hwnd))
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found


def is_hung(hwnd: int) -> bool:
    return bool(user32.IsHungAppWindow(hwnd))


def restore_and_focus(hwnd: int) -> bool:
    """Ask the window to un-minimize and come to the front without ever blocking on it.

    Only asynchronous calls are used (ShowWindowAsync / SWP_ASYNCWINDOWPOS): the synchronous
    ShowWindow/BringWindowToTop wait for the target thread and hang forever on a frozen game.
    Returns False if the window is hung (nothing was sent).
    """
    if is_hung(hwnd):
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindowAsync(hwnd, SW_RESTORE)
    user32.SetWindowPos(hwnd, HWND_TOP, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_ASYNCWINDOWPOS)
    fg = user32.GetForegroundWindow()
    if fg != hwnd:
        cur = kernel32.GetCurrentThreadId()
        fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg and not is_hung(fg) else 0
        attached = False
        if fg_tid and fg_tid != cur:
            attached = bool(user32.AttachThreadInput(cur, fg_tid, True))
        user32.SetForegroundWindow(hwnd)
        if attached:
            user32.AttachThreadInput(cur, fg_tid, False)
    return True


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT), ("_pad", ctypes.c_byte * 32)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _U)]


user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
user32.ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
_INPUT_MOUSE = 0
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_MOUSEEVENTF_MOVE = 0x0001


user32.IsWindow.argtypes = [wt.HWND]
user32.WindowFromPoint.argtypes = [wt.POINT]
user32.WindowFromPoint.restype = wt.HWND
user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
user32.GetAncestor.restype = wt.HWND
_GA_ROOT = 2


def is_window(hwnd: int) -> bool:
    return bool(user32.IsWindow(hwnd))


def is_visible(hwnd: int) -> bool:
    return bool(user32.IsWindowVisible(hwnd))


def foreground_window() -> int:
    return int(user32.GetForegroundWindow() or 0)


def root_window_at(x: int, y: int) -> int:
    """Top-level window that would receive a click at screen (x, y)."""
    h = user32.WindowFromPoint(wt.POINT(x, y))
    if not h:
        return 0
    return int(user32.GetAncestor(h, _GA_ROOT) or 0)


def cursor_pos() -> tuple[int, int]:
    pt = wt.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def client_rect_on_screen(hwnd: int) -> tuple[int, int, int, int]:
    """(left, top, width, height) of the client area in physical screen pixels."""
    rect = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    pt = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y, rect.right - rect.left, rect.bottom - rect.top


def left_click(x: int, y: int) -> None:
    """Real left click at screen (x, y); the cursor is put back afterwards."""
    old = cursor_pos()
    user32.SetCursorPos(x, y)
    seq = (_INPUT * 2)()
    seq[0].type = seq[1].type = _INPUT_MOUSE
    seq[0].mi.dwFlags = _MOUSEEVENTF_LEFTDOWN
    seq[1].mi.dwFlags = _MOUSEEVENTF_LEFTUP
    user32.SendInput(1, ctypes.byref(seq[0]), ctypes.sizeof(_INPUT))
    kernel32.Sleep(60)
    user32.SendInput(1, ctypes.byref(seq[1]), ctypes.sizeof(_INPUT))
    kernel32.Sleep(60)
    user32.SetCursorPos(*old)


def nudge_mouse() -> None:
    """A zero-distance relative mouse move: counts as user input (wakes the display), moves nothing."""
    inp = _INPUT()
    inp.type = _INPUT_MOUSE
    inp.mi.dwFlags = _MOUSEEVENTF_MOVE
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


def post_close(hwnd: int) -> bool:
    return bool(user32.PostMessageW(hwnd, WM_CLOSE, 0, 0))


def mutex_exists(name: str) -> bool | None:
    """Read-only probe with OpenMutexW. True=exists, False=absent, None=unknown.

    Never creates the mutex (CreateMutex would make the original tool think it is already running).
    """
    h = kernel32.OpenMutexW(SYNCHRONIZE, False, name)
    if h:
        kernel32.CloseHandle(h)
        return True
    err = ctypes.get_last_error()
    if err == ERROR_FILE_NOT_FOUND:
        return False
    if err == ERROR_ACCESS_DENIED:
        return True
    return None
