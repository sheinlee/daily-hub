"""What every tool adapter provides. Capabilities differ per tool and are not faked."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

from .. import procs
from ..config import LAUNCH_BY_HUB, LAUNCH_BY_TOOL, GameConfig
from ..logtail import FileTail, PipeReader

# Line kinds an adapter can report.
DONE = "done"      # the tool says the whole daily run finished
FAIL = "fail"      # the run as a whole failed / aborted (hub ends the item shortly after)
ABORT = "abort"    # the tool is about to do the wrong thing: stop it right now
WARN = "warn"      # a sub-task failed or something needs a look, run continues
KEY = "key"        # milestone worth highlighting


@dataclass
class LaunchSpec:
    argv: list[str]
    cwd: str
    env: dict[str, str]
    console: bool          # console program: hide its window
    capture: bool          # read stdout/stderr through a pipe


@dataclass
class Rule:
    kind: str
    pattern: re.Pattern

    @classmethod
    def of(cls, kind: str, regex: str) -> "Rule":
        return cls(kind, re.compile(regex))


class Session:
    """Collects this run's output lines (pipe or log file) and classifies them."""

    def __init__(self, adapter: "Adapter", g: GameConfig, popen, since: float):
        self.adapter = adapter
        self.g = g
        self.popen = popen
        self.since = since
        self.state: dict = {}   # per-run scratch space for adapters
        self.pipe = PipeReader(popen.stdout) if popen.stdout is not None else None
        self.tail: FileTail | None = adapter.log_tail(g, since)

    def raw_lines(self, final: bool = False) -> list[str]:
        out: list[str] = []
        if self.pipe:
            out += self.pipe.drain() if final else self.pipe.poll()
        if self.tail:
            out += self.tail.poll()
        return out

    def poll(self, final: bool = False) -> list[tuple[str, str | None]]:
        res = []
        for line in self.adapter.transform(self.raw_lines(final), self):
            if not line.strip():
                continue
            kind = self.adapter.classify(line)
            if kind is None and not self.adapter.show(line):
                continue
            res.append((line, kind))
        return res


class Adapter:
    key = ""
    tool_name = ""
    exits_when_done = True      # does the tool process exit on its own after finishing?
    game_gone_grace = 60        # seconds without any game process before the item is given up
    # Game-launch modes that make sense for this tool; the first one is the fallback.
    launch_modes = (LAUNCH_BY_HUB, LAUNCH_BY_TOOL)
    rules: list[Rule] = []
    hide: list[re.Pattern] = []
    # Tool version the run rules (command line, log markers, dangerous settings) were checked against.
    verified_version = ""

    def game_mode(self, g: GameConfig) -> str:
        return g.launch_mode if g.launch_mode in self.launch_modes else self.launch_modes[0]

    def hub_launches(self, g: GameConfig) -> bool:
        return self.game_mode(g) != LAUNCH_BY_TOOL

    def launcher_probe(self, g: GameConfig):
        """Evidence source for clicking the official launcher's start button (None = cannot verify).

        Only adapters that can prove the button state list LAUNCH_VIA_LAUNCHER in launch_modes.
        """
        return None

    def version(self, g: GameConfig) -> str | None:
        """Installed tool version (None = cannot tell)."""
        return None

    def version_note(self, g: GameConfig) -> str | None:
        """Precheck line when the installed version differs from the one the hub was checked against."""
        v = self.version(g)
        if not v or not self.verified_version:
            return None
        if str(v).strip().lstrip("vV") == str(self.verified_version).strip().lstrip("vV"):
            return None
        return (f"{self.tool_name} 已是 {v}，总控的运行规则按 {self.verified_version} 核对过；"
                "如果这款运行异常（不开跑、判定不对），请把日志交给 Claude 更新适配")

    # ---- checks ----
    def problems(self, g: GameConfig) -> list[str]:
        """Blocking problems (missing files, bad options, dangerous settings). Empty = ok to start."""
        p = []
        if not os.path.isdir(g.tool_dir):
            from ..config import tool_dir_hint
            hint = tool_dir_hint(g.key)
            p.append(f"还没设置工具目录：请在“总控设置”里填写{'，' + hint if hint else ''}" if not g.tool_dir.strip()
                     else f"工具目录不存在：{g.tool_dir}{'；' + hint if hint else ''}")
        return p

    def warnings(self, g: GameConfig) -> list[str]:
        """Non-blocking things the user should know before a real run (resource use etc.)."""
        return []

    def markers(self, g: GameConfig) -> list[tuple[str, list[str]]]:
        """Texts the run rules depend on and the tool files that carry them: [(text, [pattern, ...])].

        Patterns are relative to tool_dir ("pyz:<exe>" = inside a PyInstaller exe).  markers.missing()
        checks them in precheck and when the tool's version changed (updates.py)."""
        return []

    def marker_problems(self, g: GameConfig) -> list[str]:
        from .. import markers
        lost = markers.missing(g.tool_dir, self.markers(g))
        return [f"在 {self.tool_name} 里找不到总控用来判断完成/失败的文字「{t}」（可能是工具更新改了日志），"
                "自动运行时可能判断不出结果" for t in lost]

    def running_instances(self, g: GameConfig) -> list[str]:
        """Already-running copies of the tool (user-opened or leftover). Must block the run."""
        return []

    # ---- run ----
    def launch_spec(self, g: GameConfig) -> LaunchSpec:
        raise NotImplementedError

    def log_tail(self, g: GameConfig, since: float) -> FileTail | None:
        return None

    def transform(self, lines: list[str], session: Session) -> list[str]:
        return lines

    def classify(self, line: str) -> str | None:
        for r in self.rules:
            if r.pattern.search(line):
                return r.kind
        return None

    def show(self, line: str) -> bool:
        return not any(h.search(line) for h in self.hide)

    # ---- UI helpers ----
    def open_targets(self, g: GameConfig) -> dict[str, str]:
        return {"工具目录": g.tool_dir}

    def option_choices(self, g: GameConfig) -> dict[str, list[str]]:
        return {}

    def option_labels(self) -> dict[str, str]:
        return {}

    def extra_cleanup_names(self) -> list[str]:
        """Helper processes the tool spawns that must not outlive it (looked up under tool_dir)."""
        return []


def instances_text(infos: list[dict]) -> list[str]:
    return [f"已在运行：{procs.describe(infos)}"] if infos else []


def json_value(path: str, key: str) -> str | None:
    """A top-level string value of a JSON file (e.g. interface.json "version"), None if absent."""
    d = read_json(path)
    v = d.get(key) if isinstance(d, dict) else None
    return str(v) if v not in (None, "") else None


def read_json(path: str):
    """Read-only JSON load; None when missing or unreadable."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None
