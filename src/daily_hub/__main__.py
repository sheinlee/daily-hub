"""Entry: python -m daily_hub [--auto [--then sleep|hibernate|shutdown|none]] [--no-elevate]
                            [--screenshot out.png [tab]]"""
from __future__ import annotations

import os
import sys

from . import instance, winapi

# Project-root bootstrap run by the base pythonw.exe (see hub.pyw for why not the venv's pythonw.exe).
BOOT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "hub.pyw")


def elevation_command(args: list[str]) -> tuple[str, list[str]]:
    """(exe, argv) that restarts the hub elevated without opening a console window."""
    if getattr(sys, "frozen", False):          # packaged DailyHub.exe
        return sys.executable, list(args)
    w = winapi.gui_python()
    if w and os.path.isfile(BOOT):
        return w, ["-I", BOOT, *args]
    return sys.executable, ["-m", "daily_hub", *args]


def main() -> int:
    args = sys.argv[1:]
    solo = "--no-elevate" in args or "--screenshot" in args   # debugging starts never join a running hub
    if not solo and instance.forward(args):
        return 0
    if not winapi.is_admin() and "--no-elevate" not in args:
        # One UAC prompt per start; if the user declines we keep going without admin (read-only use).
        if winapi.relaunch_as_admin(*elevation_command(args)):
            return 0
    from .gui import run
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
