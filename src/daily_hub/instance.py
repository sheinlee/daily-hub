"""One hub window: a second start hands its arguments to the running hub and exits.

Only an elevated hub listens (gui.MainWindow.start_ipc, QLocalServer with UserAccessOption = current
user only): a non-elevated hub cannot run anything, so it must not swallow a scheduled --auto start.
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import time

from .config import EDITION

NAME = "daily-hub-" + (EDITION + "-" if EDITION else "") + re.sub(r"\W", "_", os.environ.get("USERNAME", "user"))
PIPE = r"\\.\pipe" + "\\" + NAME
ASFW_ANY = -1


def forward(args: list[str]) -> bool:
    """Send args to a running hub.  True if one took them."""
    msg = (json.dumps({"args": args}, ensure_ascii=False) + "\n").encode("utf-8")
    for _ in range(10):
        try:
            with open(PIPE, "r+b", buffering=0) as f:
                ctypes.windll.user32.AllowSetForegroundWindow(ASFW_ANY)   # let the hub come to the front
                f.write(msg)
                time.sleep(0.5)      # keep the pipe open until the hub has read the line
            return True
        except FileNotFoundError:
            return False
        except OSError:              # ERROR_PIPE_BUSY: the hub is serving another start
            time.sleep(0.3)
    return False


def parse(line: bytes) -> list[str] | None:
    try:
        args = json.loads(line.decode("utf-8"))["args"]
    except (ValueError, KeyError, TypeError):
        return None
    return [str(a) for a in args] if isinstance(args, list) else None
