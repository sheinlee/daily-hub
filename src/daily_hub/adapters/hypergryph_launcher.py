"""Read-only evidence from 鹰角启动器's own log (games.log) for clicking its "开始游戏" button safely.

Evidence: docs/research/phase2/maaend.md and the live log (lines like
  [10-16 13:54:14.580] [info] [thread 34672] cvwg enter DoMainBtnClicked m_strUUId:<uuid>).
- Right after the launcher opens it briefly logs start_game, then ~0.6-2.5 s later the version
  query returns and the real state (e.g. update_game) is logged.  So a freshly opened launcher is
  only trusted after a version-query line, and the state must stay unchanged for a while.
- The launcher remembers the last game page (Arknights is installed too); clicking on another page
  starts / updates that game.  The last "SetUUIdForFirstShow" is the page shown.
- A click is confirmed by "DoMainBtnClicked m_strUUId:<Endfield>" and then "create game process".
The file also contains account data: only the fields matched below are ever extracted, nothing
else is logged or stored.
"""
from __future__ import annotations

import glob
import os
import re
import time

ENDFIELD_UUID = "24dab91fe61d1e55938b25b74963cbc5"
LOG_GLOB = os.path.join(os.path.expanduser("~"), "AppData", "LocalLow", "Hypergryph", "*", "logs", "games.log")

_STATE = re.compile(r"OnMainBtnStateInfoChanged info:strUUID:" + ENDFIELD_UUID + r", eDownloadBtnState:\d+, strState:([a-z_]+)")
_PAGE = re.compile(r"SetUUIdForFirstShow strUUID:([0-9a-f]{32})")
_QUERY = re.compile(r"OnQuery_ProtocolVer_LauncherVer_GameVer_Result|ParseLatestGameJson")
_CLICK = re.compile(r"enter DoMainBtnClicked m_strUUId:([0-9a-f]{32})")
_CREATE = re.compile(r"create game process m_strUUID:" + ENDFIELD_UUID)
_UPDATING = re.compile(r"strUUID:" + ENDFIELD_UUID + r", eDownloadBtnState:\d+, strState:"
                       r"(update_game|download_game|downloading|installing|continue_download|checking_\w+)"
                       r"|_ShowUpdateGameMsgBoxIfNeed")

STABLE_S = 8          # the start_game state must not change for this long before clicking
TAIL_BYTES = 8_000_000


def log_path() -> str | None:
    files = []
    for p in glob.glob(LOG_GLOB):
        try:
            files.append((os.path.getmtime(p), p))
        except OSError:
            continue
    return max(files)[1] if files else None


def _read(path: str, start: int | None) -> str:
    """Bytes from `start` (or the last TAIL_BYTES) to EOF; the file is closed right after."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            begin = max(0, size - TAIL_BYTES) if start is None else min(start, size)
            f.seek(begin)
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _size(path: str | None) -> int:
    try:
        return os.path.getsize(path) if path else 0
    except OSError:
        return 0


class HypergryphLauncherProbe:
    def __init__(self):
        self.path = log_path()
        self.start = 0
        self.click_at = 0
        self._stable_key = None
        self._stable_since = 0.0

    def begin(self) -> None:
        """Remember where the log ends before the hub touches the launcher."""
        self.path = log_path()
        self.start = _size(self.path)

    def current_state(self) -> str | None:
        if not self.path:
            return None
        states = _STATE.findall(_read(self.path, None))
        return states[-1] if states else None

    def ready(self, newly_started: bool) -> tuple[bool | None, str]:
        """True = safe to click; False = must not click (reason); None = keep waiting."""
        if not self.path:
            return False, "找不到鹰角启动器日志（games.log），无法确认按钮状态"
        text = _read(self.path, self.start if newly_started else None)
        if newly_started and not _QUERY.search(text):
            return None, "等待启动器完成版本检查"
        pages = _PAGE.findall(text if newly_started else _read(self.path, None))
        if not pages:
            if newly_started:
                return None, "等待启动器显示游戏页"
            return False, "无法从启动器日志确认当前显示的是哪个游戏页"
        if pages[-1] != ENDFIELD_UUID:
            return False, "启动器当前停在其他游戏页（不是终末地），请在启动器里切到终末地后重试"
        states = list(_STATE.finditer(text))
        if not states:
            return (None, "等待启动器写出终末地按钮状态") if newly_started else (False, "日志里没有终末地按钮状态")
        last = states[-1]
        if last.group(1) != "start_game":
            return False, f"启动器主按钮状态是「{last.group(1)}」，不是“开始游戏”（可能在更新或下载）"
        # The whole log line (it carries a timestamp) identifies this state change.
        line_start = text.rfind("\n", 0, last.start()) + 1
        line_end = text.find("\n", last.end())
        key = text[line_start: line_end if line_end >= 0 else len(text)]
        now = time.monotonic()
        if key != self._stable_key:
            self._stable_key, self._stable_since = key, now
        if now - self._stable_since < STABLE_S:
            return None, "确认“开始游戏”状态稳定"
        return True, "start_game"

    def mark_click(self) -> None:
        self.click_at = _size(self.path)

    def clicked(self) -> tuple[bool | None, str]:
        """After the click: True = Endfield is being started, False = something else happened, None = not yet."""
        text = _read(self.path, self.click_at) if self.path else ""
        clicks = _CLICK.findall(text)
        if clicks and clicks[0] != ENDFIELD_UUID:
            return False, "点击落在了其他游戏的按钮上"
        if _UPDATING.search(text):
            return False, "点击后启动器开始更新/下载或弹出更新提示，需人工处理"
        if clicks and _CREATE.search(text):
            return True, "启动器已创建终末地进程"
        return None, "等待启动器响应点击" if not clicks else "启动器已收到点击，等待创建游戏进程"
