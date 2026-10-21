"""Shared reading of MaaFramework ProjectInterface v2 projects (MaaEnd / MXU, Maa_bbb / MFW-ChainFlow).

Evidence: docs/research/settings/endfield.json and bh3.json (schema_source), checked against the files.
- interface.json (interface_version 2) plus every file in its "import" list; task[] lists are concatenated,
  option{} dicts merged (a later file wins), group[] appended.  Some task files are JSONC (// comments,
  trailing commas), so they are read with a string-aware stripper.  A file that fails to load is reported
  in Interface.problems (MXU turns it into {} and then deletes its tasks from instances).
- Option types: select (also when "type" is missing), switch (cases Yes/No), checkbox (default_case is a
  list), input (inputs[] with default string, pipeline_type, verify regex), hotkey (global keymaps only).
  A select/switch case may list nested options that apply only while that case is chosen.
- Labels: a "$key" string is looked up in the zh_cn locale named by interface.languages (MaaEnd); Maa_bbb
  has no languages map and writes literal Chinese.  Labels and descriptions carry markdown images, bold,
  links, HTML tags, [color:x] tags and entities: cleaned to plain text here.
- controller[] / resource[] on a task or option whitelist where it applies (case-insensitive in MFW).
This module never writes anything; the providers decide how values are stored.
"""
from __future__ import annotations

import html
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .base import SettingsError

SWITCH_YES = ("Yes", "yes", "Y", "y")
TEXT_RISK = "可能消耗资源（按文字推断，未核实）"
# option / input names that hold personal data (phone digits, mail, player names): never shown
SENSITIVE = re.compile(r"account|phone|mail|password|passwd|token|secret|uid|remark|digits|cdk", re.I)

_STR_OR_COMMENT = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/', re.S)
_STR_OR_TRAILING_COMMA = re.compile(r'"(?:\\.|[^"\\])*"|,(?=\s*[}\]])', re.S)


def load_jsonc(path: str):
    """JSON with // and /* */ comments and trailing commas (as jsonc-parser / MFW accept)."""
    text = Path(path).read_bytes().decode("utf-8-sig")
    try:
        return json.loads(text)
    except ValueError:
        pass
    text = _STR_OR_COMMENT.sub(lambda m: m.group(0) if m.group(0)[0] == '"' else "", text)
    text = _STR_OR_TRAILING_COMMA.sub(lambda m: m.group(0) if m.group(0)[0] == '"' else "", text)
    return json.loads(text)


def clean(text) -> str:
    """Tool label / description -> plain text (markdown, HTML and colour tags removed)."""
    s = str(text or "")
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", s)                  # ![](resource/image/...) icons
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)              # [text](url)
    s = re.sub(r"\[/?color(?::[^\]]*)?\]", "", s, flags=re.I)  # [color:tomato]..[/color]
    s = re.sub(r"<br\s*/?>|</p>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s.replace("**", ""))
    s = re.sub(r"[ \t]+\n", "\n", s.replace("\r\n", "\n"))
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def opt_type(opt: dict) -> str:
    return str(opt.get("type") or "select")


def in_list(value: str, allowed, ignore_case: bool = True) -> bool:
    """controller[] / resource[] whitelist test (missing or empty list = everywhere)."""
    if not allowed:
        return True
    if ignore_case:
        return str(value).lower() in {str(a).lower() for a in allowed}
    return value in allowed


@dataclass
class Node:
    """One option at one place in a task's option tree."""
    name: str
    opt: dict
    parents: tuple = ()          # ((parent option name, parent definition, case name), ...) from the top

    @property
    def type(self) -> str:
        return opt_type(self.opt)


class Interface:
    """The merged PI v2 definitions of one project (read-only)."""

    def __init__(self, tool_dir: str):
        self.dir = tool_dir
        path = os.path.join(tool_dir, "interface.json")
        try:
            root = load_jsonc(path)
        except (OSError, ValueError) as e:
            raise SettingsError(f"读不了 {path}：{e}") from e
        if not isinstance(root, dict):
            raise SettingsError(f"{path} 不是对象")
        self.root = root
        self.version = root.get("version")
        self.interface_version = root.get("interface_version")
        self.problems: list[str] = []
        self.tasks: list[dict] = [t for t in root.get("task") or [] if isinstance(t, dict)]
        self.options: dict[str, dict] = dict(root.get("option") or {})
        self.groups: dict[str, dict] = {}
        self.pretasks: list[dict] = []
        self._add_groups(root.get("group"))
        self._add_pretasks(root.get("pretask"))
        for rel in root.get("import") or []:
            try:
                d = load_jsonc(os.path.join(tool_dir, rel))
            except (OSError, ValueError) as e:
                self.problems.append(f"定义文件 {rel} 读取失败（{type(e).__name__}）")
                continue
            if not isinstance(d, dict):
                self.problems.append(f"定义文件 {rel} 不是对象")
                continue
            self.tasks += [t for t in d.get("task") or [] if isinstance(t, dict)]
            self.options.update({k: v for k, v in (d.get("option") or {}).items() if isinstance(v, dict)})
            self._add_groups(d.get("group"))
            self._add_pretasks(d.get("pretask"))
        self.task_by_name: dict[str, dict] = {}
        for t in self.tasks:
            self.task_by_name.setdefault(str(t.get("name")), t)
        self.locale: dict = {}
        langs = root.get("languages") or {}
        rel = next((v for k, v in langs.items() if re.fullmatch(r"zh[-_]?(cn|hans)", str(k), re.I)), None)
        if rel:
            try:
                loc = load_jsonc(os.path.join(tool_dir, rel))
                self.locale = loc if isinstance(loc, dict) else {}
            except (OSError, ValueError) as e:
                self.problems.append(f"中文文本 {rel} 读取失败（{type(e).__name__}），只能显示选项名")

    def _add_groups(self, groups) -> None:
        for g in groups or []:
            if isinstance(g, dict) and g.get("name") and g["name"] not in self.groups:
                self.groups[g["name"]] = g

    def _add_pretasks(self, pre) -> None:
        self.pretasks += [p for p in (pre if isinstance(pre, list) else [pre]) if isinstance(p, dict)]

    # ---- text ----
    def text(self, value) -> str:
        """A label / description: "$key" through the zh locale (missing key -> ""), cleaned."""
        if not isinstance(value, str) or not value:
            return ""
        if value.startswith("$"):
            value = self.locale.get(value[1:])
            if not isinstance(value, str):
                return ""
        return clean(value)

    def task_label(self, tdef: dict) -> str:
        return self.text(tdef.get("label")) or str(tdef.get("name", ""))

    def option_label(self, name: str, opt: dict | None = None) -> str:
        opt = self.options.get(name) if opt is None else opt
        return (self.text(opt.get("label")) if opt else "") or name

    def case_label(self, opt: dict, case: dict | str) -> str:
        case = next((c for c in opt.get("cases") or [] if c.get("name") == case), {"name": case}) \
            if isinstance(case, str) else case
        name = str(case.get("name", ""))
        if opt_type(opt) == "switch" and not case.get("label"):
            return "是" if name in SWITCH_YES else "否" if name.lower() in ("no", "n") else name
        return self.text(case.get("label")) or name

    def group_label(self, name: str) -> str:
        g = self.groups.get(name) or {}
        return self.text(g.get("label")) or name

    # ---- option tree ----
    def walk(self, names, visible=lambda opt: True, parents: tuple = (), path: frozenset = frozenset()):
        """Options of a task depth-first: each option, then the nested options of its select/switch cases."""
        for name in names or []:
            opt = self.options.get(name)
            if not isinstance(opt, dict) or name in path or not visible(opt):
                continue
            yield Node(name, opt, parents)
            if opt_type(opt) not in ("select", "switch"):
                continue
            for case in opt.get("cases") or []:
                if isinstance(case, dict) and case.get("option"):
                    yield from self.walk(case["option"], visible, parents + ((name, opt, case.get("name")),),
                                         path | {name})

    def all_names(self, names) -> set[str]:
        """Every option name reachable from a task (all cases, no filters)."""
        return {n.name for n in self.walk(names)}

    def condition(self, node: Node) -> str:
        """Plain-language "only when parent = case" for a nested option ("" at top level)."""
        parts = [f"「{self.option_label(p, pd)}」选「{self.case_label(pd, c)}」" for p, pd, c in node.parents]
        return f"仅当{'且'.join(parts)}时生效" if parts else ""

    def label_path(self, node: Node) -> str:
        return " › ".join([self.option_label(p, pd) for p, pd, _c in node.parents] + [self.option_label(node.name, node.opt)])

    def words(self, node_or_task: dict | Node) -> str:
        """All visible text of an option (label, description, case labels) or a task, for risk inference."""
        if isinstance(node_or_task, Node):
            o = node_or_task.opt
            out = [node_or_task.name, self.text(o.get("label")), self.text(o.get("description"))]
            out += [self.case_label(o, c) for c in o.get("cases") or [] if isinstance(c, dict)]
            out += [self.text(i.get("label")) for i in o.get("inputs") or [] if isinstance(i, dict)]
            return " ".join(out)
        return " ".join([str(node_or_task.get("name", "")), self.text(node_or_task.get("label")),
                         self.text(node_or_task.get("description"))])


def default_case(opt: dict):
    """Definition default: case name (select / switch), list (checkbox)."""
    cases = [c.get("name") for c in opt.get("cases") or [] if isinstance(c, dict)]
    d = opt.get("default_case")
    if opt_type(opt) == "checkbox":
        return list(d) if isinstance(d, list) else []
    if isinstance(d, str) and d in cases:
        return d
    return cases[0] if cases else (d or "")


def input_defs(opt: dict) -> list[dict]:
    return [i for i in opt.get("inputs") or [] if isinstance(i, dict) and i.get("name")]


def verify_ok(inp: dict, text: str) -> bool:
    """An input value against its verify regex (JS RegExp.test = search semantics) and pipeline_type."""
    pattern = inp.get("verify")
    if pattern:
        try:
            if not re.search(pattern, text, re.ASCII):
                return False
        except re.error:
            pass
    ptype = str(inp.get("pipeline_type", "string"))
    if ptype == "int":
        return bool(re.fullmatch(r"-?\d+", text.strip()))
    if ptype == "float":
        return bool(re.fullmatch(r"-?\d+(\.\d+)?", text.strip()))
    return True


def input_help(iface: Interface, inp: dict) -> str:
    parts = []
    if inp.get("description"):
        parts.append(iface.text(inp["description"]))
    if inp.get("verify") or str(inp.get("pipeline_type", "string")) in ("int", "float"):
        msg = iface.text(inp.get("pattern_msg"))
        parts.append("格式不对时不会写入" + (f"（{msg}）" if msg else ""))
    return "\n".join(parts)


def risk_of(iface: Interface, node: Node, overlay: list, pattern: re.Pattern) -> str:
    """Hand-kept risk text for an option name (regex overlay), else a guess from the tool's own words."""
    for rx, text in overlay:
        if re.fullmatch(rx, node.name):
            return text
    return TEXT_RISK if pattern.search(iface.words(node)) else ""


def task_risk(iface: Interface, name: str, tdef: dict | None, overlay: list, pattern: re.Pattern) -> str:
    for rx, text in overlay:
        if re.fullmatch(rx, name):
            return text
    return TEXT_RISK if tdef and pattern.search(iface.words(tdef)) else ""


def gui_version_ok(exe: str, verified: tuple) -> tuple[str | None, bool]:
    """(FileVersion of the GUI exe, its major.minor is one the storage format was checked against)."""
    try:
        from ..winapi import file_version
        v = file_version(exe)
    except (OSError, AttributeError, ImportError):
        v = None
    m = re.search(r"(\d+)\.(\d+)", v or "")
    return v, bool(m and f"{m.group(1)}.{m.group(2)}" in verified)
