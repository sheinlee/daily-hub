"""绝区零 · zzz-onedragon（源码 + .venv 版）。

Evidence: docs/research/phase1/zzz-onedragon.md, docs/research/phase2/zzz.md
- CLI: .venv\\Scripts\\python.exe -m zzz_od.application.zzz_application_launcher --instance N
  (src/one_dragon/launcher/launcher_base.py:26-28: -c/--close-game, -s/--shutdown, -i/--instance).
  Never pass -s.  cwd = install dir, PYTHONPATH = <dir>\\src.  Bypasses OneDragon-Launcher.exe
  (which runs git update / uv sync first).
- The tool must open the game itself: when a game window already exists it skips the
  "enter game / login" step, so a hub-launched game sitting on the title screen breaks the run.
- No lock of any kind between GUI / CLI / backend: a second copy runs in parallel and the GUI's
  init resets run records -> the hub refuses to start while any one-dragon process runs.
- Exit code is not a verdict; "指令[ 一条龙 ] 执行成功" also follows per-app failures, so app-level
  failures are warnings.
"""
from __future__ import annotations

import glob
import os
import re

from .. import procs
from ..config import LAUNCH_BY_TOOL, GameConfig
from .base import DONE, FAIL, KEY, WARN, Adapter, LaunchSpec, Rule, instances_text

_LAUNCHER_NAMES = {"onedragon-launcher.exe", "onedragon-runtimelauncher.exe", "onedragon-installer.exe",
                   "onedragon launcher.exe", "onedragon scheduler.exe", "onedragon installer.exe"}
_APP_NAME = re.compile(r"""^\s*APP_NAME\s*(?::\s*str\s*)?=\s*['"]([^'"]+)['"]""", re.M)


def _git_dir(tool_dir: str) -> str | None:
    d = os.path.join(tool_dir, ".git")
    if os.path.isfile(d):                       # worktree / submodule: "gitdir: <path>"
        try:
            with open(d, encoding="utf-8") as fh:
                line = fh.read().strip()
        except OSError:
            return None
        if line.startswith("gitdir:"):
            d = os.path.join(tool_dir, line[7:].strip())
    return d if os.path.isdir(d) else None


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def git_refs(tool_dir: str) -> dict[str, str]:
    """{ref name: sha} from loose refs and packed-refs (peeled '^sha' lines replace annotated tags)."""
    gd = _git_dir(tool_dir)
    if not gd:
        return {}
    refs: dict[str, str] = {}
    last = None
    for line in _read(os.path.join(gd, "packed-refs")).splitlines():
        if line.startswith("^") and last:
            refs[last] = line[1:].strip()
        elif line and not line.startswith("#") and " " in line:
            sha, last = line.split(" ", 1)
            refs[last] = sha
    for sub in ("heads", "remotes", "tags"):
        base = os.path.join(gd, "refs", sub)
        for dirpath, _dirs, files in os.walk(base):
            for f in files:
                p = os.path.join(dirpath, f)
                refs["refs/" + os.path.relpath(p, os.path.join(gd, "refs")).replace("\\", "/")] = _read(p)
    return refs


def git_head(tool_dir: str) -> str | None:
    """Full SHA of HEAD read from .git files (no git process)."""
    gd = _git_dir(tool_dir)
    head = _read(os.path.join(gd, "HEAD")) if gd else ""
    if head.startswith("ref:"):
        return git_refs(tool_dir).get(head[4:].strip()) or None
    return head or None


class ZzzOneDragonAdapter(Adapter):
    key = "zzz_onedragon"
    tool_name = "zzz-onedragon"
    verified_version = "bb44c00d"   # git HEAD (v2.4.5-55); the tool is a source checkout without a version number
    exits_when_done = True
    launch_modes = (LAUNCH_BY_TOOL,)   # see module docstring: a pre-opened game breaks its login flow
    hide = [re.compile(r"\[DEBUG\]")]

    def __init__(self):
        self._rules_for: str | None = None
        self.rules = self._build_rules([])

    @staticmethod
    def _build_rules(app_names: list[str]) -> list[Rule]:
        apps = "|".join(re.escape(a) for a in sorted(set(app_names), key=len, reverse=True))
        rules = [
            Rule.of(DONE, r"指令\[ 一条龙 \] 执行成功"),
            Rule.of(FAIL, r"指令\[ 一条龙 \] 执行失败"),
            Rule.of(FAIL, r"初始化出错|等待应用 one_dragon 初始化超时|应用 one_dragon 未注册|未初始化控制器|"
                          r"运行前初始化失败|请先结束其他运行中的功能 再启动|创建应用 one_dragon 失败|未找到有效的实例"),
        ]
        if apps:
            rules.append(Rule.of(WARN, rf"指令\[ (?:{apps}) \] 执行失败"))
            rules.append(Rule.of(KEY, rf"指令\[ (?:{apps}) \] 执行成功"))
        rules += [
            Rule.of(WARN, r"指令\[ 恢复电量 \] 执行成功 返回状态 恢复电量成功"),   # reserve charge was spent
            Rule.of(KEY, r"指定运行实例|尝试自动启动游戏|指令\[ 打开并登录游戏 \]|应用已完成|剩余电量 \d+"),
        ]
        return rules

    def _ensure_rules(self, g: GameConfig) -> None:
        """App names (APP_NAME constants) come from the installed source, so new apps are covered."""
        if self._rules_for == g.tool_dir:
            return
        names = []
        for f in glob.glob(os.path.join(g.tool_dir, "src", "zzz_od", "application", "**", "*.py"), recursive=True):
            try:
                with open(f, encoding="utf-8") as fh:
                    names += _APP_NAME.findall(fh.read())
            except OSError:
                continue
        self.rules = self._build_rules(names)
        self._rules_for = g.tool_dir

    def version(self, g):
        """Tag name when HEAD sits on a tag, else the short SHA (src/one_dragon/version.py is a v0.0.0 stub)."""
        sha = git_head(g.tool_dir)
        if not sha:
            return None
        tags = sorted(r[10:] for r, s in git_refs(g.tool_dir).items() if r.startswith("refs/tags/") and s == sha)
        return tags[-1] if tags else sha[:8]

    @staticmethod
    def python(g: GameConfig) -> str:
        return os.path.join(g.tool_dir, ".venv", "Scripts", "python.exe")

    def problems(self, g):
        self._ensure_rules(g)
        p = super().problems(g)
        if not os.path.isfile(self.python(g)):
            p.append(f"找不到一条龙虚拟环境解释器：{self.python(g)}")
        if not os.path.isfile(os.path.join(g.tool_dir, "src", "zzz_od", "application",
                                           "zzz_application_launcher.py")):
            p.append("找不到 src/zzz_od/application/zzz_application_launcher.py")
        inst = str(g.options.get("instance", "")).strip()
        if not inst or not all(x.strip().isdigit() for x in inst.split(",")):
            p.append("绝区零实例编号无效（例：1 或 1,2）")
        return p

    def warnings(self, g):
        w = []
        inst = str(g.options.get("instance", "1")).split(",")[0].strip() or "1"
        cfg_dir = os.path.join(g.tool_dir, "config", f"{int(inst):02d}" if inst.isdigit() else inst)
        for f in glob.glob(os.path.join(cfg_dir, "**", "charge_plan.yml"), recursive=True):
            try:
                with open(f, encoding="utf-8") as fh:
                    m = re.search(r"^restore_charge:\s*(.+?)\s*$", fh.read(), re.M)
            except OSError:
                continue
            if m and m.group(1) not in ("不使用", "'不使用'", '"不使用"'):
                w.append(f"一条龙体力计划的恢复电量设置为「{m.group(1)}」：电量不足时会消耗储蓄电量等补充资源"
                         f"（{os.path.relpath(f, g.tool_dir)}）")
        w.append("一条龙的体力刷本每次运行都会重跑，会把当前电量刷光")
        return w

    def markers(self, g):
        return [("指令[ %s ]", ["src/one_dragon/base/operation/operation.py"]),
                ("%s 执行成功", ["src/one_dragon/base/operation/operation.py"]),
                ('APP_NAME = "一条龙"', ["src/zzz_od/application/one_dragon_app/zzz_one_dragon_app_const.py"])]

    def running_instances(self, g):
        d = procs.norm(g.tool_dir)

        def pred(i):
            if i["name"].lower() in _LAUNCHER_NAMES:
                return True
            cmd = " ".join(i["cmdline"]).lower()
            if "zzz_od" in cmd or "one_dragon" in cmd:
                return True
            exe = procs.norm(i["exe"])
            return bool(exe) and exe.startswith(d + os.sep)
        return instances_text(procs.find_processes(pred))

    def launch_spec(self, g):
        self._ensure_rules(g)
        inst = str(g.options.get("instance", "1")).strip()
        env = procs.clean_env(drop_prefixes=("UV_",), extra={
            "PYTHONPATH": os.path.join(g.tool_dir, "src"),
            "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1",
        })
        return LaunchSpec(
            argv=[self.python(g), "-m", "zzz_od.application.zzz_application_launcher", "--instance", inst],
            cwd=g.tool_dir, env=env, console=True, capture=True,
        )

    def open_targets(self, g):
        return {"工具目录": g.tool_dir, "配置": os.path.join(g.tool_dir, "config"),
                "日志": os.path.join(g.tool_dir, ".log")}

    def option_labels(self):
        return {"instance": "一条龙账号实例（--instance）"}
