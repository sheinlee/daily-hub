"""明日方舟（模拟器）· MAA（MaaAssistantArknights GUI，实为 v6.19）。

Evidence: docs/research/phase1/misc-tools.md (MAA part) and config/gui.new.json / debug/gui.log.
- There is no "run now" CLI: MAA.exe --config <name> opens that configuration, and it only starts on
  its own when Configurations.<name>.Gui.StartUpSettings.RunDirectly is true.  The run path never
  edits MAA's config; the user switches it on (in MAA or on the hub's 日常设置 tab, settings/maa.py).
- Connects over ADB (MuMu 12 co-branded build, 127.0.0.1:16384, its own adb.exe); the "开始唤醒"
  task starts Arknights inside the emulator.  The hub starts / shuts down the emulator.
- Single instance per install path: a second launch only activates the first -> block.
- gui.log markers (only [TaskQueueViewModel] lines are reliable): "开始任务: X", "完成任务: X",
  "任务出错: X", "任务已全部完成！" (printed even when tasks failed), "Post actions: ...".
- Closing the window while a task runs pops a modal confirm; after completion it closes normally.
- Auto-update (global) can restart MAA and skip the auto-run after the update.
"""
from __future__ import annotations

import os
import re

from .. import procs, winapi
from ..config import LAUNCH_BY_HUB, LAUNCH_BY_TOOL, GameConfig
from ..logtail import FileTail
from .base import DONE, FAIL, KEY, WARN, Adapter, LaunchSpec, Rule, instances_text, read_json

_DANGER_POST = ("shutdown", "hibernate", "sleep", "lock")


class MaaArknightsAdapter(Adapter):
    key = "maa"
    tool_name = "MAA"
    exits_when_done = False          # unless the configuration's PostActions include "退出 MAA"
    game_gone_grace = 180            # MAA may restart the emulator when ADB fails
    launch_modes = (LAUNCH_BY_HUB, LAUNCH_BY_TOOL)   # tool = MAA's own "启动模拟器" setting
    verified_version = "6.19.0"
    rules = [
        Rule.of(DONE, r"\[TaskQueueViewModel\].*任务已全部完成"),
        Rule.of(FAIL, r"Configuration .* does not exist|Secondary launch detected"),
        Rule.of(WARN, r"\[TaskQueueViewModel\].*任务出错|\]\[ERR\]\[TaskQueueViewModel\]"),
        Rule.of(KEY, r"\[TaskQueueViewModel\].*(LinkStart Enter|开始任务:|Post actions)"),
    ]

    @staticmethod
    def exe(g: GameConfig) -> str:
        return os.path.join(g.tool_dir, "MAA.exe")

    def version(self, g):
        return winapi.file_version(self.exe(g)) if os.path.isfile(self.exe(g)) else None

    def _gui(self, g: GameConfig) -> dict:
        return read_json(os.path.join(g.tool_dir, "config", "gui.new.json")) or {}

    def _conf(self, g: GameConfig) -> dict | None:
        name = str(g.options.get("config", "")).strip()
        c = (self._gui(g).get("Configurations") or {}).get(name)
        return c if isinstance(c, dict) else None

    def problems(self, g):
        p = super().problems(g)
        if not os.path.isfile(self.exe(g)):
            p.append(f"找不到 {self.exe(g)}")
            return p
        name = str(g.options.get("config", "")).strip()
        conf = self._conf(g)
        if conf is None:
            names = "、".join((self._gui(g).get("Configurations") or {}).keys()) or "无"
            p.append(f"MAA 没有名为「{name}」的配置（现有：{names}）")
            return p
        gui = conf.get("Gui") or {}
        startup = gui.get("StartUpSettings") or {}
        if startup.get("RunDirectly") is not True:
            p.append(f"MAA 配置「{name}」没勾选“启动后直接运行”，总控启动 MAA 后它不会开跑。"
                     "可以在总控的“日常设置 → 明日方舟 → 启动与收尾”里打开它（打开后手动用这个配置开 MAA 也会直接开跑；"
                     "不想这样就在 MAA 里新建一个总控专用配置）")
        post = str(gui.get("PostActions", "")).lower()
        bad = [d for d in _DANGER_POST if d in post]
        if bad:
            p.append(f"MAA 配置「{name}」的完成后动作包含关机/休眠/睡眠/锁屏（{gui.get('PostActions')}），请改掉")
        return p

    def warnings(self, g):
        w = []
        conf = self._conf(g) or {}
        for t in conf.get("TaskQueue") or []:
            if not isinstance(t, dict) or not t.get("IsEnable"):
                continue
            kind, name = t.get("TaskType"), t.get("Name") or t.get("TaskType")
            if kind == "Fight":
                if t.get("UseStone"):
                    w.append(f"MAA「{name}」会用源石恢复理智（付费货币）")
                if t.get("UseMedicine"):
                    w.append(f"MAA「{name}」会用理智药")
                elif t.get("UseExpiringMedicine"):
                    w.append(f"MAA「{name}」会用即将过期的理智药")
            elif kind == "Recruit":
                w.append(f"MAA「{name}」会消耗招聘许可" + ("，并可能用加急许可" if t.get("UseExpedited") else ""))
            elif kind == "Mall" and t.get("Shopping"):
                w.append(f"MAA「{name}」会用信用点购物")
            elif kind == "Infrast" and t.get("OriginiumShardAutoReplenishment"):
                w.append(f"MAA「{name}」会自动补充源石碎片（消耗材料）")
        gui = conf.get("Gui") or {}
        if (gui.get("StartUpSettings") or {}).get("StartEmulator") and g.launch_mode == LAUNCH_BY_HUB:
            w.append("MAA 自己也开着“启动模拟器”，和总控先开模拟器重复")
        upd = self._gui(g).get("Update") or {}
        if re.search(r"AutoInstallUpdatePackage[^,}]*true", str(upd), re.I) or upd.get("AutoInstallUpdatePackage"):
            w.append("MAA 开着自动安装更新：启动时若有更新会重启，重启后不会自动开跑")
        return w

    def markers(self, g):
        return [("任务已全部完成", ["MAA.dll"]), ("TaskQueueViewModel", ["MAA.dll"])]

    def running_instances(self, g):
        return instances_text(procs.under_dir(g.tool_dir, ["MAA.exe", "MAA.Updater.exe"]))

    def launch_spec(self, g):
        name = str(g.options.get("config", "")).strip()
        return LaunchSpec(argv=[self.exe(g), "--config", name], cwd=g.tool_dir,
                          env=procs.clean_env(drop_prefixes=("DOTNET_", "COMPLUS_", "CORECLR_")),
                          console=False, capture=False)

    def log_tail(self, g, since):
        path = os.path.join(g.tool_dir, "debug", "gui.log")
        return FileTail(lambda: [path] if os.path.exists(path) else [], since)

    def show(self, line):
        return "[TaskQueueViewModel]" in line or "][ERR][" in line

    def open_targets(self, g):
        return {"工具目录": g.tool_dir, "配置": os.path.join(g.tool_dir, "config"),
                "日志": os.path.join(g.tool_dir, "debug")}

    def option_choices(self, g):
        return {"config": list((self._gui(g).get("Configurations") or {}).keys())}

    def option_labels(self):
        return {"config": "MAA 配置名（--config）"}

    def extra_cleanup_names(self):
        return ["MAA.exe", "MAA.Updater.exe"]
