"""Start the daily hub without a console window.

uv's .venv\\Scripts\\pythonw.exe is a byte copy of its console-subsystem python.exe trampoline, so
starting the hub through it always opens a console window that stays until the hub exits.  启动总控.cmd
and the UAC relaunch therefore run this file with the base installation's pythonw.exe (GUI subsystem,
"-I" so no user site / PYTHON* variables leak in); it only puts the venv's packages on sys.path.
"""
import os
import site
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
site.addsitedir(os.path.join(ROOT, ".venv", "Lib", "site-packages"))   # also runs the editable .pth
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from daily_hub.__main__ import main  # noqa: E402

sys.exit(main())
