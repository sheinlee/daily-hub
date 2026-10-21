"""崩坏：星穹铁道 · 三月七小助手（March7thAssistant，PyInstaller 冻结版）。

Evidence: docs/research/phase1/march7th.md
- CLI: "March7th Assistant.exe" main  (task names validated by argparse; bad name -> exit 2).
  Env MARCH7TH_GUI_STARTED=true skips every input() pause; MARCH7TH_AFTER_FINISH=None overrides the
  after-finish action (Exit / shutdown / sleep ...) without touching config.yaml.
  Do NOT set MARCH7TH_DOCKER_STARTED (it also bypasses the disclaimer gate).
- Exit code: 0 normal end (but sub-task failures also end with 0), 1 error, 2 bad arguments.
- It starts StarRail itself (cmd start game_path, up to 3 tries) and handles "点击进入"; if the game
  is already open it only checks the resolution and does NOT click into the game -> tool mode only.
  On retries / errors it kills every StarRail.exe by name.
- No single-instance lock for the CLI; the GUI (March7th Launcher.exe) shares config and logs.
- Log: logs/YYYY-MM-DD.log (UTF-8, "time | LEVEL | msg"; hr titles without timestamp).  The console
  output goes through the system ANSI code page, so the hub reads the log file instead of the pipe.
"""
from __future__ import annotations

import os
import re

from .. import procs
from ..config import LAUNCH_BY_TOOL, GameConfig
from ..logtail import FileTail, newest
from .base import DONE, FAIL, KEY, WARN, Adapter, LaunchSpec, Rule, instances_text

_EXES = ["March7th Assistant.exe", "March7th Launcher.exe", "March7th Updater.exe"]
_ALLOWED_TASKS = {"main", "game"}   # main = the full daily run; game = start + log in only (no stamina)


def _yaml_scalar(text: str, key: str) -> str | None:
    m = re.search(rf"^{re.escape(key)}:\s*(.*?)\s*(?:#.*)?$", text, re.M)
    return m.group(1).strip().strip("'\"") if m else None


class March7thAdapter(Adapter):
    key = "march7th"
    tool_name = "三月七小助手"
    exits_when_done = True
    launch_modes = (LAUNCH_BY_TOOL,)
    game_gone_grace = 180       # it kills and restarts the game itself between start-up retries
    verified_version = "v2026.7.26"   # run rules (CLI, log markers, env) were checked against this release
    rules = [
        Rule.of(DONE, r"停止运行"),
        # Only the final error (ErrorOccurred template) ends the run; start-up attempts log
        # "尝试启动游戏时发生错误：..." and are retried up to 3 times by M7A itself.
        Rule.of(FAIL, r"(?<!尝试启动游戏时)发生错误|首次使用请先打开图形界面"),
        Rule.of(WARN, r"尝试启动游戏时发生错误|账号登录过期|尝试自动登录失败|客户端版本过低|游戏分辨率|切换到游戏超时|"
                      r"游戏路径不存在|启动游戏失败|清体力未完成|遗器背包已满|任务无法完成|战斗失败|战斗超时|"
                      r"每日实训未完成|获取当前界面超时"),
        Rule.of(KEY, r"开始运行|开始检测更新|开始日常任务|今日实训|开始领取奖励|实训分数已达标|本日活跃度已满|"
                     r"游戏启动|副本任务完成"),
    ]

    @staticmethod
    def exe(g: GameConfig) -> str:
        return os.path.join(g.tool_dir, "March7th Assistant.exe")

    def version(self, g):
        # The tool itself reads this file: module/config/__init__.py VERSION_PATH, config.py _load_version
        try:
            with open(os.path.join(g.tool_dir, "assets", "config", "version.txt"), encoding="utf-8-sig") as f:
                v = f.readline().strip()
        except OSError:
            return None
        return v or None

    def _config_text(self, g: GameConfig) -> str:
        try:
            with open(os.path.join(g.tool_dir, "config.yaml"), encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""

    def problems(self, g):
        p = super().problems(g)
        if not os.path.isfile(self.exe(g)):
            p.append(f"找不到 {self.exe(g)}")
            return p
        task = str(g.options.get("task", "main")).strip()
        if task not in _ALLOWED_TASKS:
            p.append(f"三月七任务「{task}」不在允许范围（{'、'.join(sorted(_ALLOWED_TASKS))}）")
        text = self._config_text(g)
        game_path = _yaml_scalar(text, "game_path")
        if game_path and not os.path.isfile(game_path):
            p.append(f"三月七配置里的游戏路径不存在：{game_path}")
        disclaimer = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "March7thAssistant", "disclaimer")
        if not os.path.exists(disclaimer):
            p.append("三月七还没同意免责声明：请先手动打开一次 March7th Launcher（否则它会直接退出、什么都不做）")
        return p

    def warnings(self, g):
        w = []
        text = self._config_text(g)
        if _yaml_scalar(text, "use_reserved_trailblaze_power") == "true":
            w.append("三月七会使用后备开拓力（use_reserved_trailblaze_power）")
        if _yaml_scalar(text, "use_fuel") == "true":
            w.append("三月七会使用燃料（use_fuel）")
        if _yaml_scalar(text, "update_via_launcher") == "true":
            w.append("三月七开着“通过启动器更新游戏”")
        sched = _yaml_scalar(text, "scheduled_tasks")
        if sched not in (None, "[]"):
            w.append("三月七界面里设置了定时任务，可能和总控抢调度")
        w.append("三月七启动游戏时会临时改分辨率注册表、关闭 AutoHDR，并把游戏内自动战斗/倍速设置写成开启")
        return w

    def markers(self, g):
        return [("停止运行", ["pyz:March7th Assistant.exe"])]

    def running_instances(self, g):
        found = procs.under_dir(g.tool_dir, _EXES)
        found += procs.under_dir(os.path.join(g.tool_dir, "3rdparty"))
        return instances_text(found)

    def launch_spec(self, g):
        task = str(g.options.get("task", "main")).strip() or "main"
        env = procs.clean_env(extra={"MARCH7TH_GUI_STARTED": "true", "MARCH7TH_AFTER_FINISH": "None"},
                              drop=("MARCH7TH_DOCKER_STARTED",))
        return LaunchSpec(argv=[self.exe(g), task], cwd=g.tool_dir, env=env, console=True, capture=False)

    def log_tail(self, g, since):
        return FileTail(newest(os.path.join(g.tool_dir, "logs", "*.log"), since, limit=2), since)

    def open_targets(self, g):
        return {"工具目录": g.tool_dir, "配置": g.tool_dir, "日志": os.path.join(g.tool_dir, "logs")}

    def option_choices(self, g):
        return {"task": sorted(_ALLOWED_TASKS)}

    def option_labels(self):
        return {"task": "三月七任务（main=完整日常，game=只启动登录）"}
