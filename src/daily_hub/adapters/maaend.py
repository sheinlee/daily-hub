"""终末地 · MaaEnd（MXU 界面 + MaaFramework）。

Evidence: docs/research/phase1/maaend.md, docs/research/phase2/maaend.md
- CLI: MaaEnd.exe --autostart -i "<实例名>" --quit-after-run ; a wrong name makes MXU stay hidden.
- Game start: since v2.33.0 MaaEnd has the pre-task "🚀启动游戏（PC）" (instance task
  "__MXU_PRETASK__LaunchGamePC", run by agent/go-service --pretask LaunchGamePC through the Hypergryph
  launcher; options region / launcher path / in-game settings) -> launch mode "tool".  Older versions
  could not start the game on PC: the hub then clicks the launcher's start button (mode "launcher").
- No single-instance protection at all: a second MXU runs in parallel and fights for the game.
- Exit code is always 0.  MXU log debug/YYYY-MM-DD-N.log; per-task results in debug/maafw.log
  as `[Px<pid>]... !!!OnEventNotify!!! ... [msg=Tasker.Task.Succeeded|Failed] [details={...}]`.
- A-class start failures ("任务启动失败", pending update) leave MXU hidden and running.
- Runs foreground-exclusive.  Helpers agent/go-service.exe and agent/cpp-algo.exe.
"""
from __future__ import annotations

import json
import os
import re

from .. import procs
from ..config import LAUNCH_BY_HUB, LAUNCH_BY_TOOL, LAUNCH_VIA_LAUNCHER, GameConfig
from ..logtail import FileTail, newest
from .base import (DONE, FAIL, KEY, WARN, Adapter, LaunchSpec, Rule, Session, instances_text,
                   json_value, read_json)
from .hypergryph_launcher import HypergryphLauncherProbe

_HELPERS = ["MaaEnd.exe", "go-service.exe", "cpp-algo.exe", "MaaPiCli.exe"]
_DANGEROUS_TASKS = {"CloseGamePC", "CloseGame", "__MXU_KILLPROC__", "__MXU_POWER__", "__MXU_LAUNCH__"}
_PRETASK_LAUNCH = "__MXU_PRETASK__LaunchGamePC"
_AGENTS = ["go-service.exe", "cpp-algo.exe"]
_MAAFW = re.compile(r"^\[[\d\- :.]+\]\[\w+\]\[Px(\d+)\]")
_TASK_EVT = re.compile(r"\[msg=Tasker\.Task\.(Succeeded|Failed)\] \[details=(\{.*\})\]")

class MaaEndAdapter(Adapter):
    key = "maaend"
    tool_name = "MaaEnd"
    verified_version = "v2.34.0"   # interface.json version (MXU 2.8.0)
    exits_when_done = True     # --quit-after-run
    # The game checks that its parent is the launcher: MaaEnd's own launch pre-task (via the launcher) first,
    # the hub clicking the launcher's start button second, a direct start as the last fallback.
    launch_modes = (LAUNCH_BY_TOOL, LAUNCH_VIA_LAUNCHER, LAUNCH_BY_HUB)
    rules = [
        Rule.of(DONE, r"kind:\s*tasks-completed|自动执行任务完成，关闭自身"),
        Rule.of(FAIL, r"任务启动失败|任务启动异常|未找到名为|目标实例不存在|检测到待安装更新"),
        Rule.of(WARN, r"^maafw: 任务 .* 失败|^\S+ \S+ ERROR\b"),
        Rule.of(KEY, r"开始执行任务, 数量|任务已提交, task_ids|匹配实例|^maafw: 任务"),
    ]
    hide = [re.compile(r"^\S+ \S+ (DEBUG|TRACE)\b")]

    def version(self, g):
        return json_value(os.path.join(g.tool_dir, "interface.json"), "version")

    @staticmethod
    def exe(g: GameConfig) -> str:
        return os.path.join(g.tool_dir, "MaaEnd.exe")

    @staticmethod
    def config_file(g: GameConfig) -> str:
        return os.path.join(g.tool_dir, "config", "mxu-MaaEnd.json")

    def _config(self, g: GameConfig) -> dict:
        return read_json(self.config_file(g)) or {}

    def instances(self, g: GameConfig) -> list[str]:
        return [i.get("name", "") for i in self._config(g).get("instances", []) if i.get("name")]

    def _instance(self, g: GameConfig) -> dict | None:
        name = str(g.options.get("instance", "")).strip()
        for i in self._config(g).get("instances", []):
            if i.get("name") == name:
                return i
        return None

    @staticmethod
    def _launch_pretask(inst: dict | None) -> dict | None:
        """The instance's "🚀启动游戏（PC）" entry when it is enabled (MXU treats a missing flag as enabled)."""
        for t in (inst or {}).get("tasks", []):
            if t.get("taskName") == _PRETASK_LAUNCH and t.get("enabled", True) is not False:
                return t
        return None

    def problems(self, g):
        p = super().problems(g)
        if not os.path.isfile(self.exe(g)):
            p.append(f"找不到 {self.exe(g)}")
            return p
        missing = [a for a in _AGENTS if not os.path.isfile(os.path.join(g.tool_dir, "agent", a))]
        if missing:
            p.append("MaaEnd 缺少 " + "、".join(f"agent\\{a}" for a in missing)
                     + "（更新没装完整），大部分任务会失败：请用完整包重新覆盖安装")
        name = str(g.options.get("instance", "")).strip()
        cfg = self._config(g)
        names = self.instances(g)
        if not name:
            p.append("MaaEnd 实例名为空")
        elif names and name not in names:
            p.append(f"MaaEnd 没有名为「{name}」的实例（现有：{'、'.join(names)}）；名字不对时 MXU 会隐藏挂起")
        settings = cfg.get("settings", {}) if isinstance(cfg, dict) else {}
        if settings.get("autoRunOnLaunch") is True:
            p.append("MaaEnd 开启了“启动时自动运行”（autoRunOnLaunch），会和总控抢调度，请关掉")
        if settings.get("autoStartInstanceId"):
            p.append("MaaEnd 设置了开机自启实例（autoStartInstanceId）：实例名写错时会改跑那个实例，请清掉")
        inst = self._instance(g)
        if inst:
            bad = [t.get("taskName") for t in inst.get("tasks", [])
                   if t.get("enabled") and t.get("taskName") in _DANGEROUS_TASKS]
            if bad:
                p.append(f"实例「{name}」启用了 {'、'.join(bad)}（会按进程名关游戏/关机），请在 MaaEnd 里关掉")
            if any(a.get("enabled", True) for a in inst.get("preActions") or []):
                p.append(f"实例「{name}」有前置动作（preActions），可能重复拉起游戏，请关掉")
            pre = self._launch_pretask(inst)
            mode = self.game_mode(g)
            if mode == LAUNCH_BY_TOOL and pre is None:
                p.append(f"游戏启动方式是“工具”（MaaEnd 自己开游戏），但实例「{name}」没有启用“🚀启动游戏（PC）”："
                         "请在 MaaEnd 的添加任务 → 前置任务里加上并启用，或在总控设置里改用别的启动方式")
            elif mode != LAUNCH_BY_TOOL and pre is not None:
                p.append(f"实例「{name}」启用了“🚀启动游戏（PC）”，总控也会启动游戏，会重复启动："
                         "请在总控设置里把终末地的游戏启动方式改为“工具”，或在 MaaEnd 里关掉这个前置任务")
            if pre is not None:
                exe = (((pre.get("optionValues") or {}).get("LaunchGamePCLauncherExe") or {}).get("values")
                       or {}).get("LauncherExe")
                manual = ((pre.get("optionValues") or {}).get("LaunchGamePCLauncherPath") or {}).get("caseName")
                if manual == "Manual" and exe and not os.path.isfile(str(exe).strip('"')):
                    p.append(f"“🚀启动游戏（PC）”手动指定的启动器不存在：{exe}")
        for i in cfg.get("instances", []) if isinstance(cfg, dict) else []:
            if any(s.get("enabled") for s in i.get("schedulePolicies") or []):
                p.append(f"MaaEnd 实例「{i.get('name')}」有定时策略，会和总控抢调度，请关掉")
        lang = settings.get("language")
        if lang and lang != "zh-CN":
            p.append(f"MaaEnd 界面语言是 {lang}：总控按中文日志判定结果，请改回简体中文")
        cache = os.path.join(g.tool_dir, "cache")
        try:
            pending = [n for n in os.listdir(cache)
                       if n.lower().endswith((".zip", ".exe")) or n in ("update_package", "update_extract")]
        except OSError:
            pending = []
        if pending:
            p.append(f"MaaEnd 有待安装的更新（cache\\{pending[0]}），启动后会在隐藏窗口里自动安装并重启；"
                     "请先手动打开一次 MaaEnd 完成更新")
        return p

    def warnings(self, g):
        w = []
        inst = self._instance(g)
        pre = self._launch_pretask(inst)
        if pre is not None:
            opts = pre.get("optionValues") or {}
            region = (opts.get("LaunchGamePCRegion") or {}).get("caseName", "CN")
            if region != "CN":
                w.append(f"“🚀启动游戏（PC）”的区服是 {region}，不是官服（CN）")
            if (opts.get("LaunchGamePCGameSetting") or {}).get("value") is True:
                w.append("“🚀启动游戏（PC）”会在启动前改游戏设置（语言、显示模式、画质、帧率、自动 HDR）")
        if inst:
            option_key = {"AutoEssence": "AutoUseSpMedication", "ProtocolSpace": "ProtocolSpaceUseSpMedication"}
            for t in inst.get("tasks", []):
                if not t.get("enabled") or t.get("taskName") not in option_key:
                    continue
                opt = (t.get("optionValues") or {}).get(option_key[t["taskName"]]) or {}
                if opt.get("caseName", "UseMedication") != "EndTask":
                    w.append(f"实例「{inst.get('name')}」的 {t.get('taskName')} 在理智不足时会使用应急理智加强剂"
                             "（补充道具）；不想消耗请在 MaaEnd 里把“理智不足时”改为结束任务")
        settings = self._config(g).get("settings", {})
        if settings.get("autoClearLogsOnLaunch") is True:
            w.append("MaaEnd 每次启动会清掉上一轮日志（autoClearLogsOnLaunch），要留存请自行备份 debug 目录")
        if (settings.get("hotkeys") or {}).get("globalEnabled") is True:
            w.append("MaaEnd 开启了全局热键（F10 开始 / F11 停止），运行期间别按这两个键")
        if settings.get("allowLanAccess") is True:
            w.append("MaaEnd 的内置网页服务允许局域网访问（allowLanAccess），且没有鉴权")
        opt = read_json(os.path.join(g.tool_dir, "config", "maa_option.json")) or {}
        if opt.get("logging") is False:
            w.append("MaaFramework 日志被关闭（maa_option.json logging=false），总控看不到逐任务结果")
        return w

    def markers(self, g):
        return [("tasks-completed", ["MaaEnd.exe"]), ("自动执行任务完成，关闭自身", ["MaaEnd.exe"])]

    def running_instances(self, g):
        found = procs.under_dir(g.tool_dir, _HELPERS)
        seen = {i["pid"] for i in found}
        found += [i for i in procs.by_names(["MaaEnd.exe", "mxu.exe"]) if i["pid"] not in seen]
        return instances_text(found)

    def launch_spec(self, g):
        name = str(g.options.get("instance", "")).strip()
        return LaunchSpec(
            argv=[self.exe(g), "--autostart", "-i", name, "--quit-after-run"],
            cwd=g.tool_dir, env=procs.clean_env(drop_prefixes=("WEBVIEW2_",)), console=False, capture=False,
        )

    def log_tail(self, g, since):
        debug = os.path.join(g.tool_dir, "debug")
        mxu = newest(os.path.join(debug, "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]-*.log"), since, limit=2)
        maafw = os.path.join(debug, "maafw.log")
        return FileTail(lambda: mxu() + ([maafw] if os.path.exists(maafw) else []), since)

    def transform(self, lines, session: Session):
        """MXU lines pass through; maafw lines are reduced to this process's per-task results."""
        out = []
        for line in lines:
            m = _MAAFW.match(line)
            if not m:
                out.append(line)
                continue
            if int(m.group(1)) != session.popen.pid:
                continue
            ev = _TASK_EVT.search(line)
            if not ev:
                continue
            try:
                entry = json.loads(ev.group(2)).get("entry", "?")
            except ValueError:
                entry = "?"
            out.append(f"maafw: 任务 {entry} {'成功' if ev.group(1) == 'Succeeded' else '失败'}")
        return out

    def open_targets(self, g):
        return {"工具目录": g.tool_dir, "配置": os.path.join(g.tool_dir, "config"),
                "日志": os.path.join(g.tool_dir, "debug")}

    def option_choices(self, g):
        return {"instance": self.instances(g)}

    def option_labels(self):
        return {"instance": "MaaEnd 实例名（-i）"}

    def extra_cleanup_names(self):
        # MXU may relaunch itself after a silent self-update; nothing ran from this install before we started.
        return ["MaaEnd.exe", "go-service.exe", "cpp-algo.exe"]

    def launcher_probe(self, g):
        return HypergryphLauncherProbe()
