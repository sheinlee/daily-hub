"""崩坏3 · Maa_bbb（MaaFramework 资源 + MFW-ChainFlow Assistant 界面 MFW.exe）。

Evidence: docs/research/phase1/maafw-others.md (Maa_bbb / CFA parts) and the live gui.log format.
- CLI: MFW.exe --direct-run --config-id <id>; ids come from config/configs/<id>.json ("name").
  The active config is "单独运行", so the id must always be passed.
- PC mode does not start BH3 ("启动并进入游戏" is DoNothing on win32) -> the hub starts BH3.exe.
- Needs admin (Win32 controller permission_required); without it: "this Controller requires admin".
- Same-directory second instance only activates the first ("Program Already Running") -> block.
- Does not exit after the flow unless Post-Action says so; the hub closes it after
  "WHEN_POST_TASK (6): Task Flow Completed".  "崩坏三 启动！" can loop forever if the main menu is
  never reached (maintenance / update / login), so the timeout matters.
- Dev log debug/gui.log: "[YYYY-MM-DD HH:MM:SS,mmm][LEVEL][file.py][Lnnn][func] | message".
"""
from __future__ import annotations

import glob
import json
import os
import re

from .. import procs
from ..config import LAUNCH_BY_HUB, GameConfig
from ..logtail import FileTail
from .base import DONE, FAIL, KEY, WARN, Adapter, LaunchSpec, Rule, instances_text, json_value, read_json

_DANGER_POST = ("shutdown", "run_program", "run_other")


class MaaBbbAdapter(Adapter):
    key = "maabbb"
    tool_name = "Maa_bbb"
    verified_version = "v1.13.4"   # interface.json version (MFW-ChainFlow Assistant 4.10.2)
    exits_when_done = False
    launch_modes = (LAUNCH_BY_HUB,)
    rules = [
        Rule.of(DONE, r"WHEN_POST_TASK \(\d+\): (Task Flow Completed|任务完成)"),
        Rule.of(FAIL, r"WHEN_POST_TASK \(\d+\): (Task Flow Failed|任务失败)|设备连接失败|requires admin permission|"
                      r"Runtime \S+ failed|崩坏三需前往客户端进行更新或是停服维护中|TASK_FLOW_STOP manual=True"),
        Rule.of(WARN, r"任务 '.+?' 执行失败|\]\[ERROR\]\[task_flow\.py\]|识别错误|严重错误"),
        Rule.of(KEY, r"TASK_FLOW_START|任务 '.+?' 的执行信息|TASK_FLOW_STOP"),
    ]

    def version(self, g):
        return json_value(os.path.join(g.tool_dir, "interface.json"), "version")

    @staticmethod
    def exe(g: GameConfig) -> str:
        return os.path.join(g.tool_dir, "MFW.exe")

    def configs(self, g: GameConfig) -> dict[str, dict]:
        """config name -> parsed config file (with its item_id)."""
        out = {}
        for f in sorted(glob.glob(os.path.join(g.tool_dir, "config", "configs", "*.json"))):
            data = read_json(f)
            if isinstance(data, dict) and data.get("name") and data.get("item_id"):
                out[data["name"]] = data
        return out

    def _selected(self, g: GameConfig) -> dict | None:
        return self.configs(g).get(str(g.options.get("config", "")).strip())

    def problems(self, g):
        p = super().problems(g)
        if not os.path.isfile(self.exe(g)):
            p.append(f"找不到 {self.exe(g)}")
            return p
        name = str(g.options.get("config", "")).strip()
        cfg = self._selected(g)
        if cfg is None:
            p.append(f"Maa_bbb 没有名为「{name}」的配置（现有：{'、'.join(self.configs(g)) or '无'}）")
            return p
        for t in cfg.get("tasks", []):
            if t.get("item_id") == "Post-Action" and str(t.get("is_checked")) == "True":
                opt = json.dumps(t.get("task_option") or {}, ensure_ascii=False).lower()
                bad = [d for d in _DANGER_POST if d in opt]
                if bad:
                    p.append(f"Maa_bbb 配置「{name}」的完成后动作包含 {'、'.join(bad)}（关机/运行其他程序），请在 Maa_bbb 里改掉")
        return p

    def warnings(self, g):
        w = []
        cfg = self._selected(g)
        if cfg:
            on = [t.get("name") for t in cfg.get("tasks", [])
                  if str(t.get("is_checked")) == "True" and str(t.get("item_id", "")).startswith("t_")]
            if on:
                w.append(f"Maa_bbb 配置「{cfg.get('name')}」会执行：{'、'.join(on)}")
        settings = read_json(os.path.join(g.tool_dir, "config", "config.json")) or {}
        flat = json.dumps(settings)
        if re.search(r'"auto_update":\s*true', flat):
            w.append("Maa_bbb 开着自动更新：启动时若要整包更新会自动重启，重启后不会再自动运行任务")
        w.append("“崩坏三 启动！”识别不到主菜单（维护、更新、登录失效）时会一直循环，只能等超时")
        return w

    def markers(self, g):
        return [("WHEN_POST_TASK", ["MFW.exe"]), ("Task Flow Completed", ["MFW.exe"])]

    def running_instances(self, g):
        found = procs.under_dir(g.tool_dir, ["MFW.exe", "MFWUpdater.exe", "MFWUpdater1.exe"])
        return instances_text(found)

    def launch_spec(self, g):
        cfg = self._selected(g) or {}
        return LaunchSpec(argv=[self.exe(g), "--direct-run", "--config-id", cfg.get("item_id", "")],
                          cwd=g.tool_dir, env=procs.clean_env(), console=False, capture=False)

    def log_tail(self, g, since):
        path = os.path.join(g.tool_dir, "debug", "gui.log")
        return FileTail(lambda: [path] if os.path.exists(path) else [], since)

    def transform(self, lines, session):
        # Notification bodies copy the whole run log as "[HH:MM:SS][级别]..." lines: drop the copies.
        return [ln for ln in lines if not re.match(r"^\[\d\d:\d\d:\d\d\]\[", ln)]

    def show(self, line):
        # Keep the task-flow narrative and errors; drop debug chatter and the notification body copies.
        if re.match(r"^\[\d\d:\d\d:\d\d\]\[", line) or "][DEBUG][" in line:
            return False
        return "task_flow.py" in line or "][ERROR][" in line or "][WARNING][" in line

    def open_targets(self, g):
        return {"工具目录": g.tool_dir, "配置": os.path.join(g.tool_dir, "config", "configs"),
                "日志": os.path.join(g.tool_dir, "debug")}

    def option_choices(self, g):
        return {"config": list(self.configs(g))}

    def option_labels(self):
        return {"config": "Maa_bbb 配置名"}

    def extra_cleanup_names(self):
        return ["MFW.exe"]
