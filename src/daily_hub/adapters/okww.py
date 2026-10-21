"""鸣潮 · ok-ww（安装版，pyappify 布局）。

Evidence: docs/research/phase1/ok-ww.md, docs/research/phase2/okww.md
- Direct start: <app>\\python\\python.exe main.py --headless -t <task>, cwd = <app>\\working.
  `-h` is swallowed as --help by ok/util/logger.py:179-181 (exit 0, nothing runs) -> never used.
- Never pass -e: TaskExecutor.py:589-595 + DeviceManager.stop_hwnd kill every Client-Win64-Shipping.exe.
- Single instance: named mutex md5(cwd) + %TEMP%\\ok-script-<md5>.pid; a second copy with the same
  exe+cwd+entry kills the first after 5 s, a copy with a different cwd runs in parallel on the same
  game.  The hub refuses to start while ok-ww runs.
- Exit code is 0 for success, task exception and start failure alike -> judge by log lines.
- ok-ww launches the game with -krqlv=<Game Package> (configs/Basic Options.json); when that differs
  from the installed resource package the client may exit early.  The official launcher uses `Wuthering Waves.exe -krqlv=uhd --game Aki`, so by default the hub
  starts the game itself with those arguments and ok-ww just attaches to the window.
"""
from __future__ import annotations

import ast
import glob
import hashlib
import os
import re
import tempfile
from pathlib import Path

import psutil

from .. import procs, winapi
from ..config import GameConfig
from .base import (ABORT, DONE, FAIL, KEY, WARN, Adapter, LaunchSpec, Rule, instances_text, read_json)

_WINDOW_TITLES = ("OK-WW ",)


class OkWwAdapter(Adapter):
    key = "okww"
    tool_name = "ok-ww"
    verified_version = "v3.7.4"   # working/config.py `version` (ok-script 2.0.8 per app.json update_note)
    exits_when_done = True       # --headless exits after the one-time task
    rules = [
        Rule.of(DONE, r"DailyTask:Daily Task Completed"),
        Rule.of(FAIL, r"exception stopped"),
        Rule.of(FAIL, r"\bok:start error\b"),
        Rule.of(FAIL, r"Please start in game world"),
        # Executor paused because the game window is minimized/off-screen: it never resumes headless.
        Rule.of(FAIL, r"og\.executor\.pause pos_invalid"),
        Rule.of(FAIL, r"Uncaught exception"),
        # Reserve waveplates (结晶单质) were used to claim double rewards.
        Rule.of(WARN, r"当前加备用大于日常剩余所需"),
        Rule.of(WARN, r"(NightmareNest|Garden|MergeEcho)Task Failed|can not battle pass"),
        Rule.of(WARN, r"used all stamina|not enough stamina|exceeded recovery retries"),
        Rule.of(WARN, r"Successfully Executed Task, Exiting Game and App!|Killing process\(es\)"),
        # Precheck missed an instance: within 5 s ok-ww would kill a same-signature process -> stop now.
        Rule.of(ABORT, r"Another instance of this application is already running"),
        Rule.of(KEY, r"ok-script init|run one-time task without ui|get queued onetime_task|"
                     r"starting game|task finished without ui|info_set current daily progress|"
                     r"info_set (current_stamina|back_up_stamina)|当前体力大于等于双倍|weekly garden"),
    ]
    hide = [re.compile(r"waiting for game to start error|test check_device_error msg|"
                       r"Game window is not connected|capture_by_bitblt")]

    @staticmethod
    def working(g: GameConfig) -> str:
        return os.path.join(g.tool_dir, "working")

    @staticmethod
    def python(g: GameConfig) -> str:
        return os.path.join(g.tool_dir, "python", "python.exe")

    @staticmethod
    def install_root(g: GameConfig) -> str:
        """...\\ok-ww for the pyappify layout ...\\ok-ww\\data\\apps\\ok-ww; else the tool dir itself."""
        parts = os.path.normpath(g.tool_dir).split(os.sep)
        if len(parts) >= 3 and [x.lower() for x in parts[-3:-1]] == ["data", "apps"]:
            return os.sep.join(parts[:-3])
        return g.tool_dir

    def version(self, g):
        """`version = "v3.7.4"` at module level of working/config.py (read with ast, never imported)."""
        try:
            tree = ast.parse(Path(self.working(g), "config.py").read_bytes())
        except (OSError, SyntaxError, ValueError):
            return None
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "version" for t in node.targets)                     and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                return node.value.value
        return None

    def mutex_name(self, g: GameConfig) -> str:
        return hashlib.md5(self.working(g).encode()).hexdigest()

    def _cfg(self, g: GameConfig, name: str) -> dict:
        return read_json(os.path.join(self.working(g), "configs", name)) or {}

    def problems(self, g):
        p = super().problems(g)
        if not os.path.isfile(self.python(g)):
            p.append(f"找不到 ok-ww 自带解释器：{self.python(g)}")
        if not os.path.isfile(os.path.join(self.working(g), "main.py")):
            p.append(f"找不到 {self.working(g)}\\main.py")
        task = str(g.options.get("task", "")).strip()
        if not task or task.startswith("-"):
            p.append("ok-ww 任务名为空或无效")
        # Settings that would make ok-ww kill the game (by path, not ownership) or quit by itself.
        for name in ("DailyTask.json", "FarmEchoTask.json", "MultiAccountDailyTask.json"):
            if self._cfg(g, name).get("Exit After Task") is True:
                p.append(f"ok-ww 的 {name} 开启了 Exit After Task（成功后会强杀所有鸣潮客户端），请在 ok-ww 里关掉")
        if self._cfg(g, "Basic Options.json").get("Exit App when Game Exits") is True:
            p.append("ok-ww 开启了 Exit App when Game Exits，请在 ok-ww 里关掉")
        tasks_dir = os.path.join(self.working(g), "ok_tasks")
        if os.path.isdir(tasks_dir) and any(f.endswith(".py") for f in os.listdir(tasks_dir)):
            p.append("ok-ww 的 working\\ok_tasks 里有自定义任务脚本（启动时会被执行），请确认后移走")
        return p

    def warnings(self, g):
        w = []
        pkg = str(self._cfg(g, "Basic Options.json").get("Game Package", "")).lower()
        installed = self.installed_package(g)
        if pkg and installed and pkg != installed:
            w.append(f"ok-ww 的 Game Package={pkg}，本机安装的是 {installed}；ok-ww 自己拉起游戏时参数与安装包不一致"
                     f"（这样拉起的客户端可能很快退出）。建议在 ok-ww 设置里改成 {installed}")
        extra = self._cfg(g, "DailyTask.json").get("Additional Tasks to Run After Daily Task") or []
        if extra:
            w.append(f"ok-ww 日常附加任务：{'、'.join(map(str, extra))}（周常乐园没满时会进局内商店购买）")
        w.append("ok-ww 日常在自然结晶波片不够时会主动动用结晶单质（备用体力）领双倍，没有开关可关；"
                 "补充弹窗是盲点两次确认，代码上排除不了动用结晶溶剂/星声")
        if self._cfg(g, "Basic Options.json").get("Launch with DX11") is True:
            w.append("ok-ww 开启了 Launch with DX11：它自己开游戏时会加 DX11 参数")
        return w

    def installed_package(self, g: GameConfig) -> str | None:
        if not g.game_exe:
            return None
        data = read_json(os.path.join(os.path.dirname(g.game_exe), "launcherDownloadConfig.json")) or {}
        bundles = data.get("bundles")
        keys = list(bundles.keys()) if isinstance(bundles, dict) else []
        return keys[0].lower() if len(keys) == 1 else None

    def markers(self, g):
        return [("Daily Task Completed", ["working/src/task/DailyTask.py"])]

    def running_instances(self, g):
        found = procs.under_dir(self.install_root(g))
        marker = os.path.normcase(os.path.join("ok-ww", "data", "apps", "ok-ww"))
        seen = {i["pid"] for i in found}

        def pred(i):
            if i["pid"] in seen or not procs.is_python_image(i["name"]):
                return False
            cmd = os.path.normcase(" ".join(i["cmdline"]))
            cwd = procs.norm(i["cwd"])
            return marker in cmd or cwd == procs.norm(self.working(g))
        found += procs.find_processes(pred)   # cmdline/cwd are read for python*.exe only
        out = instances_text(found)
        # The mutex is md5(cwd).
        for cwd in [self.working(g)]:
            name = hashlib.md5(cwd.encode()).hexdigest()
            if winapi.mutex_exists(name):
                out.append(f"单实例互斥量 {name} 存在（{cwd} 下的 ok 程序正在运行）")
        # Owner files of any ok-script instance (catches a copy started with a differently-spelled cwd).
        for pid_file in glob.glob(os.path.join(tempfile.gettempdir(), "ok-script-*.pid")):
            try:
                with open(pid_file, encoding="ascii") as f:
                    pid = int(f.read().strip())
                proc = psutil.Process(pid)
                if proc.create_time() <= os.path.getmtime(pid_file) + 1 and \
                        procs.norm(proc.exe()).startswith(procs.norm(self.install_root(g)) + os.sep):
                    out.append(f"ok-ww 实例记录 {os.path.basename(pid_file)} 指向正在运行的 PID {pid}")
            except (OSError, ValueError, psutil.Error):
                continue
        for w in winapi.top_level_windows():
            if w.title.startswith(_WINDOW_TITLES):
                out.append(f"发现窗口「{w.title.strip()}」（ok-ww 界面开着）")
        return out

    def launch_spec(self, g):
        task = str(g.options.get("task") or "src.task.DailyTask.DailyTask").strip()
        env = procs.clean_env(path=procs.system_path(), drop=("TMPDIR",), extra={
            "PYTHONNOUSERSITE": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1",
        })
        return LaunchSpec(
            argv=[self.python(g), "main.py", "--headless", "-t", task],
            cwd=self.working(g), env=env, console=True, capture=True,
        )

    def open_targets(self, g):
        w = self.working(g)
        return {"工具目录": w, "配置": os.path.join(w, "configs"), "日志": os.path.join(w, "logs")}

    def option_labels(self):
        return {"task": "ok-ww 任务（-t）"}
