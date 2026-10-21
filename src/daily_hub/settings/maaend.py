"""MaaEnd (终末地) daily settings: config/mxu-MaaEnd.json → instances[name=<hub instance>].tasks[].

Evidence: docs/research/settings/endfield.json (verdict none) and the files themselves.
- The file is written by MaaEnd.exe (MXU 2.7.1) as JSON.stringify(cfg, null, 2): UTF-8, LF, no trailing
  newline; JsonFile reproduces it byte for byte.  MXU rewrites it while it runs and on exit -> read-only
  while MaaEnd / its agents run (adapter.running_instances).
- Format gates (else read-only): file "version" "1.0", interface_version 2, MXU major.minor 2.7 / 2.8
  (2.8.0 ships with v2.33.0; its default / sanitise code is identical), every import file readable.
- Schema-driven: tasks and options come from interface.json + imports at every open (maafw_pi), labels
  from locales/interface/zh_cn.json.  One section per instance task (key: task id; taskName may repeat),
  run order = list order, switch = "enabled".
- optionValues is one flat map per task over the whole option tree (nested options included):
  select {"type","caseName"}, switch {"type","value"}, checkbox {"type","caseNames"},
  input {"type","values":{name: string}} (int inputs are strings too).  Missing entries mean the definition
  default, so they are shown as defaults and only written (as the complete typed object) when edited;
  an entry without the right "type" would be reset by MXU.  Each option is shown once, where it first
  appears; options of other controllers are hidden (MXU ignores them).
- Read-only here: the tasks the adapter blocks (CloseGamePC, CloseGame, __MXU_KILLPROC__, __MXU_POWER__,
  __MXU_LAUNCH__), AccountSwitch (personal data), MXU built-in tasks and pretasks (enable / order only),
  tasks MaaEnd no longer defines.  Personal inputs (phone digits, mail, friends' names) are not shown.
"""
from __future__ import annotations

import os
import re

from .. import adapters
from ..adapters.base import read_json
from ..adapters.maaend import _DANGEROUS_TASKS
from ..config import GameConfig
from .base import Field, JsonFile, Model, Section, SettingsError, SettingsProvider, get_path, set_path
from .maafw_pi import (SENSITIVE, SWITCH_YES, Interface, Node, default_case, gui_version_ok, in_list,
                       input_defs, input_help, risk_of, task_risk, verify_ok)

CONFIG_FORMAT = "1.0"
VERIFIED_MXU = ("2.7", "2.8")
READONLY_TASKS = set(_DANGEROUS_TASKS) | {"AccountSwitch"}

# MXU built-in tasks (labels from MXU's own i18n)
MXU_SPECIAL = {
    "__MXU_SLEEP__": "⏳ 倒计时", "__MXU_WAITUNTIL__": "⏰ 定时等待", "__MXU_NOTIFY__": "💬 系统通知",
    "__MXU_LAUNCH__": "▶️ 自定义程序", "__MXU_KILLPROC__": "⛔ 结束进程", "__MXU_POWER__": "⚡ 电脑操作",
    "__MXU_WEBHOOK__": "🔔 Webhook",
}

_MED = "选“使用限时药剂恢复”会消耗应急理智加强剂；没保存过时默认就是这一项"
# (option name regex, risk) — from the research risk_rules; anything else falls back to the tool's own words
OPTION_RISK = [
    (r"AutoUseSpMedication|ProtocolSpaceUseSpMedication", _MED),
    (r"(AutoEssence|ProtocolSpace)SpMedicationExpireWithinDays", "范围越大，用掉的限时理智药越多"),
    (r"ProtocolSpaceObtainMode(Claim)?", "领取会花理智；优先双倍领取会用理智消耗许可，花双倍理智"),
    (r"AutoEssenceObtainMode(ClaimOnly|ClaimOnlyForcedFilter)?", "领取会花理智；优先双倍领取花双倍（推断）"),
    (r"AutoEssenceRepeatCount", "每轮消耗理智"),
    (r"ProtocolSpaceSuccessCount", "选“无限制”会一直刷到理智（和允许使用的药）用完"),
    (r"AutoEssenceDoOverride", "消耗刻写券"),
    (r"EssenceFilterAfterBattleDiscardUnmatched", "把未匹配的基质标记为废弃（不可逆）"),
    (r"CreditShoppingForce", "执行刷新、购买所有物品都会花信用点"),
    (r"CreditShopping(Priority\d)?UnconditionalPurchase", "跳过保留信用点阈值，花信用点"),
    (r"CreditShoppingReserve", "填 0 表示不设下限，信用点可能全部花掉"),
    (r"CreditShoppingPriority\dAutoGetCredits|CreditShoppingClueSend", "会送出线索"),
    (r"AutoStockpileMinBuy|AutoStockBuy\w*", "花调度券"),
    (r"AutoStockReserve\w*", "保留值越低，花掉的调度券越多"),
    (r"DailyTaskUpgradeWeapon|DailyTaskLevelUpOperator", "消耗经验材料 / 折金票"),
    (r"DailyTaskCreation|DailyTaskAssemble", "消耗制作材料"),
    (r"AutoExtractSeed", "消耗材料本体提取基核"),
    (r"SeizeDeliveryJobsPostProcessing", "自动送货可能损坏货物或反复循环（见说明）"),
    (r"AutoStockpileAllowDataUpload", "会上传数据（读取 UID），不是资源消耗"),
]
TASK_RISK = [
    (r"AutoEssence|ProtocolSpace", "消耗理智"),
    (r"AutoStockpile|AutoStockStaple", "花调度券"),
    (r"SellProduct|AutoSell", "出售库存货品"),
    (r"CreditShopping\w*", "花信用点"),
    (r"DecomposeWeaponEssence", "分解基质"),
    (r"GiftOperator", "送出礼物"),
    (r"GearAssembly|WeaponUpgrade|Crafting|SimpleProductionBatchStart", "消耗材料"),
    (r"BatchUseDetector", "消耗道具"),
]
RISK_WORDS = re.compile(r"药剂|理智|刻写券|许可|源石|嵌晶玉|消耗|购买|刷新|信用点|调度券|出售|售卖|分解|废弃|赠送|"
                        r"制作|制造|升级|提取|无条件")


def _short(text: str, n: int = 240) -> str:
    text = text.replace("\n", " ")
    return text if len(text) <= n else text[:n] + "…"


def _mxu_default(opt: dict, typ: str) -> dict:
    """The complete typed value MXU itself creates for an option (Fa)."""
    if typ == "switch":
        return {"type": "switch", "value": default_case(opt) in SWITCH_YES}
    if typ == "checkbox":
        return {"type": "checkbox", "caseNames": default_case(opt)}
    if typ == "input":
        return {"type": "input", "values": {i["name"]: str(i.get("default") or "") for i in input_defs(opt)}}
    return {"type": "select", "caseName": default_case(opt)}


def _writer(name: str, opt: dict, typ: str, inp: dict | None = None):
    """Write one edited value into optionValues, creating the full typed entry when it is missing."""
    def write(task: dict, value) -> None:
        ov = task.get("optionValues")
        if not isinstance(ov, dict):
            ov = task["optionValues"] = {}
        entry = ov.get(name)
        if not isinstance(entry, dict) or entry.get("type") != typ:
            entry = _mxu_default(opt, typ)
        if typ == "select":
            entry["caseName"] = value
        elif typ == "switch":
            entry["value"] = bool(value)
        elif typ == "checkbox":
            lo, hi = opt.get("min_count"), opt.get("max_count")
            if (isinstance(lo, int) and len(value) < lo) or (isinstance(hi, int) and len(value) > hi):
                return                     # MXU refuses to start with such a value: keep the old one
            entry["caseNames"] = list(value)
        elif typ == "input":
            if inp is None or not verify_ok(inp, str(value)):
                return
            vals = entry.get("values")
            if not isinstance(vals, dict):
                vals = entry["values"] = {}
            vals[inp["name"]] = str(value)
        ov[name] = entry
    return write


class MaaEndSettings(SettingsProvider):
    key = "maaend"
    verified_version = "v2.34.0"          # interface.json version (MXU 2.7.1, config format 1.0)

    @staticmethod
    def path(g: GameConfig) -> str:
        return os.path.join(g.tool_dir, "config", "mxu-MaaEnd.json")

    def file_paths(self, g):
        return [self.path(g)]

    def config_names(self, g):
        d = read_json(self.path(g)) or {}
        return [i["name"] for i in d.get("instances") or [] if isinstance(i, dict) and i.get("name")]

    def default_config(self, g):
        names = self.config_names(g)
        name = str(g.options.get("instance", "")).strip()
        if name in names:
            return name
        d = read_json(self.path(g)) or {}
        last = next((i.get("name") for i in d.get("instances") or []
                     if isinstance(i, dict) and i.get("id") == d.get("lastActiveInstanceId")), None)
        return last if last in names else (names[0] if names else "")

    def busy(self, g):
        return adapters.get(g.adapter).running_instances(g)

    def load(self, g, config_name):
        path = self.path(g)
        if not os.path.isfile(path):
            raise SettingsError(f"找不到 {path}")
        f = JsonFile(path)
        data = f.data if isinstance(f.data, dict) else {}
        inst = next((i for i in data.get("instances") or []
                     if isinstance(i, dict) and i.get("name") == config_name), None)
        if inst is None or not isinstance(inst.get("tasks"), list):
            raise SettingsError(f"MaaEnd 里没有实例「{config_name}」")
        iface = Interface(g.tool_dir)
        model = Model(files=[f], sections=[], order=inst["tasks"])

        lock = []
        if data.get("version") != CONFIG_FORMAT:
            lock.append(f"配置文件格式版本是 {data.get('version')}（总控按 {CONFIG_FORMAT} 核对过）")
        if iface.interface_version != 2:
            lock.append(f"interface_version 是 {iface.interface_version}（总控只认 2）")
        lock += iface.problems
        gui, ok = gui_version_ok(os.path.join(g.tool_dir, "MaaEnd.exe"), VERIFIED_MXU)
        if gui is None:
            model.drift.append("读不到 MaaEnd.exe（MXU 界面）的版本号，存储格式按 MXU 2.7 / 2.8 处理")
        elif not ok:
            lock.append(f"MaaEnd 界面（MXU）已是 {gui}，总控按 2.7 / 2.8 的存储格式核对过")
        if lock:
            f.exact, f.reason = False, "；".join(([f.reason] if f.reason else []) + lock)
            model.drift += lock

        controllers = [c.get("name") for c in iface.root.get("controller") or [] if isinstance(c, dict)]
        resources = [r.get("name") for r in iface.root.get("resource") or [] if isinstance(r, dict)]
        ctrl = inst.get("controllerName") or (controllers[0] if controllers else "")
        res = inst.get("resourceName") or (resources[0] if resources else "")
        pretasks = {f"__MXU_PRETASK__{p.get('name')}": p for p in iface.pretasks}
        for i, task in enumerate(inst["tasks"]):
            if isinstance(task, dict):
                model.sections.append(self._section(iface, model, task, i, ctrl, res, pretasks))

        present = {t.get("taskName") for t in inst["tasks"] if isinstance(t, dict)}
        usable = [t for t in iface.tasks if in_list(ctrl, t.get("controller"), False)]
        missing = [iface.task_label(t) for t in usable if t.get("name") not in present]
        if missing:
            model.notes.append("实例里没有添加的任务（要用请在 MaaEnd 里添加）：" + "、".join(missing))
        snapshot, fresh = data.get("interfaceTaskSnapshot"), data.get("newTaskNames") or []
        if isinstance(snapshot, list) and snapshot:
            new = [iface.task_label(t) for t in iface.tasks if t.get("name") not in snapshot or t.get("name") in fresh]
            if new:
                model.drift.append("MaaEnd 新增任务（需在 MaaEnd 里添加到实例）：" + "、".join(new))
        if not f.exact:
            model.notes.append(f"只能查看：{f.reason}。请在 MaaEnd 里修改。")
        model.notes.append("没保存过的选项显示的是 MaaEnd 的默认值，改动后才会写入；改动在下次启动 MaaEnd 时生效。")
        return model

    def _section(self, iface: Interface, model: Model, task: dict, index: int, ctrl: str, res: str,
                 pretasks: dict) -> Section:
        name = str(task.get("taskName", ""))
        tdef = iface.task_by_name.get(name)
        pre = pretasks.get(name)
        label = (iface.task_label(tdef) if tdef else iface.text(pre.get("label")) if pre else "") \
            or MXU_SPECIAL.get(name, name)
        title = str(task.get("customName") or label)
        sid = str(task.get("id") or f"task{index}")
        state = "已启用" if task.get("enabled") else "未启用"
        if name in READONLY_TASKS:
            why = "含个人信息" if name == "AccountSwitch" else "会按进程名关游戏 / 关机 / 运行程序，总控预检会拦下"
            return Section(sid, title, task, [], movable=True, subtitle="只读",
                           note=f"⚠ 这个任务{why}，只能在 MaaEnd 里开关和设置。当前：{state}。")
        if name.startswith("__MXU_PRETASK__") or name in MXU_SPECIAL or (name.startswith("__MXU_") and not tdef):
            if name.startswith("__MXU_PRETASK__") and not pre:
                model.drift.append(f"{title}：MaaEnd 已没有这个前置任务，下次打开 MaaEnd 会从实例中移除")
                return Section(sid, title, task, [], movable=True, subtitle="已失效", note=f"只读。当前：{state}。")
            return Section(sid, title, task, [], enable_key="enabled", movable=True, subtitle="MaaEnd 内置",
                           note="这个任务的参数请在 MaaEnd 里设置，这里只能开关和调顺序。")
        if tdef is None:
            model.drift.append(f"{title}：MaaEnd 已删除此任务，下次打开 MaaEnd 会从实例中移除")
            return Section(sid, title, task, [], movable=True, subtitle="已失效",
                           note=f"MaaEnd 已没有这个任务（只读）。当前：{state}。")

        notes = []
        risk = task_risk(iface, name, tdef, TASK_RISK, RISK_WORDS)
        if risk:
            notes.append(f"⚠ 这个任务{risk}。")
        desc = iface.text(tdef.get("description"))
        if desc:
            notes.append(_short(desc))
        if not in_list(ctrl, tdef.get("controller"), False):
            notes.append(f"当前控制器 {ctrl} 不支持这个任务，运行时会跳过。")
        ov = task.get("optionValues")
        broken = ov is not None and not isinstance(ov, dict)
        if broken:
            model.drift.append(f"{title}：optionValues 不是对象，选项只读")
        ov = ov if isinstance(ov, dict) else {}

        def visible(o: dict) -> bool:
            return in_list(ctrl, o.get("controller"), False) and in_list(res, o.get("resource"), False)

        occurrences: dict[str, list[Node]] = {}
        for node in iface.walk(tdef.get("option"), visible):
            occurrences.setdefault(node.name, []).append(node)
        fields, writers, hidden = [], {}, 0
        for oname, nodes in occurrences.items():
            if SENSITIVE.search(oname):
                hidden += 1
                continue
            for fd, write in self._fields(iface, model, title, nodes, ov, occurrences):
                fd.readonly = fd.readonly or broken
                fields.append(fd)
                writers[fd.key] = write
        if hidden:
            notes.append(f"有 {hidden} 项含个人信息的选项不在这里显示，请在 MaaEnd 里设置。")
        known = iface.all_names(tdef.get("option"))
        unknown = [k for k in ov if k not in known]
        if unknown:
            model.drift.append(f"{title}：配置里有 MaaEnd 已不认识的选项 {'、'.join(unknown)}"
                               "（MaaEnd 下次打开会丢弃，总控不动它）")
        groups = "、".join(iface.group_label(x) for x in tdef.get("group") or [])

        def on_set(obj, fd, value, writers=writers):
            if fd.key in writers:
                writers[fd.key](obj, value)
            else:
                set_path(obj, fd.key, value)

        return Section(sid, title, task, fields, enable_key="enabled", movable=True, subtitle=groups,
                       note="\n".join(notes), on_set=on_set)

    def _fields(self, iface: Interface, model: Model, title: str, nodes: list[Node], ov: dict,
                occurrences: dict) -> list[tuple[Field, object]]:
        node = nodes[0]
        name, opt, typ = node.name, node.opt, node.type
        label = iface.label_path(node)
        conds = [] if any(not n.parents for n in nodes) else [iface.condition(n) for n in nodes]
        desc = iface.text(opt.get("description"))
        help_ = "\n".join(x for x in (desc, "；或".join(conds)) if x)
        risk = risk_of(iface, node, OPTION_RISK, RISK_WORDS)
        entry = ov.get(name)
        bad = entry is not None and (not isinstance(entry, dict) or entry.get("type") != typ)
        if bad:
            model.drift.append(f"{title}：「{label}」保存的类型和定义不符，MaaEnd 下次打开会改回默认（暂时只读）")
        enabled_by = ""
        if len(nodes) == 1 and node.parents:
            pname, pdef, case = node.parents[-1]
            if pdef.get("type") == "switch" and case in SWITCH_YES and pname in occurrences \
                    and not SENSITIVE.search(pname):
                enabled_by = ("optionValues", pname, "value")
        common = dict(help=help_, risk=risk, readonly=bad, enabled_by=enabled_by)
        cases = [(c.get("name"), iface.case_label(opt, c)) for c in opt.get("cases") or [] if isinstance(c, dict)]
        names = [c for c, _ in cases]
        out = []
        if typ == "select":
            cur = get_path(ov, (name, "caseName"))
            if cur is not None and cur not in names and not bad:
                model.drift.append(f"{title}：「{label}」保存的选项 {cur} 已不存在，MaaEnd 会改回默认")
            out.append((Field(("optionValues", name, "caseName"), label, "choice", cases,
                              default=default_case(opt), **common), _writer(name, opt, typ)))
        elif typ == "switch":
            out.append((Field(("optionValues", name, "value"), label, "bool",
                              default=default_case(opt) in SWITCH_YES, **common), _writer(name, opt, typ)))
        elif typ == "checkbox":
            lo, hi = opt.get("min_count"), opt.get("max_count")
            if isinstance(lo, int) or isinstance(hi, int):
                common["help"] = "\n".join(x for x in (help_, f"需要选 {lo if lo is not None else 0}"
                                                       f"–{hi if hi is not None else len(names)} 项，数量不对时不会写入") if x)
                cur = get_path(ov, (name, "caseNames"))
                if isinstance(cur, list) and ((isinstance(lo, int) and len(cur) < lo) or
                                              (isinstance(hi, int) and len(cur) > hi)):
                    model.drift.append(f"{title}：「{label}」选的数量不对，MaaEnd 会拒绝启动")
            out.append((Field(("optionValues", name, "caseNames"), label, "multi", cases,
                              default=default_case(opt), **common), _writer(name, opt, typ)))
        elif typ == "input":
            vals = get_path(ov, (name, "values"))
            if vals is not None and not isinstance(vals, dict):
                common["readonly"] = True
            inputs = input_defs(opt)
            known = {i["name"] for i in inputs}
            if isinstance(vals, dict) and set(vals) - known:
                model.drift.append(f"{title}：「{label}」里有已不存在的输入项 {'、'.join(sorted(set(vals) - known))}"
                                   "（MaaEnd 会丢弃）")
            for inp in inputs:
                if inp.get("password") or SENSITIVE.search(inp["name"]):
                    continue
                ilabel = iface.text(inp.get("label"))
                flabel = label if len(inputs) == 1 and (not ilabel or ilabel == label) else f"{label} · {ilabel or inp['name']}"
                cur = vals.get(inp["name"]) if isinstance(vals, dict) else None
                if isinstance(cur, str) and not verify_ok(inp, cur):
                    model.drift.append(f"{title}：「{flabel}」当前值不符合格式要求")
                fd = Field(("optionValues", name, "values", inp["name"]), flabel, "text",
                           default=str(inp.get("default") or ""),
                           **{**common, "help": "\n".join(x for x in (common["help"], input_help(iface, inp)) if x)})
                out.append((fd, _writer(name, opt, typ, inp)))
        elif typ != "hotkey":
            model.drift.append(f"{title}：「{label}」是总控不认识的选项类型 {typ}，请在 MaaEnd 里设置")
        return out
