"""Build the Windows release: dist/DailyHub/ (DailyHub.exe + runtime) and dist/DailyHub-v<version>-win64.zip.

Run from the project folder:  uv run --with pyinstaller python tools/build_release.py
The zip also carries schedule-task.ps1, README.md and LICENSE.  Nothing outside build/ and dist/ is written.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
DIST = ROOT / "dist"
NAME = "DailyHub"


def version() -> str:
    m = re.search(r'^version\s*=\s*"([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else "0.0.0"


def main() -> int:
    ver = version()
    entry = BUILD / "entry.py"
    BUILD.mkdir(exist_ok=True)
    entry.write_text("import sys\nfrom daily_hub.__main__ import main\nsys.exit(main())\n", encoding="utf-8")
    assets = ROOT / "src" / "daily_hub" / "assets"
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--name", NAME,
           "--icon", str(assets / "app.ico"),
           "--paths", str(ROOT / "src"),
           "--collect-submodules", "daily_hub",          # settings providers are imported by name
           "--add-data", f"{assets};daily_hub/assets",
           "--distpath", str(DIST), "--workpath", str(BUILD / "work"), "--specpath", str(BUILD),
           str(entry)]
    print(" ".join(cmd))
    subprocess.run(cmd, check=True, cwd=ROOT)
    app = DIST / NAME
    for extra in ("schedule-task.ps1", "README.md", "LICENSE"):
        if (ROOT / extra).is_file():
            shutil.copy2(ROOT / extra, app / extra)
    zpath = DIST / f"{NAME}-v{ver}-win64.zip"
    if zpath.exists():
        zpath.unlink()
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(app.rglob("*")):
            if p.is_file():
                z.write(p, Path(NAME) / p.relative_to(app))
    print(f"built {zpath} ({zpath.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
