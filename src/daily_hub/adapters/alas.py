"""碧蓝航线（模拟器）· Alas（AzurLaneAutoScript，AlasApp 0.3.7 自带 toolkit Python）。

Evidence: docs/research/phase1/misc-tools.md (Alas part), alas.py:492-614, log/YYYY-MM-DD_<config>.txt.
- Alas is a resident scheduler: it runs every due task, then logs
  "Wait until <time> for task `X`" and sleeps until the next one.  It never exits by itself, so that
  line is the "today's queue is done" signal and the hub stops it.
- Started headless with its own interpreter: toolkit\\python.exe, cwd = AzurLaneAutoScript, running
  AzurLaneAutoScript('<config>').loop() (alas.py's __main__ does the same with 'alas').  Running it this
  way does not start the WebUI (gui.py listens on 0.0.0.0:22267 without a password).
- Failure: "Request human takeover", "Task `X` failed 3 or more times".  GameStuckError etc. are
  retried by Alas (game restart) and count as warnings.
- Emulator: config EmulatorInfo = MuMuPlayer12 instance 0; Alas itself controls it with
  "MuMuManager.exe api -v 0 launch_player / shutdown_player" (module/device/platform/platform_windows.py:95-162).
"""
from __future__ import annotations

import os
import re

from .. import procs
from ..config import LAUNCH_BY_HUB, LAUNCH_BY_TOOL, GameConfig
from ..logtail import FileTail, newest
from .base import DONE, FAIL, KEY, WARN, Adapter, LaunchSpec, Rule, instances_text, read_json


class AlasAdapter(Adapter):
    key = "alas"
    tool_name = "Alas"
    exits_when_done = False
    game_gone_grace = 180       # Alas may restart the emulator itself
    launch_modes = (LAUNCH_BY_HUB, LAUNCH_BY_TOOL)   # tool = Alas starts the emulator from EmulatorInfo
    rules = [
        Rule.of(DONE, r"\| INFO \| Wait until .* for task"),
        Rule.of(FAIL, r"Request human takeover|failed 3 or more times|RequestHumanTakeover|EmulatorNotRunningError"),
        Rule.of(WARN, r"\| (ERROR|CRITICAL) \|"),
        Rule.of(KEY, r"Start scheduler loop|Scheduler: Start task"),
    ]

    # Alas auto-updates by `git reset --hard origin/master` on every launch: the version is the commit.
    verified_version = "7d6a415e3"

    def version(self, g):
        """Short commit of the AzurLaneAutoScript checkout, read from .git without running git.

        The loose ref wins over packed-refs (packed-refs keeps a stale master); a detached HEAD holds the sha."""
        git = os.path.join(g.tool_dir, ".git")
        try:
            with open(os.path.join(git, "HEAD"), encoding="utf-8") as f:
                head = f.read().strip()
        except OSError:
            return None
        sha = head
        if head.startswith("ref:"):
            ref = head[4:].strip()
            sha = ""
            try:
                with open(os.path.join(git, *ref.split("/")), encoding="utf-8") as f:
                    sha = f.read().strip()
            except OSError:
                try:
                    with open(os.path.join(git, "packed-refs"), encoding="utf-8") as f:
                        for line in f:
                            parts = line.split()
                            if len(parts) == 2 and parts[1] == ref:
                                sha = parts[0]
                except OSError:
                    pass
        return sha[:9] if re.fullmatch(r"[0-9a-f]{40}", sha) else None

    @staticmethod
    def python(g: GameConfig) -> str:
        return os.path.join(g.tool_dir, "toolkit", "python.exe")

    def _name(self, g: GameConfig) -> str:
        return str(g.options.get("config", "alas")).strip() or "alas"

    def _config(self, g: GameConfig) -> dict:
        return read_json(os.path.join(g.tool_dir, "config", f"{self._name(g)}.json")) or {}

    def problems(self, g):
        p = super().problems(g)
        if not os.path.isfile(self.python(g)):
            p.append(f"找不到 Alas 自带解释器：{self.python(g)}")
        if not os.path.isfile(os.path.join(g.tool_dir, "alas.py")):
            p.append(f"找不到 {g.tool_dir}\\alas.py")
        name = self._name(g)
        if not re.fullmatch(r"[A-Za-z0-9_\-]+", name):
            p.append(f"Alas 配置名「{name}」无效")
        elif not os.path.isfile(os.path.join(g.tool_dir, "config", f"{name}.json")):
            p.append(f"Alas 没有配置 config\\{name}.json")
        patch = self._patch(g)
        if patch and not os.path.isfile(patch):
            p.append(f"找不到高分辨率补丁：{patch}（不用补丁就把这一项清空）")
        return p

    @staticmethod
    def _patch(g: GameConfig) -> str:
        """Absolute path of the hires_patch runner; a relative value is relative to the AzurLaneAutoScript folder."""
        value = str(g.options.get("hires_patch", "")).strip()
        return os.path.normpath(os.path.join(g.tool_dir, value)) if value else ""

    def warnings(self, g):
        cfg = self._config(g)
        on = [k for k, v in cfg.items()
              if isinstance(v, dict) and isinstance(v.get("Scheduler"), dict) and v["Scheduler"].get("Enable")]
        w = []
        if on:
            w.append(f"Alas 启用了 {len(on)} 个任务（{'、'.join(on[:8])}{'…' if len(on) > 8 else ''}），"
                     "会消耗石油等资源；涉及钻石/魔方的选项请在 Alas 里自查")
        w.append("Alas 跑到“当前没有到期任务”就算这一轮完成；之后到期的任务要等下次运行")
        return w

    def markers(self, g):
        return [("Wait until {task.next_run} for task", ["alas.py"])]

    def running_instances(self, g):
        root = os.path.dirname(g.tool_dir)   # AlasApp_0.3.7 (Electron shell + AzurLaneAutoScript)
        found = [i for i in procs.under_dir(root) if i["name"].lower() != "adb.exe"]   # an idle adb server is fine
        seen = {i["pid"] for i in found}
        found += [i for i in procs.find_processes(
            lambda i: procs.is_python_image(i["name"]) and "alas" in " ".join(i["cmdline"]).lower()
            and ("alas.py" in " ".join(i["cmdline"]).lower() or "gui.py" in " ".join(i["cmdline"]).lower()))
            if i["pid"] not in seen]
        return instances_text(found)

    def launch_spec(self, g):
        name = self._name(g)
        env = procs.clean_env(extra={"PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"})
        patch = self._patch(g)
        if patch:
            # a custom runner script (e.g. one that patches Alas first) that ends with AzurLaneAutoScript(name).loop()
            argv = [self.python(g), patch, name]
        else:
            argv = [self.python(g), "-c", f"from alas import AzurLaneAutoScript; AzurLaneAutoScript({name!r}).loop()"]
        return LaunchSpec(argv=argv, cwd=g.tool_dir, env=env, console=True, capture=False)

    def log_tail(self, g, since):
        return FileTail(newest(os.path.join(g.tool_dir, "log", f"*_{self._name(g)}.txt"), since, limit=2), since)

    def show(self, line):
        if re.search(r"[═─│┌┐└┘]{3,}", line):
            return False
        return not re.search(r"\| (INFO|DEBUG) \|", line)

    def open_targets(self, g):
        return {"工具目录": g.tool_dir, "配置": os.path.join(g.tool_dir, "config"),
                "日志": os.path.join(g.tool_dir, "log")}

    def option_choices(self, g):
        names = []
        cfg_dir = os.path.join(g.tool_dir, "config")
        try:
            for f in os.listdir(cfg_dir):
                if f.endswith(".json") and "." not in f[:-5] and not f.startswith("template"):
                    names.append(f[:-5])
        except OSError:
            pass
        return {"config": sorted(names), "hires_patch": []}

    def option_labels(self):
        return {"config": "Alas 配置名", "hires_patch": "自定义启动脚本（可选，留空=原版 Alas）"}
