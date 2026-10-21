"""Maa_bbb (崩坏3) daily settings: config/configs/<item_id>.json picked by its "name" (MFW-ChainFlow 4.10).

Evidence: docs/research/settings/bh3.json (verdict none) and the files themselves.
- Each config file is UTF-8 without BOM, CRLF, 4-space indent, ensure_ascii=False, no trailing newline;
  JsonFile reproduces it byte for byte.  MFW.exe rewrites it on option changes, on connect and on exit
  -> read-only while MFW / MFWUpdater runs (adapter.running_instances).
- Format gates (else read-only): interface_version 2, MFW.exe major.minor 4.10 (the storage format belongs
  to MFW and changed before: "children" -> "branches"), no unknown top-level / task keys, all imports readable.
- tasks[] = PreTask, Controller, Resource (not shown), the t_* tasks in run order (enable = is_checked),
  Post-Action last (shown read-only: shutdown / run_program are blocked by the adapter).  Keyed by item_id.
- task_option.<option> = {"value": V, "branches"?: {case: {child: entry}}, "hidden"?: true}.  V is the case
  name for select / switch ("Yes"/"No", never a bool), a list for checkbox, a dict {input: typed value} for
  input (pipeline_type int -> JSON number).  Children of the cases not chosen carry hidden:true and are
  skipped at run time, so changing a parent flips the hidden flags one level down.
- A missing option applies no override at run time: its default is shown and it is written (as MFW's own
  default entry, branches included) only when edited.  Options / tasks outside the config's controller and
  resource whitelists are hidden, as MFW does.  Labels are literal Chinese from the task files.
"""
from __future__ import annotations

import glob
import os
import re

from .. import adapters
from ..adapters.base import read_json
from ..config import GameConfig
from .base import Field, JsonFile, Model, Section, SettingsError, SettingsProvider, get_path, set_path
from .maafw_pi import (SENSITIVE, Interface, Node, default_case, gui_version_ok, in_list, input_defs,
                       input_help, opt_type, risk_of, task_risk, verify_ok)

VERIFIED_MFW = ("4.10",)
BASE_IDS = ("PreTask", "Controller", "Resource")
KNOWN_TOP = {"name", "item_id", "tasks", "know_task", "global_options", "bundle", "interface_task_list_materialized"}
KNOWN_TASK = {"name", "item_id", "is_checked", "task_option", "task_source", "builtin_key"}
# Post-Action keys (MFW TaskFlowRunner._handle_post_action) with MFW's zh_CN labels
POST_LABELS = {"close_controller": "关闭控制器", "close_software": "退出软件", "run_other": "运行其他配置",
               "run_program": "运行其他程序", "shutdown": "关机", "always_run": "始终运行"}


def _lit(*names: str) -> str:
    return "|".join(re.escape(n) for n in names)


TASK_RISK = [
    (_lit("材料远征"), "消耗体力（正常的日常消耗）"),
    (_lit("舰团每日委托"), "会提交委托材料"),
    (_lit("后勤终端物品购买"), "花金币（萌新别选，还挺贵的）"),
    (_lit("每日40晶体核心"), "在金币商店买晶体核心，花金币"),
    (_lit("模拟作战室-减负"), "可能消耗体力（未核实）"),
    (_lit("每周虚境商店1星石的90张虚境挑战卷"), "每周花 1 星石"),
    (_lit("乐土每周商店购买"), "花乐土币"),
    (_lit("卖材料换星石"), "出售材料换星石（不可逆）"),
]
OPTION_RISK = [
    (_lit("材料远征体力不足时吃药吗", "派遣时体力不足吃药吗"), "选“是”会消耗体力药"),
    (_lit("委托材料不足时自动补货"), "选“是”会点“快捷补全道具”补齐缺的委托材料，要花资源（花哪种货币未核实）"),
    (_lit("后勤终端购买物品货币类型"), "花金币；选含“星石”的项会花星石"),
    (_lit("是否消耗时序通行证"), "选“是”会消耗时序通行证"),
    (_lit("接委托时体力不足处理"), "选“吃药”会消耗体力药"),
    (_lit("换星石-换取材料选择"), "决定出售哪种材料（不可逆）"),
    (_lit("聚能·空间透镜", "相转移镜面", "吼吼休假卷", "时序通行证"), "选“是”会花乐土币购买"),
    (_lit("你氪通行证了吗", "万象虚境-时序通行证不足处理"), ""),          # checked: spends nothing
]
RISK_WORDS = re.compile(r"吃药|体力药|星石|金币|补货|补全|购买|消耗|出售|卖|花费|兑换")


def _short(text: str, n: int = 240) -> str:
    text = text.replace("\n", " ")
    return text if len(text) <= n else text[:n] + "…"


def _convert(inp: dict, value):
    """An input value in the type MFW stores (pipeline_type); None when it fails verify / conversion."""
    ptype = str(inp.get("pipeline_type", "string"))
    try:
        if ptype == "int":
            v = int(value)
        elif ptype == "float":
            v = float(value)
        elif ptype == "bool":
            v = value if isinstance(value, bool) else str(value).strip().lower() in ("true", "1", "yes")
        else:
            v = str(value)
    except (TypeError, ValueError):
        return None
    return v if isinstance(v, bool) or verify_ok(inp, str(v)) else None


def _kind(inp: dict) -> str:
    return {"int": "int", "float": "float", "bool": "bool"}.get(str(inp.get("pipeline_type", "string")), "text")


def _input_default(inp: dict):
    v = _convert(inp, inp.get("default") or "")
    return v if v is not None else {"int": 0, "float": 0.0, "bool": False}.get(_kind(inp), "")


def _entry_path(node: Node) -> tuple:
    chain = [p for p, _d, _c in node.parents] + [node.name]
    path = ("task_option", chain[0])
    for (_p, _d, case), child in zip(node.parents, chain[1:]):
        path += ("branches", case, child)
    return path


def _shape_ok(task: dict, path: tuple) -> bool:
    """Every part of the path that exists is a dict (a non-dict would be overwritten by an edit)."""
    cur = task
    for part in path:
        if not isinstance(cur, dict):
            return False
        if part not in cur:
            return True
        cur = cur[part]
    return isinstance(cur, dict)


class MaaBbbSettings(SettingsProvider):
    key = "maabbb"
    verified_version = "v1.13.4"          # interface.json version (MFW-ChainFlow Assistant 4.10.2)

    @staticmethod
    def _files(g: GameConfig) -> dict[str, str]:
        """config name -> file."""
        out = {}
        for p in sorted(glob.glob(os.path.join(g.tool_dir, "config", "configs", "*.json"))):
            d = read_json(p)
            if isinstance(d, dict) and d.get("name") and d.get("item_id"):
                out.setdefault(str(d["name"]), p)
        return out

    def file_paths(self, g):
        return sorted(glob.glob(os.path.join(g.tool_dir, "config", "configs", "*.json")))

    def config_names(self, g):
        return list(self._files(g))

    def default_config(self, g):
        files = self._files(g)
        name = str(g.options.get("config", "")).strip()
        if name in files:
            return name
        cur = (read_json(os.path.join(g.tool_dir, "config", "multi_config.json")) or {}).get("curr_config_id")
        by_id = {os.path.splitext(os.path.basename(p))[0]: n for n, p in files.items()}
        return by_id.get(cur) or (next(iter(files)) if files else "")

    def busy(self, g):
        return adapters.get(g.adapter).running_instances(g)

    def load(self, g, config_name):
        path = self._files(g).get(config_name)
        if not path:
            raise SettingsError(f"Maa_bbb 里没有配置「{config_name}」")
        f = JsonFile(path)
        data = f.data if isinstance(f.data, dict) else {}
        tasks = data.get("tasks")
        if not isinstance(tasks, list):
            raise SettingsError(f"{os.path.basename(path)} 里没有任务列表")
        iface = Interface(g.tool_dir)
        model = Model(files=[f], sections=[], order=tasks)

        lock = []
        if iface.interface_version != 2:
            lock.append(f"interface_version 是 {iface.interface_version}（总控只认 2）")
        lock += iface.problems
        gui, ok = gui_version_ok(os.path.join(g.tool_dir, "MFW.exe"), VERIFIED_MFW)
        if gui is None:
            model.drift.append("读不到 MFW.exe 的版本号，存储格式按 MFW 4.10 处理")
        elif not ok:
            lock.append(f"MFW 已是 {gui}，总控按 4.10 的存储格式核对过")
        extra = sorted(set(data) - KNOWN_TOP)
        extra_task = sorted({k for t in tasks if isinstance(t, dict) for k in t} - KNOWN_TASK)
        if extra or extra_task:
            lock.append(f"配置里出现了总控不认识的字段 {'、'.join(extra + extra_task)}")
        if lock:
            f.exact, f.reason = False, "；".join(([f.reason] if f.reason else []) + lock)
            model.drift += lock

        base = {t.get("item_id"): t for t in tasks if isinstance(t, dict)}
        ctrl = str(get_path(base.get("Controller") or {}, "task_option.controller_type") or "")
        res = str(get_path(base.get("Resource") or {}, "task_option.resource") or "")
        for i, task in enumerate(tasks):
            if not isinstance(task, dict) or task.get("item_id") in BASE_IDS:
                continue
            if task.get("item_id") == "Post-Action":
                model.sections.append(self._post_action(task))
            else:
                model.sections.append(self._section(iface, model, task, i, ctrl, res))

        present = {t.get("name") for t in tasks if isinstance(t, dict)}
        usable = [t for t in iface.tasks
                  if in_list(ctrl, t.get("controller")) and in_list(res, t.get("resource"))]
        missing = [t for t in usable if t.get("name") not in present]
        if missing:
            model.notes.append("配置里没有添加的任务（要用请在 MFW 里添加）："
                               + "、".join(iface.task_label(t) for t in missing))
        if not f.exact:
            model.notes.append(f"只能查看：{f.reason}。请在 MFW 里修改。")
        model.notes.append("标着“配置里还没有”的选项 MFW 运行时不会套用，显示的是默认值，改动后才写入；"
                           "改动在下次启动 MFW 时生效。")
        return model

    @staticmethod
    def _post_action(task: dict) -> Section:
        opt = task.get("task_option")
        po = opt.get("post_action", opt) if isinstance(opt, dict) else {}
        on = [POST_LABELS[k] for k, v in (po.items() if isinstance(po, dict) else []) if k in POST_LABELS and v]
        return Section("Post-Action", "完成后运行", task, [], subtitle="只读",
                       note="完成后动作只能在 MFW 里改（关机、运行其他程序会被总控预检拦下）。"
                            f"当前：{'、'.join(on) if on else '无动作'}。")

    def _section(self, iface: Interface, model: Model, task: dict, index: int, ctrl: str, res: str) -> Section:
        name = str(task.get("name", ""))
        sid = str(task.get("item_id") or f"task{index}")
        tdef = iface.task_by_name.get(name)
        state = "已启用" if task.get("is_checked") else "未启用"
        if "builtin_key" in task or "task_source" in task:
            return Section(sid, name, task, [], movable=True, subtitle="MFW 内置任务",
                           note=f"MFW 内置任务（启动程序、等待、通知等）只能在 MFW 里设置和开关。当前：{state}。")
        if tdef is None:
            model.drift.append(f"{name}：interface 里已没有这个任务（MFW 运行时会报找不到）")
            return Section(sid, name, task, [], movable=True, subtitle="已失效",
                           note=f"interface 里已没有这个任务（只读）。当前：{state}。")
        title = iface.task_label(tdef)
        notes = []
        risk = task_risk(iface, name, tdef, TASK_RISK, RISK_WORDS)
        if risk:
            notes.append(f"⚠ 这个任务{risk}。")
        desc = iface.text(tdef.get("description"))
        if desc:
            notes.append(_short(desc))
        if not (in_list(ctrl, tdef.get("controller")) and in_list(res, tdef.get("resource"))):
            notes.append(f"当前控制器 / 资源（{ctrl} / {res}）不支持这个任务，MFW 不会运行它。")
        to = task.get("task_option")
        broken = to is not None and not isinstance(to, dict)
        if broken:
            model.drift.append(f"{title}：task_option 不是对象，选项只读")
        to = to if isinstance(to, dict) else {}
        if get_path(to, ("_speedrun_config", "enabled")) is True:
            notes.append("⚠ 这个任务在 MFW 里开了“条件执行”，条件不满足时会被跳过。")

        def visible(o: dict) -> bool:
            return in_list(ctrl, o.get("controller")) and in_list(res, o.get("resource"))

        fields, writers, hidden = [], {}, 0
        for node in iface.walk(tdef.get("option"), visible):
            if SENSITIVE.search(node.name):
                hidden += 1
                continue
            for fd, write in self._fields(iface, model, title, task, node):
                fd.readonly = fd.readonly or broken
                fields.append(fd)
                writers[fd.key] = write
        if hidden:
            notes.append(f"有 {hidden} 项含个人信息的选项不在这里显示，请在 MFW 里设置。")
        unknown = self._unknown(iface, to, tdef.get("option") or [])
        if unknown:
            model.drift.append(f"{title}：配置里有 interface 已不认识的选项 {'、'.join(unknown)}（总控不动它）")
        if name != title:
            notes.append(f"任务名：{name}")

        def on_set(obj, fd, value, writers=writers):
            if fd.key in writers:
                writers[fd.key](obj, value)
            else:
                set_path(obj, fd.key, value)

        return Section(sid, title, task, fields, enable_key="is_checked", movable=True,
                       subtitle="、".join(iface.group_label(x) for x in tdef.get("group") or []),
                       note="\n".join(notes), on_set=on_set)

    def _unknown(self, iface: Interface, stored: dict, names: list, prefix: str = "") -> list[str]:
        """Stored option keys (any depth) that the task's definitions no longer have."""
        out = []
        for key, entry in stored.items():
            if str(key).startswith("_"):
                continue
            if key not in names or key not in iface.options:
                out.append(prefix + str(key))
                continue
            branches = entry.get("branches") if isinstance(entry, dict) else None
            if not isinstance(branches, dict):
                continue
            cases = {c.get("name"): c for c in iface.options[key].get("cases") or [] if isinstance(c, dict)}
            for case, kids in branches.items():
                if case not in cases:
                    out.append(f"{prefix}{key}/{case}")
                elif isinstance(kids, dict):
                    out += self._unknown(iface, kids, cases[case].get("option") or [], f"{prefix}{key}/{case}/")
        return out

    def _default_entry(self, iface: Interface, opt: dict, hidden: bool = False, depth: int = 0) -> dict:
        """The entry MFW generates for an option (TaskService.gen_single_task_default_option)."""
        typ = opt_type(opt)
        if typ == "input":
            value = {i["name"]: _input_default(i) for i in input_defs(opt)}
        else:
            value = default_case(opt)
        entry = {"value": value}
        if typ in ("select", "switch") and depth < 8:
            branches = {}
            for case in opt.get("cases") or []:
                kids = [k for k in (case.get("option") or []) if k in iface.options] if isinstance(case, dict) else []
                if kids:
                    branches[case.get("name")] = {k: self._default_entry(iface, iface.options[k],
                                                                         case.get("name") != value, depth + 1)
                                                  for k in kids}
            if branches:
                entry["branches"] = branches
        if hidden:
            entry["hidden"] = True
        return entry

    def _ensure(self, iface: Interface, task: dict, node: Node) -> dict | None:
        """The entry of node inside task_option, created (with MFW's defaults) along the path if missing."""
        to = task.get("task_option")
        if not isinstance(to, dict):
            return None
        chain = [(p, d) for p, d, _c in node.parents] + [(node.name, node.opt)]
        top, top_def = chain[0]
        if top not in to:
            items = list(to.items())       # MFW keeps internal "_" keys (_speedrun_config) last
            to.clear()
            for k, v in items:
                if str(k).startswith("_") and top not in to:
                    to[top] = self._default_entry(iface, top_def)
                to[k] = v
            to.setdefault(top, self._default_entry(iface, top_def))
        cur = to[top]
        for (_p, _d, case), (child, child_def) in zip(node.parents, chain[1:]):
            if not isinstance(cur, dict):
                return None
            branches = cur.setdefault("branches", {})
            if not isinstance(branches, dict):
                return None
            kids = branches.setdefault(case, {})
            if not isinstance(kids, dict):
                return None
            if child not in kids:
                kids[child] = self._default_entry(iface, child_def, cur.get("value") != case)
            cur = kids[child]
        return cur if isinstance(cur, dict) else None

    def _writer(self, iface: Interface, node: Node, inp: dict | None = None):
        def write(task: dict, value) -> None:
            typ = node.type
            if typ == "input":
                conv = _convert(inp, value) if inp else None
                if conv is None:
                    return
                entry = self._ensure(iface, task, node)
                if entry is None:
                    return
                if not isinstance(entry.get("value"), dict):
                    entry["value"] = {}
                entry["value"][inp["name"]] = conv
                return
            if typ == "checkbox":
                names = [c.get("name") for c in node.opt.get("cases") or [] if isinstance(c, dict)]
                value = [c for c in names if c in value] + [c for c in value if c not in names]
                lo, hi = node.opt.get("min_count"), node.opt.get("max_count")
                if (isinstance(lo, int) and len(value) < lo) or (isinstance(hi, int) and len(value) > hi):
                    return
            entry = self._ensure(iface, task, node)
            if entry is None:
                return
            entry["value"] = value
            branches = entry.get("branches")
            if typ in ("select", "switch") and isinstance(branches, dict):
                for case, kids in branches.items():          # only the chosen case's children run
                    for kid in (kids.values() if isinstance(kids, dict) else []):
                        if isinstance(kid, dict) and case == value:
                            kid.pop("hidden", None)
                        elif isinstance(kid, dict):
                            kid["hidden"] = True
        return write

    def _fields(self, iface: Interface, model: Model, title: str, task: dict, node: Node) -> list:
        opt, typ = node.opt, node.type
        path = _entry_path(node)
        label = iface.label_path(node)
        if node.parents:                     # the same child under several cases of one parent: name the case
            pname, pdef, case = node.parents[-1]
            same = [c for c in pdef.get("cases") or [] if isinstance(c, dict) and node.name in (c.get("option") or [])]
            if len(same) > 1:
                label += f"（{iface.case_label(pdef, case)}）"
        entry = get_path(task, path)
        ok = _shape_ok(task, path)
        if not ok:
            model.drift.append(f"{title}：「{label}」保存的格式不对，暂时只读")
        notes = [iface.text(opt.get("description")), iface.condition(node)]
        if entry is None and ok:
            notes.append("配置里还没有这一项，MFW 运行时不会套用；在这里改动后才写入")
        help_ = "\n".join(x for x in notes if x)
        risk = risk_of(iface, node, OPTION_RISK, RISK_WORDS)
        cases = [(c.get("name"), iface.case_label(opt, c)) for c in opt.get("cases") or [] if isinstance(c, dict)]
        common = dict(help=help_, risk=risk, readonly=not ok)
        out = []
        if typ in ("select", "switch"):
            cur = get_path(task, path + ("value",))
            if isinstance(cur, str) and cur not in [c for c, _ in cases]:
                model.drift.append(f"{title}：「{label}」保存的选项 {cur} 在 interface 里已不存在")
            out.append((Field(path + ("value",), label, "choice", cases, default=default_case(opt), **common),
                        self._writer(iface, node)))
        elif typ == "checkbox":
            lo, hi = opt.get("min_count"), opt.get("max_count")
            if isinstance(lo, int) or isinstance(hi, int):
                common["help"] = "\n".join(x for x in (help_, f"需要选 {lo if lo is not None else 0}"
                                                       f"–{hi if hi is not None else len(cases)} 项，数量不对时不会写入") if x)
            out.append((Field(path + ("value",), label, "multi", cases, default=default_case(opt), **common),
                        self._writer(iface, node)))
        elif typ == "input":
            vals = get_path(task, path + ("value",))
            if vals is not None and not isinstance(vals, dict):
                common["readonly"] = True
                model.drift.append(f"{title}：「{label}」保存的值不是输入项格式，暂时只读")
            inputs = input_defs(opt)
            for inp in inputs:
                if inp.get("password") or SENSITIVE.search(inp["name"]):
                    continue
                ilabel = iface.text(inp.get("label"))
                flabel = label if len(inputs) == 1 and (not ilabel or ilabel == label) else f"{label} · {ilabel or inp['name']}"
                kind = _kind(inp)
                default = _input_default(inp)
                fd = Field(path + ("value", inp["name"]), flabel, kind, default=default,
                           min=min(0, default) if kind in ("int", "float") else 0,
                           max=2_147_483_647 if kind == "int" else 999_999,
                           **{**common, "help": "\n".join(x for x in (common["help"], input_help(iface, inp)) if x)})
                out.append((fd, self._writer(iface, node, inp)))
        else:
            model.drift.append(f"{title}：「{label}」是总控不支持编辑的选项类型 {typ}，请在 MFW 里设置")
        return out
