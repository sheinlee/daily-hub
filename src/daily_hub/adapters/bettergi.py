"""原神 · BetterGI（.NET 8，一条龙）。

Evidence: docs/research/phase1/bettergi.md, docs/research/phase2/bettergi.md
- CLI: BetterGI.exe startOneDragon "<配置名>" ; the name must equal "Name" in exactly one
  User/OneDragon/<Name>.json, otherwise BetterGI logs "未找到，请检查。" and runs the selected config.
- Single instance = named pipe \\\\.\\pipe\\BetterGI.v2.user-<SID>.root (no mutex).  A second process
  forwards its arguments to the first, which silently ignores startOneDragon -> must block.
- Not elevated: it re-launches itself via runas and exits 553 -> the hub must be admin.
- Does not exit after the run unless the config's CompletionAction closes it, so the hub watches
  "一条龙和配置组任务结束" and then closes it (WM_CLOSE -> save config -> exit; kill as fallback).
- Log: <dir>/log/better-genshin-impactYYYYMMDD.log, records
  "[HH:MM:SS.mmm] [LVL] [Primary:S1:P<pid>:T<ticks>] <Class>" + message line(s).
- The Genshin path stored in BetterGI's config is stale on this PC -> the hub launches YuanShen.exe.
"""
from __future__ import annotations

import glob
import os
import re

from .. import procs, winapi
from ..config import GameConfig
from ..logtail import FileTail
from .base import (ABORT, DONE, FAIL, KEY, WARN, Adapter, LaunchSpec, Rule, Session, instances_text,
                   read_json)

_HEADER = re.compile(r"^\[(\d\d:\d\d:\d\d)\.\d+\] \[(\w+)\] \[[A-Za-z]+:S\d+:P(\d+):T\d+\]")


class BetterGIAdapter(Adapter):
    key = "bettergi"
    tool_name = "BetterGI"
    verified_version = "0.66.0"
    exits_when_done = False
    rules = [
        Rule.of(DONE, r"一条龙和配置组任务结束"),
        # Name not found -> BetterGI falls back to the selected config: stop before it spends resin.
        Rule.of(ABORT, r"未找到，请检查。"),
        # Anything other than original/condensed resin being used is an unauthorised resource.
        Rule.of(ABORT, r"：使用 \"(?!原粹树脂\"|浓缩树脂\")[^\"]+\", 数量"),
        Rule.of(FAIL, r"没有配置,退出执行|任务被取消，退出执行|一条龙在启动阶段被取消|检测到停止指令，退出启动等待|"
                      r"任务启动失败：当前存在正在运行中的独立任务"),
        Rule.of(WARN, r"\[ERR\]|任务中断:|执行配置组任务时失败|执行异常："),
        Rule.of(KEY, r"启用一条龙配置|一条龙任务执行|启用一条龙任务的数量|原粹树脂：\d+|：使用 \"|检查每日奖励结果"),
    ]
    hide = [re.compile(r"\[(DBG|VRB)\]")]

    def version(self, g):
        return winapi.file_version(self.exe(g)) if os.path.isfile(self.exe(g)) else None

    @staticmethod
    def exe(g: GameConfig) -> str:
        return os.path.join(g.tool_dir, "BetterGI.exe")

    def _configs(self, g: GameConfig) -> list[tuple[str, dict]]:
        out = []
        for f in sorted(glob.glob(os.path.join(g.tool_dir, "User", "OneDragon", "*.json"))):
            data = read_json(f)
            if isinstance(data, dict):
                out.append((f, data))
        return out

    def config_names(self, g: GameConfig) -> list[str]:
        return [d.get("Name") for _, d in self._configs(g) if d.get("Name")]

    def problems(self, g):
        p = super().problems(g)
        if not os.path.isfile(self.exe(g)):
            p.append(f"找不到 {self.exe(g)}")
            return p
        name = str(g.options.get("onedragon_config", "")).strip()
        matches = [(f, d) for f, d in self._configs(g) if d.get("Name") == name]
        if not name:
            p.append("BetterGI 一条龙配置名为空（为空时会执行当前选中的配置）")
        elif len(matches) != 1:
            names = "、".join(self.config_names(g)) or "无"
            p.append(f"BetterGI 一条龙配置「{name}」匹配到 {len(matches)} 个（现有：{names}）；"
                     "名字不对时 BetterGI 会改跑当前选中的配置")
        else:
            f, d = matches[0]
            if os.path.splitext(os.path.basename(f))[0] != name:
                p.append(f"一条龙配置文件名 {os.path.basename(f)} 与 Name「{name}」不一致，BetterGI 会另建文件")
            if d.get("NextTaskId"):
                p.append(f"一条龙配置「{name}」带有续跑标记 NextTaskId（会跳过前面的任务），请在 BetterGI 里从头运行一次或清掉")
            if str(d.get("CompletionAction", "")).strip() == "关机":
                p.append(f"一条龙配置「{name}」完成后动作是“关机”，请在 BetterGI 里改掉")
        cfg = read_json(os.path.join(g.tool_dir, "User", "config.json")) or {}
        if ((cfg.get("otherConfig") or {}).get("autoRestartConfig") or {}).get("enabled") is True:
            p.append("BetterGI 开启了自动重启（otherConfig.autoRestartConfig），会打乱进程跟踪，请关掉")
        return p

    def warnings(self, g):
        w = []
        cfg = read_json(os.path.join(g.tool_dir, "User", "config.json")) or {}
        dom = cfg.get("autoDomainConfig") or {}
        if dom.get("specifyResinUse") is False:
            w.append("BetterGI 自动秘境为“用尽模式”：会先用光浓缩树脂再用原粹树脂")
        if dom.get("autoArtifactSalvage") is True:
            star = (cfg.get("autoArtifactSalvageConfig") or {}).get("maxArtifactStar", "?")
            w.append(f"BetterGI 自动秘境结束后会快速分解 {star} 星及以下圣遗物（不可逆）")
        if (cfg.get("commonConfig") or {}).get("exitToTray") is True:
            w.append("BetterGI 关闭时最小化到托盘（exitToTray）：总控只能强制结束它")
        if (cfg.get("scriptConfig") or {}).get("autoUpdateBeforeCommandLineRun") is True:
            w.append("BetterGI 命令行运行前会自动更新脚本（autoUpdateBeforeCommandLineRun）")
        return w

    def markers(self, g):
        return [("一条龙和配置组任务结束", ["BetterGI.exe", "*.dll"])]

    def running_instances(self, g):
        out = instances_text(procs.by_names(["BetterGI.exe", "BetterGI.update.exe"]))
        try:
            # Only list the pipe namespace; opening the pipe would count as an IPC client.
            pipes = [n for n in os.listdir("\\\\.\\pipe\\") if n.startswith("BetterGI.v2.")]
        except OSError:
            pipes = []
        if pipes:
            out.append(f"BetterGI 的实例管道存在：{pipes[0]}（已有 BetterGI 在运行）")
        return out

    def launch_spec(self, g):
        name = str(g.options.get("onedragon_config", "")).strip()
        env = procs.clean_env(drop_prefixes=("DOTNET_", "COMPLUS_", "CORECLR_"))
        return LaunchSpec(argv=[self.exe(g), "startOneDragon", name], cwd=g.tool_dir,
                          env=env, console=False, capture=False)

    def log_tail(self, g, since):
        log_dir = os.path.join(g.tool_dir, "log")

        def resolve():
            files = []
            for p in glob.glob(os.path.join(log_dir, "better-genshin-impact*.log")):
                try:
                    files.append((os.path.getmtime(p), p))
                except OSError:  # deleted by BetterGI's log retention between glob and stat
                    continue
            return [p for _, p in sorted(files)[-2:]]
        return FileTail(resolve, since)

    def transform(self, lines, session: Session):
        """Join header + message lines and keep only records written by our BetterGI process."""
        st = session.state.setdefault("bgi", {"hdr": None, "mine": False})
        out = []
        for line in lines:
            m = _HEADER.match(line)
            if m:
                st["mine"] = int(m.group(3)) == session.popen.pid
                st["hdr"] = (m.group(1), m.group(2))
                continue
            if not st["mine"] or not line.strip():
                continue
            t, lvl = st["hdr"] or ("", "")
            out.append(f"{t} [{lvl}] {line.strip()}")
        return out

    def open_targets(self, g):
        return {"工具目录": g.tool_dir, "配置": os.path.join(g.tool_dir, "User", "OneDragon"),
                "日志": os.path.join(g.tool_dir, "log")}

    def option_choices(self, g):
        return {"onedragon_config": self.config_names(g)}

    def option_labels(self):
        return {"onedragon_config": "BetterGI 一条龙配置名"}
