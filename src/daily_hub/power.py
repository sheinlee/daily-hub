"""Unattended runs: keep the PC awake while the queue runs, and the power action after it.

The scheduled task (schedule-task.ps1) wakes the PC from sleep/hibernate and starts the hub with
"--auto --then <action>".  After a wake-timer wake Windows keeps the display off and puts the PC back
to sleep after about 2 minutes unless a program asks for the system, so keep_awake() is called as soon
as an auto run is requested and for every queue run.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import subprocess
import time

from . import winapi
from .config import LOG_DIR

ACTIONS = {"none": "无操作", "sleep": "睡眠", "hibernate": "休眠", "shutdown": "关机"}

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32.SetThreadExecutionState.argtypes = [wt.DWORD]
_kernel32.SetThreadExecutionState.restype = wt.DWORD
_user32.OpenInputDesktop.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
_user32.OpenInputDesktop.restype = wt.HANDLE
_user32.GetUserObjectInformationW.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD,
                                              ctypes.POINTER(wt.DWORD)]
_user32.CloseDesktop.argtypes = [wt.HANDLE]

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002
DESKTOP_READOBJECTS = 0x0001
UOI_NAME = 2
SE_SHUTDOWN_PRIVILEGE = 19


def parse_then(args: list[str]) -> str:
    """The value after --then (default and fallback: none)."""
    if "--then" in args:
        i = args.index("--then")
        if i + 1 < len(args) and args[i + 1] in ACTIONS:
            return args[i + 1]
    return "none"


def keep_awake(on: bool) -> None:
    """Block (or allow again) sleep and display-off.  Must be called from the GUI thread (per-thread state)."""
    flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED if on else 0)
    _kernel32.SetThreadExecutionState(flags)


def wake_display() -> None:
    """Turn the display on after a wake-timer wake (games and screen-capture tools need a live desktop)."""
    _kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
    winapi.nudge_mouse()


def session_locked() -> bool:
    """True while the lock screen / sign-in screen (not the user's desktop) receives input."""
    h = _user32.OpenInputDesktop(0, False, DESKTOP_READOBJECTS)
    if not h:
        return True     # the Winlogon desktop cannot be opened from the user's session
    try:
        buf = ctypes.create_unicode_buffer(64)
        need = wt.DWORD(0)
        if not _user32.GetUserObjectInformationW(h, UOI_NAME, buf, ctypes.sizeof(buf), ctypes.byref(need)):
            return False
        return buf.value.lower() != "default"
    finally:
        _user32.CloseDesktop(h)


def log_event(text: str) -> None:
    """One line per unattended event in logs/power.log (wake, start, cancel, power action)."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_DIR / "power.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
    except OSError:
        pass


def perform(action: str) -> str | None:
    """Run the power action.  Returns an error text, or None on success.  Sleep returns after the wake."""
    try:
        if action == "shutdown":
            # No /f: an app with unsaved work may still veto the shutdown.
            r = subprocess.run(["shutdown.exe", "/s", "/t", "0"], capture_output=True,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            return None if r.returncode == 0 else f"shutdown 退出码 {r.returncode}"
        if action == "hibernate":
            r = subprocess.run(["shutdown.exe", "/h"], capture_output=True,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            return None if r.returncode == 0 else f"shutdown /h 退出码 {r.returncode}"
        if action == "sleep":
            prev = ctypes.c_bool()
            ctypes.WinDLL("ntdll").RtlAdjustPrivilege(SE_SHUTDOWN_PRIVILEGE, True, False, ctypes.byref(prev))
            # bHibernate=False, bForce=False, bWakeupEventsDisabled=False (the next wake timer must still fire)
            ok = ctypes.WinDLL("powrprof", use_last_error=True).SetSuspendState(False, False, False)
            return None if ok else f"SetSuspendState 失败（错误码 {ctypes.get_last_error()}）"
        return None
    except OSError as e:
        return repr(e)
