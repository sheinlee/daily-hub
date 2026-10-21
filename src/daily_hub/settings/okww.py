"""鸣潮 · ok-ww daily settings: the task / global config JSONs under working\\configs.

Evidence: docs/research/settings/wuwa.json (2026-10-18, ok-ww v3.7.4) and the files themselves.
- Every config is a flat dict in working/configs/<name>.json: task configs are named after the task class
  (DailyTask.json ...), global ones after ConfigOption.name ('Basic Options.json' ...).  ok-ww writes them
  with json.dump(indent=4, ensure_ascii=False) in text mode (CRLF, no BOM, no final newline); JsonFile
  reproduces every one of them byte for byte.
- Schema-driven: default_config / config_description / config_type are read from the task classes'
  __init__ along the MRO with ast and a tiny literal interpreter (the tool is never imported), the global
  ones from config.py (ConfigOption(...) and create_basic_options in ok/util/GlobalConfig.py).  Labels
  follow App.tr: Qt context "app" of ok/ui/qt/i18n/zh_CN.ts first, then i18n/zh_CN/LC_MESSAGES/ok.po.
- ok-ww's loader (ok/util/config.py:132-163) drops unknown keys, adds missing ones and resets a value
  whose type differs from the default's -> the page never adds keys and only edits values that pass it.
- Which files the daily run reads is code, not schema (DailyTask.py:77-166): the overlay below names the
  files, the global options that matter, risk texts, ranges and the dangerous keys (always read-only,
  the adapter blocks the run when they are on).
- ok-ww reads its configs once at start and writes them while running -> read-only while it runs.
"""
from __future__ import annotations

import ast
import hashlib
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from .. import adapters
from ..config import GameConfig
from .base import Field, JsonFile, Model, Section, SettingsError, SettingsProvider, get_path, set_path

F = Field
_CFG_ATTRS = frozenset({"default_config", "config_description", "config_type"})
_QT_INT_MAX = 99_999_999


# ----------------------------------------------------------------------------- overlay
@dataclass(frozen=True)
class _Spec:
    id: str
    file: str                    # name under working/configs, without .json
    source: str                  # "task" (one-time / trigger task class) or "global" (ConfigOption)
    group: str
    only: tuple = ()             # show just these keys (empty: every option of the config)
    note: str = ""


SECTIONS = [
    _Spec("daily", "DailyTask", "task", "日常",
          note="总控运行的就是这个任务（-t src.task.DailyTask.DailyTask）。刷体力用这里的选项，不用单独的"
               "无音区 / 凝素 / 模拟领域任务的设置。流程：登录 → 读日常进度 → [梦魇巢穴] → 刷体力 → 领日常 → "
               "领邮件 → 领通行证 → 附加任务。"),
    _Spec("nightmare", "NightmareNestTask", "task", "日常",
          note="附加任务勾了“自动刷所有梦魇巢穴”，或者“使用梦魇巢穴获取日常声骸”生效（刷什么不是无音区）时，"
               "日常会用这里的“刷什么”。"),
    _Spec("farm4c", "FarmEchoTask", "task", "日常",
          note="只有附加任务勾了“传送并刷取4C声骸”时日常才会用这里，而且“传送至Boss”不能是“否”。"
               "这里的“完成后退出”只在单独运行这个任务时生效。"),
    _Spec("combat", "AutoCombatTask", "task", "日常", only=("Switch to Healer before and after Combat",),
          note="这里只列出日常战斗（无音区等）会读的一项；其余选项只影响自动战斗任务本身，请在 ok-ww 里改。"),
    _Spec("monthly", "Monthly Card Config", "global", "全局设置"),
    _Spec("basic", "Basic Options", "global", "全局设置",
          only=("Exit App when Game Exits", "Game Package", "Launch with DX11", "Auto Resize Game Window",
                "Use DirectML", "Trigger Interval"),
          note="只列出与无人值守日常有关的项，其余（热键、托盘、静音、模糊遮挡等）请在 ok-ww 里改。"),
    _Spec("chars", "Character Config", "global", "全局设置", note="影响战斗出招逻辑。"),
    _Spec("hotkeys", "Game Hotkey", "global", "全局设置",
          note="必须与游戏内的按键设置一致，平时不用改。ok-ww 运行时会自动识别部分按键并写回这个文件。"),
]

_DANGER = "开启后会强杀所有鸣潮客户端或让 ok-ww 自己退出，总控预检会拦截；只读，请在 ok-ww 里关掉"
# Read-only whatever the version (adapters/okww.py problems() blocks the run when they are on).
DANGEROUS = {("*", "Exit After Task"), ("basic", "Exit App when Game Exits")}

ADD_4C = "Teleport and Farm 4C Echo"
ADD_NEST = "Auto Farm all Nightmare Nest"

# (section id, key) -> label / help / risk / min / max / max_from (class, attr[, index]) / known options
OVERLAY: dict[tuple, dict] = {
    ("daily", "Which to Farm"): dict(
        known=("Tacet Suppression", "Forgery Challenge", "Simulation Challenge"),
        risk="消耗结晶波片：日常刷满 180，之后自然体力还有 60 就继续刷；自然体力不够时会自动动用结晶单质"
             "（备用体力），没有开关可关",
        help="日常用哪个副本把结晶波片刷到 180。选“无音区”时“使用梦魇巢穴获取日常声骸”不生效。"),
    ("daily", "Which Tacet Suppression to Farm"): dict(min=1, max_from=("TacetTask", "total_number")),
    ("daily", "Which Forgery Challenge to Farm"): dict(min=1, max_from=("ForgeryTask", "total_number")),
    ("daily", "Farm Nightmare Nest for Daily Echo"): dict(
        help="只在“刷什么”不是无音区、且日常奖励还没就绪时生效；刷哪类巢穴看“噩梦巢穴任务”的“刷什么”。"),
    ("daily", "Additional Tasks to Run After Daily Task"): dict(
        known=("Check Weekly Garden", ADD_NEST, "Merge Echo If discarded > 1000", ADD_4C),
        risk="检查每周乐园：周常没满时跑约 18 分钟并在局内商店购买；融合：把已弃置声骸批量合成掉（不可逆）；"
             "传送并刷取4C：日常要等它刷完才报完成，总控会超时",
        help="执行顺序固定为乐园 → 融合 → 4C，与勾选顺序无关。"),
    ("nightmare", "Which to Farm"): dict(
        help="与“每日任务”的“刷什么”同名，但这里是多选。勾了“自动刷所有梦魇巢穴”时至少要选一项，否则日常一开始就报错。"),
    ("farm4c", "Teleport to Boss"): dict(help="日常接续 4C 时不能选“否”，否则日常一开始就报错。"),
    ("farm4c", "Which Weekly Boss to Teleport"): dict(min=1, max_from=("FarmEchoTask", "total_weekly_number")),
    ("farm4c", "Which Boss Challenge to Teleport"): dict(min=1, max_from=("FarmEchoTask", "total_boss_number")),
    ("farm4c", "Which Special Nightmare to Teleport"): dict(
        min=1, max_from=("FarmEchoTask", "nightmare_structure", -1)),
    ("farm4c", "Boss"): dict(label="Boss 配置"),
    ("farm4c", "Repeat Farm Count"): dict(
        risk="耗时：日常接续 4C 时要等它刷完才报完成，次数大时总控只能超时强停",
        help="默认 10000，相当于一直刷到手动停止。"),
    ("combat", "Switch to Healer before and after Combat"): dict(
        help="日常和无音区的战斗用的是这一项（4C 任务用它自己的那一项）。"),
    ("monthly", "Monthly Card Time"): dict(
        min=1, max=23,
        help="按本机时间填（小时）。国服在北京时间 04:00 刷新，电脑不在北京时间时请自行换算。"
             "代码用 datetime.replace(hour=值)，填 24 会报错，所以这里限制在 1-23。"),
    ("basic", "Game Package"): dict(
        label="游戏资源包（-krqlv 参数）",
        help="ok-ww 自己拉起游戏时附加 -krqlv=<值>，应与本机安装的资源包一致。总控默认自己拉起游戏，不经过这一项。"),
    ("basic", "Launch with DX11"): dict(help="开启后 ok-ww 自己拉起游戏时会加 -dx11 等参数，与总控拉起游戏的参数不一致。"),
    ("basic", "Auto Resize Game Window"): dict(
        help="窗口不是 16:9 或小于 1280x720 时，ok-ww 会把游戏窗口改成 2560x1440、1920x1080、1600x900 或 1280x720。"),
}

# Keys each config had in ok-ww v3.7.4 (from the schema; '_' keys excluded).  Keys added later are shown with
# ok-ww's own labels but reported, since their resource use was not reviewed.
KNOWN_KEYS = {
    "daily": frozenset({
        "Which to Farm", "Which Tacet Suppression to Farm", "Which Forgery Challenge to Farm",
        "Material Selection", "Farm Nightmare Nest for Daily Echo", "Additional Tasks to Run After Daily Task",
        "Exit After Task"}),
    "nightmare": frozenset({"Which to Farm"}),
    "farm4c": frozenset({
        "Teleport to Boss", "Boss Level", "Boss", "Repeat Farm Count", "Combat Wait Time", "Echo Pickup Method",
        "Use Liberation", "Switch to Healer before and after Combat", "Which Weekly Boss to Teleport",
        "Which Boss Challenge to Teleport", "Which Special Nightmare to Teleport", "Exit After Task"}),
    "monthly": frozenset({"Check Monthly Card", "Monthly Card Time"}),
    "chars": frozenset({"Iuno C6", "Chisa DPS"}),
    "hotkeys": frozenset({
        "Echo Key", "Liberation Key", "Resonance Key", "Tool Key", "Jump Key", "Dodge Key", "Wheel Key",
        "Guidebook Key", "Bag Key"}),
}

# Sub-tasks DailyTask.run starts (get_task_by_class / run_task_by_class) at v3.7.4.
KNOWN_CHAIN = frozenset({"NightmareNestTask", "TacetTask", "ForgeryTask", "SimulationTask", "FarmEchoTask",
                         "GardenTask", "MergeEchoTask"})

# sha256 of the sources the schema handling and write-back format depend on (v3.7.4, 2026-10-18): a change
# under the same version number (hot fix) is reported.
SOURCE_SHA256 = {
    "config.py": "fb563b75456abc1f96adabeb51fbcebf4ca9377dc78b210fd5e592ea2b563587",
    "src/task/DailyTask.py": "e6c8dd4755d963ea31addd7a5b742d28288c21a136ba64deac51135646d39bd1",
    "src/task/NightmareNestTask.py": "4a35e8c42ec22e11c80bd6f2183396afd9b4903f05c85e4116a2dc1b70b010b4",
    "src/task/FarmEchoTask.py": "5f6319a1124965b0c4d37dd26a4c98b56c5bfdac6cb23e9631cdd9bc40594477",
    "src/task/AutoCombatTask.py": "9f505ddea3ac77747a17e791e6695d8f70525b479d30d9d3d135cf32daa19940",
    "src/task/TacetTask.py": "7b1db37d745c54cba88a093d7410d9fc7d94e637afab74c04ed6a2a57d56dfb2",
    "src/task/ForgeryTask.py": "ca9828a68eec45cd60dbf48c9fcc4abb4d5a5bd5feb9258e56ccef97399002b0",
    "src/task/BaseWWTask.py": "f3a8eef7d4c9677358005d5264a57fbb02e1cd85d2a7b2010e5218a68c4ff105",
    "ok/util/config.py": "a688d5eea25e7f124a3c69c2be29bc223a594db3f10dd5693111fc54608cdc82",
    "ok/util/file.py": "a991e5fa41d31e36b8c65580a96fe41919930b46918799a2c3e286114fbf2696",
    "ok/util/GlobalConfig.py": "f4acf0385be3ecebbd5c2d68175c7930636961727a736bce22c0d5c8aa37a7eb",
    "ok/task/task.py": "e09e6f86761c9529ae97b0ffb5ce5bd30d0a24db4762d0eeded7bbbbffb70f48",
}


# ----------------------------------------------------------------------------- static reader
class _Unknown:
    __slots__ = ("why",)

    def __init__(self, why: str):
        self.why = why

    def __repr__(self):
        return f"<?{self.why}>"


class _Obj:
    """`self` of a task, or a ConfigOption."""

    def __init__(self, **attrs):
        self.attrs = dict(attrs)


@dataclass
class _Func:
    node: ast.FunctionDef
    path: str


@dataclass
class _Lazy:                     # `from a.b import name`, resolved when used
    module: str
    name: str


class _Return(Exception):
    def __init__(self, value):
        self.value = value


def _relevant(node: ast.AST) -> bool:
    """Could this statement touch default_config / config_description / config_type?"""
    return any((isinstance(n, ast.Attribute) and n.attr in _CFG_ATTRS) or (isinstance(n, ast.Name) and n.id in _CFG_ATTRS)
               for n in ast.walk(node))


def _has_unknown(x) -> bool:
    if isinstance(x, _Unknown):
        return True
    if isinstance(x, dict):
        return any(_has_unknown(k) or _has_unknown(v) for k, v in x.items())
    if isinstance(x, (list, tuple)):
        return any(_has_unknown(v) for v in x)
    return False


class _Source:
    """ok-ww's option definitions read from its sources with ast (nothing is imported or executed)."""

    def __init__(self, working: str):
        self.w = working
        self._trees: dict[str, ast.Module | None] = {}
        self._envs: dict[str, dict] = {}
        self._index: dict[str, list[str]] | None = None
        self._mro: dict[tuple, list] = {}
        self._chain: list = []
        self._depth = 0
        self.notes: list[str] = []
        self._ctx = ""

    # ---- files / modules
    def tree(self, path: str) -> ast.Module | None:
        if path not in self._trees:
            try:
                self._trees[path] = ast.parse(Path(path).read_bytes(), path)
            except (OSError, SyntaxError, ValueError):
                self._trees[path] = None
        return self._trees[path]

    def module_path(self, dotted: str) -> str | None:
        base = os.path.join(self.w, *dotted.split("."))
        for p in (base + ".py", os.path.join(base, "__init__.py")):
            if os.path.isfile(p):
                return p
        return None

    def env(self, path: str) -> dict:
        """Module-level names: literals, functions and lazy imports (module-level statements run in order)."""
        if path in self._envs:
            return self._envs[path]
        env: dict = {}
        self._envs[path] = env                       # placeholder first: import cycles stop here
        tree = self.tree(path)
        for st in (tree.body if tree else []):
            if isinstance(st, ast.ImportFrom) and st.module and not st.level:
                for a in st.names:
                    if a.name != "*":
                        env.setdefault(a.asname or a.name, _Lazy(st.module, a.name))
            elif isinstance(st, ast.FunctionDef):
                env[st.name] = _Func(st, path)
            elif isinstance(st, (ast.Assign, ast.AnnAssign, ast.Expr)):
                self.run([st], env, env, strict=False)
        return env

    def resolve(self, v, depth: int = 0):
        while isinstance(v, _Lazy) and depth < 8:
            p = self.module_path(v.module)
            v = self.env(p).get(v.name, _Unknown(f"{v.module}.{v.name}")) if p else _Unknown(v.module)
            depth += 1
        return v

    # ---- classes
    def index(self) -> dict[str, list[str]]:
        if self._index is None:
            self._index = {}
            pat = re.compile(rb"^class\s+(\w+)", re.M)
            for root in ("src", "ok"):               # src first: its classes win a name clash
                for dp, dn, fn in os.walk(os.path.join(self.w, root)):
                    dn[:] = [d for d in dn if d != "__pycache__"]
                    for f in fn:
                        if f.endswith(".py"):
                            p = os.path.join(dp, f)
                            try:
                                data = Path(p).read_bytes()
                            except OSError:
                                continue
                            for m in pat.finditer(data):
                                self._index.setdefault(m.group(1).decode(), []).append(p)
        return self._index

    def find_class(self, name: str, path: str | None = None) -> tuple[str, ast.ClassDef] | None:
        for p in ([path] if path else []) + self.index().get(name, []):
            tree = self.tree(p)
            for node in (tree.body if tree else []):
                if isinstance(node, ast.ClassDef) and node.name == name:
                    return p, node
        return None

    def mro(self, name: str, path: str | None = None, depth: int = 0) -> list[tuple[str, str, ast.ClassDef]]:
        """C3 linearisation by base-class names: [(name, path, ClassDef)]."""
        if (name, path) in self._mro:
            return self._mro[(name, path)]
        found = self.find_class(name, path)
        if found is None or depth > 20:
            return []
        p, node = found
        bases = [b.id if isinstance(b, ast.Name) else b.attr for b in node.bases
                 if isinstance(b, (ast.Name, ast.Attribute))]
        lin = [self.mro(b, None, depth + 1) for b in bases]
        seqs = [s for s in lin + [[x[0] for x in lin if x]] if s]
        out = [(name, p, node)]
        while seqs:
            for s in seqs:
                head = s[0]
                if not any(head[0] in [x[0] for x in t[1:]] for t in seqs):
                    break
            else:
                raise SettingsError(f"ok-ww 的类 {name} 继承关系无法解析")
            out.append(head)
            seqs = [t for t in ([x for x in t if x[0] != head[0]] for t in seqs) if t]
        self._mro[(name, path)] = out
        return out

    def task(self, module: str, cls: str) -> dict:
        """default_config / config_description / config_type and other attrs a task's __init__ sets."""
        self.notes = []
        self._ctx = cls
        chain = self.mro(cls, self.module_path(module))
        if not chain:
            raise SettingsError(f"在 ok-ww 源码里找不到任务类 {cls}")
        me = _Obj()
        self._chain = chain
        for name, path, node in reversed(chain):          # base classes first (all call super().__init__ first)
            init = next((n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == "__init__"), None)
            if init is not None:
                self._ctx = name
                self.run(init.body, {"self": me}, self.env(path), strict=True)
        return self._finish(me.attrs, cls)

    def _finish(self, attrs: dict, what: str) -> dict:
        out = {k: attrs.get(k) if isinstance(attrs.get(k), dict) else {} for k in _CFG_ATTRS}
        for k in _CFG_ATTRS:
            if _has_unknown(attrs.get(k)) or not isinstance(attrs.get(k), dict):
                self.notes.append(f"{what}.{k} 含有总控读不懂的写法")
        out.update(attrs={k: v for k, v in attrs.items() if k not in _CFG_ATTRS}, notes=list(self.notes))
        return out

    def registry(self) -> dict:
        """config.py: onetime_tasks / trigger_tasks ([module, class]) and global_configs (ConfigOption)."""
        path = os.path.join(self.w, "config.py")
        self.notes = []
        self._ctx = "config.py"
        cfg = self.env(path).get("config")
        if not isinstance(cfg, dict):
            raise SettingsError("ok-ww 的 config.py 里找不到 config = {...}")
        tasks = {}
        for kind in ("onetime_tasks", "trigger_tasks"):
            for item in cfg.get(kind) or []:
                if isinstance(item, list) and len(item) == 2 and all(isinstance(x, str) for x in item):
                    tasks[item[1]] = item[0]
        globals_ = {}
        for opt in cfg.get("global_configs") or []:
            opt = self.resolve(opt)
            if isinstance(opt, _Obj) and isinstance(opt.attrs.get("name"), str):
                self.notes = []
                globals_[opt.attrs["name"]] = self._finish(opt.attrs, opt.attrs["name"])
        return {"tasks": tasks, "globals": globals_}

    def calls_in(self, path: str, funcs: tuple) -> set[str]:
        """Names passed as first argument to self.<funcs>(...) anywhere in a module."""
        out = set()
        for n in ast.walk(self.tree(path) or ast.Module(body=[], type_ignores=[])):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in funcs \
                    and n.args and isinstance(n.args[0], ast.Name):
                out.add(n.args[0].id)
        return out

    # ---- the interpreter
    def _note(self, strict: bool, st: ast.AST, what: str):
        if strict and _relevant(st):
            self.notes.append(f"{self._ctx}: 第 {getattr(st, 'lineno', '?')} 行{what}，总控读不懂")

    def ev(self, node, local: dict, glob: dict):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Dict):
            out = {}
            for k, v in zip(node.keys, node.values):
                val = self.ev(v, local, glob)
                if k is None:
                    if isinstance(val, dict):
                        out.update(val)
                    else:
                        out[_Unknown("**")] = val
                else:
                    out[self.ev(k, local, glob)] = val
            return out
        if isinstance(node, (ast.List, ast.Tuple)):
            vals = [self.ev(e, local, glob) for e in node.elts]
            return vals if isinstance(node, ast.List) else tuple(vals)
        if isinstance(node, ast.Name):
            v = local[node.id] if node.id in local else glob.get(node.id, _Unknown(node.id))
            return self.resolve(v)
        if isinstance(node, ast.Attribute):
            base = self.ev(node.value, local, glob)
            return base.attrs.get(node.attr, _Unknown(node.attr)) if isinstance(base, _Obj) else _Unknown(node.attr)
        if isinstance(node, ast.Subscript):
            base, idx = self.ev(node.value, local, glob), self.ev(node.slice, local, glob)
            try:
                return base[idx]
            except (TypeError, KeyError, IndexError):
                return _Unknown("subscript")
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            a, b = self.ev(node.left, local, glob), self.ev(node.right, local, glob)
            if type(a) is type(b) and isinstance(a, (int, float, str, list, tuple)):
                return a + b
            return _Unknown("+")
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            v = self.ev(node.operand, local, glob)
            return -v if isinstance(v, (int, float)) and not isinstance(v, bool) else _Unknown("-")
        if isinstance(node, ast.Call):
            return self._call(node, local, glob)
        return _Unknown(type(node).__name__)

    def _call(self, node: ast.Call, local: dict, glob: dict):
        fn = node.func
        if not isinstance(fn, ast.Name) or any(isinstance(a, ast.Starred) for a in node.args):
            return _Unknown("call")
        args = [self.ev(a, local, glob) for a in node.args]
        kw = {k.arg: self.ev(k.value, local, glob) for k in node.keywords if k.arg}
        if fn.id == "ConfigOption":                         # ok/util/config.py:10-14
            names = ["name", "default_config", "description", "config_description", "config_type"]
            a = dict(zip(names, args))
            a.update({("default_config" if k == "default" else k): v for k, v in kw.items() if k in names + ["default"]})
            return _Obj(name=a.get("name"), description=a.get("description", ""),
                        default_config=a.get("default_config") or {}, config_description=a.get("config_description") or {},
                        config_type=a.get("config_type") or {})
        if fn.id in ("dict", "list") and len(args) <= 1 and not kw:
            src = args[0] if args else ([] if fn.id == "list" else {})
            ok = isinstance(src, dict) if fn.id == "dict" else isinstance(src, (list, tuple))
            return (dict(src) if fn.id == "dict" else list(src)) if ok else _Unknown(fn.id)
        if fn.id == "sum" and len(args) == 1 and isinstance(args[0], (list, tuple)) \
                and all(isinstance(x, int) for x in args[0]):
            return sum(args[0])
        f = self.ev(fn, local, glob)
        if isinstance(f, _Func):
            return self._invoke(f.node, self.env(f.path), args, kw, None)
        return _Unknown(fn.id)

    def _invoke(self, node: ast.FunctionDef, glob: dict, args: list, kw: dict, me: _Obj | None, depth: int = 0):
        if depth > 4 or self._depth > 4:
            return _Unknown("depth")
        self._depth += 1
        try:
            return self._invoke_body(node, glob, args, kw, me, depth)
        finally:
            self._depth -= 1

    def _invoke_body(self, node: ast.FunctionDef, glob: dict, args: list, kw: dict, me: _Obj | None, depth: int):
        a = node.args
        params = [p.arg for p in a.posonlyargs + a.args]
        local: dict = {}
        defaults = a.defaults
        for p, d in zip(params[len(params) - len(defaults):], defaults):
            local[p] = self.ev(d, glob, glob)
        if me is not None:
            local[params[0]] = me
            params = params[1:]
        local.update(zip(params, args))
        local.update({k: v for k, v in kw.items() if k in params or k in [x.arg for x in a.kwonlyargs]})
        try:
            self.run(node.body, local, glob, strict=me is not None, depth=depth)
        except _Return as r:
            return r.value
        return None

    def _store(self, t, val, local: dict, glob: dict, st, strict: bool):
        if isinstance(t, ast.Name):
            local[t.id] = val
        elif isinstance(t, ast.Attribute):
            base = self.ev(t.value, local, glob)
            if isinstance(base, _Obj):
                base.attrs[t.attr] = val
            else:
                self._note(strict, st, "的赋值")
        elif isinstance(t, ast.Subscript):
            cont, key = self.ev(t.value, local, glob), self.ev(t.slice, local, glob)
            if isinstance(cont, dict) and not isinstance(key, _Unknown):
                cont[key] = val
            else:
                self._note(strict, st, "的下标赋值")
        else:
            self._note(strict, st, "的赋值")

    def run(self, body: list, local: dict, glob: dict, strict: bool, depth: int = 0):
        for st in body:
            if isinstance(st, ast.Assign):
                val = self.ev(st.value, local, glob)
                if isinstance(val, _Unknown):
                    self._note(strict, st, "的值")
                for t in st.targets:
                    self._store(t, val, local, glob, st, strict)
            elif isinstance(st, ast.AnnAssign) and st.value is not None:
                self._store(st.target, self.ev(st.value, local, glob), local, glob, st, strict)
            elif isinstance(st, ast.Expr) and isinstance(st.value, ast.Call):
                call, fn = st.value, st.value.func
                if isinstance(fn, ast.Attribute) and fn.attr == "update" and len(call.args) == 1 and not call.keywords:
                    cont, arg = self.ev(fn.value, local, glob), self.ev(call.args[0], local, glob)
                    if isinstance(cont, dict) and isinstance(arg, dict):
                        cont.update(arg)
                    else:
                        self._note(strict, st, "的 update")
                elif isinstance(fn, ast.Attribute) and fn.attr == "__init__":
                    continue                                       # super().__init__(): bases run separately
                elif isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) \
                        and isinstance(local.get(fn.value.id), _Obj) and strict:
                    self._self_call(fn.attr, call, local, glob, st, depth)
                elif isinstance(fn, ast.Name):
                    self._call(call, local, glob)
                else:
                    self._note(strict, st, "的调用")
            elif isinstance(st, ast.Return):
                raise _Return(self.ev(st.value, local, glob) if st.value is not None else None)
            elif isinstance(st, ast.If):
                test = self.ev(st.test, local, glob)
                if isinstance(test, _Unknown):
                    self._note(strict, st, "的条件")
                else:
                    self.run(st.body if test else st.orelse, local, glob, strict, depth)
            elif isinstance(st, (ast.Pass, ast.Import, ast.ImportFrom, ast.Expr, ast.FunctionDef, ast.ClassDef)):
                continue
            else:
                self._note(strict, st, f"（{type(st).__name__}）")

    def _self_call(self, name: str, call: ast.Call, local: dict, glob: dict, st, depth: int):
        """self.<method>(...) inside __init__: run it when it touches the config dicts (add_exit_after_config ...)."""
        for _n, path, node in self._chain:
            meth = next((n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == name), None)
            if meth is None:
                continue
            if _relevant(meth):
                args = [self.ev(a, local, glob) for a in call.args]
                kw = {k.arg: self.ev(k.value, local, glob) for k in call.keywords if k.arg}
                me = local[call.func.value.id]
                self._invoke(meth, self.env(path), args, kw, me, depth + 1)
            return
        self._note(True, st, f" self.{name}()")


# ----------------------------------------------------------------------------- labels
_PO_LINE = re.compile(r'(msgctxt|msgid_plural|msgid|msgstr(?:\[\d+\])?)\s+"(.*)"$')


def _unescape(s: str) -> str:
    table = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}
    return re.sub(r"\\(.)", lambda m: table.get(m.group(1), m.group(1)), s)


def parse_po(text: str) -> dict[str, str]:
    """msgid -> msgstr of a gettext .po file (fuzzy, context and plural entries skipped, like msgfmt + gettext())."""
    out: dict[str, str] = {}
    entry: dict[str, str] = {}
    cur = None
    fuzzy = False

    def flush():
        mid, mstr = entry.get("msgid"), entry.get("msgstr")
        if mid and mstr and not fuzzy and "msgctxt" not in entry and "msgid_plural" not in entry:
            out[mid] = mstr

    for raw in text.splitlines():
        line = raw.strip()
        m = _PO_LINE.match(line)
        starts = not line or line.startswith("#") or (m is not None and m.group(1) in ("msgctxt", "msgid"))
        if starts and any(k.startswith("msgstr") for k in entry):    # the previous entry is complete
            flush()
            entry, cur, fuzzy = {}, None, False
        if not line:
            continue
        if line.startswith("#"):
            if line.startswith("#,") and "fuzzy" in line:
                fuzzy = True
            continue
        if m:
            cur = m.group(1)
            entry[cur] = _unescape(m.group(2))
        elif line.startswith('"') and line.endswith('"') and cur:
            entry[cur] += _unescape(line[1:-1])
    flush()
    return out


class _Tr:
    """App.tr (ok/__init__.py:381-407): Qt context "app" (zh_CN.ts), then gettext ok.po, else the source text."""

    def __init__(self, working: str):
        self.qt: dict[str, str] = {}
        self.po: dict[str, str] = {}
        ts = os.path.join(working, "ok", "ui", "qt", "i18n", "zh_CN.ts")
        try:
            for ctx in ET.parse(ts).getroot().iter("context"):
                if ctx.findtext("name") != "app":
                    continue
                for m in ctx.iter("message"):
                    t = m.find("translation")
                    if m.findtext("source") and t is not None and t.text \
                            and t.get("type") not in ("unfinished", "obsolete", "vanished"):
                        self.qt[m.findtext("source")] = t.text
        except (OSError, ET.ParseError):
            pass
        try:
            po = Path(working, "i18n", "zh_CN", "LC_MESSAGES", "ok.po").read_text(encoding="utf-8")
            self.po = parse_po(po)
        except (OSError, UnicodeDecodeError):
            pass

    def __call__(self, s):
        if not isinstance(s, str) or not s:
            return "" if s is None else str(s)
        return self.qt.get(s) or self.po.get(s) or s

    def has(self, s) -> bool:
        return isinstance(s, str) and (s in self.qt or s in self.po)


# ----------------------------------------------------------------------------- fields
def _k(key: str):
    """Field key for an ok-ww option name (a name with a dot must not be split into a path)."""
    return (key,) if "." in key else key


def _kind(default, ctype: dict) -> str | None:
    """ok/core/config_schema.py:6-16 resolve_config_type + ConfigItemFactory.py:19-71 -> hub field kind.

    None: not shown (hidden, buttons, global); "?": a widget the hub does not know (shown read-only)."""
    if ctype.get("hidden"):
        return None
    t = ctype.get("type")
    if not t:
        if "buttons" in ctype or "callback" in ctype:
            return None
        if "options" in ctype:
            t = "multi_selection" if isinstance(default, list) else "drop_down"
    if t in ("global", "button"):
        return None
    if t == "drop_down":
        return "?" if isinstance(default, list) else "choice"
    if t == "multi_selection":
        return "multi"
    if t == "text_edit":
        return "textarea"
    if t in ("line_edit", "file_selector"):
        return "text"
    if t:
        return "?"
    if isinstance(default, bool):
        return "bool"
    if isinstance(default, int):
        return "int"
    if isinstance(default, float):
        return "float"
    if isinstance(default, list):
        return "list"
    if isinstance(default, str):
        return "textarea" if len(default) > 16 or "\n" in default else "text"
    return "?"


def _parents(ctypes: dict) -> dict[str, tuple[str, list]]:
    """child key -> (parent key, parent values that show it), from config_type[...]['sub_configs']."""
    out: dict[str, tuple[str, list]] = {}
    for parent, ct in ctypes.items():
        subs = ct.get("sub_configs") if isinstance(ct, dict) else None
        if not isinstance(subs, dict):
            continue
        for value, children in subs.items():
            for child in children if isinstance(children, list) else []:
                out.setdefault(child, (parent, []))[1].append(value)
    return out


class OkWwSettings(SettingsProvider):
    key = "okww"
    verified_version = "v3.7.4"

    @staticmethod
    def working(g: GameConfig) -> str:
        return os.path.join(g.tool_dir, "working")

    def path(self, g: GameConfig, spec: _Spec) -> str:
        return os.path.join(self.working(g), "configs", spec.file + ".json")

    def file_paths(self, g):
        return [self.path(g, s) for s in SECTIONS]       # never Notification.json / devices.json ...

    def busy(self, g):
        return adapters.get(g.adapter).running_instances(g)

    def load(self, g, config_name):
        work = self.working(g)
        if not os.path.isdir(os.path.join(work, "configs")):
            raise SettingsError(f"找不到 {os.path.join(work, 'configs')}")
        src = _Source(work)
        tr = _Tr(work)
        reg = src.registry()
        model = Model(files=[], sections=[])
        if src.notes:
            model.drift += [f"config.py：{n}" for n in src.notes]
        self._check_sources(g, work, model)
        schemas = {}
        for spec in SECTIONS:
            if spec.source == "task":
                module = reg["tasks"].get(spec.file)
                if module is None:
                    model.drift.append(f"ok-ww 已不再注册任务 {spec.file}，这一页不显示它")
                    continue
                schema = schemas[spec.file] = src.task(module, spec.file)
            else:
                schema = reg["globals"].get(spec.file)
                if schema is None:
                    model.drift.append(f"ok-ww 已不再有全局设置「{spec.file}」，这一页不显示它")
                    continue
            path = self.path(g, spec)
            if not os.path.isfile(path):
                model.notes.append(f"还没有 {spec.file}.json（ok-ww 第一次运行时生成），先不显示「{tr(spec.file)}」")
                continue
            try:
                f = JsonFile(path)
            except ValueError as e:
                raise SettingsError(f"{spec.file}.json 不是有效的 JSON（ok-ww 可能正在写它），稍后点“从脚本同步”：{e}")
            if not isinstance(f.data, dict):
                model.drift.append(f"{spec.file}.json 不是 ok-ww 的配置格式，不显示")
                continue
            model.files.append(f)
            model.sections.append(self._section(spec, schema, f.data, tr, src, model))
        self._check_chain(work, src, reg, model)
        on_set = self._on_set(model)
        for s in model.sections:
            s.on_set = on_set
        self._cross_check(model)
        self._package_note(g, model)
        if not model.exact:
            model.notes.append("有文件的格式无法原样写回，只能查看，请在 ok-ww 里修改。")
        model.notes.append("改动在下次启动 ok-ww 时生效；ok-ww 开着时这里只能查看。")
        return model

    # ---- one section
    def _section(self, spec: _Spec, schema: dict, data: dict, tr: _Tr, src: _Source, model: Model) -> Section:
        defaults, descs, ctypes = schema["default_config"], schema["config_description"], schema["config_type"]
        attrs = schema["attrs"]
        title = tr(attrs.get("name") if spec.source == "task" else spec.file) or spec.file
        desc = tr(attrs.get("description"))
        partial = bool(schema["notes"])
        if partial:
            model.drift.append(f"{title}：ok-ww 的选项定义有总控读不懂的写法（{'；'.join(schema['notes'][:3])}），只读")
        parents = _parents(ctypes)
        wanted = [k for k in data if isinstance(k, str) and not k.startswith("_") and (not spec.only or k in spec.only)]
        known = KNOWN_KEYS.get(spec.id)
        fields = []
        for key in wanted:
            if key not in defaults:
                model.drift.append(f"{title}：「{key}」已不是 ok-ww 的选项，ok-ww 下次启动会删掉它")
                continue
            d, ct = defaults[key], ctypes.get(key) if isinstance(ctypes.get(key), dict) else {}
            kind = _kind(d, ct)
            if kind is None:
                continue
            fd = self._field(spec, key, d, ct, descs.get(key), kind, data[key], parents, tr, src, model, title)
            fd.readonly = fd.readonly or partial
            if known is not None and key not in known:
                model.drift.append(f"{title}：新选项「{fd.label}」（按 ok-ww 自己的说明显示，资源消耗总控未复核）")
            fields.append(fd)
        for key in defaults:
            if isinstance(key, str) and not key.startswith("_") and key not in data and (not spec.only or key in spec.only):
                model.drift.append(f"{title}：文件里还没有「{tr(key)}」，ok-ww 下次启动会按默认值 {defaults[key]!r} 补上")
        for key in spec.only:
            if key not in defaults:
                model.drift.append(f"{title}：「{key}」已不存在")
        for (sid, key) in OVERLAY:
            if sid == spec.id and key not in defaults:
                model.drift.append(f"{title}：总控说明过的「{key}」已不存在")
        note = "\n".join(x for x in (desc if desc != title else "", spec.note) if x)
        return Section(spec.id, title, data, fields, note=note, group=spec.group)

    def _field(self, spec: _Spec, key: str, default, ct: dict, desc, kind: str, value, parents: dict,
               tr: _Tr, src: _Source, model: Model, title: str) -> Field:
        ov = OVERLAY.get((spec.id, key), {})
        label = ov.get("label") or tr(key)
        helps = [tr(desc)] if isinstance(desc, str) and desc else []
        enabled_by = ""
        readonly = False
        if key in parents:
            pkey, values = parents[key]
            if values == [True]:
                enabled_by = _k(pkey)
            else:
                shown = "或".join(f"「{tr(v)}」" for v in values)
                helps.insert(0, f"仅在「{tr(pkey)}」选了{shown}时生效。")
        if ov.get("help"):
            helps.append(ov["help"])
        if not ov.get("label") and not tr.has(key):
            helps.append("（ok-ww 里这一项也没有中文名）")
        options = ct.get("options") if isinstance(ct.get("options"), list) else []
        choices = [(o, tr(o)) for o in options] if kind in ("choice", "multi") else []
        lo, hi = 0, 999_999
        if kind == "int":
            lo = ct.get("min", 0 if default == 0 else (-999_999 if default < 0 else 1))
            hi = ct.get("max", _QT_INT_MAX)
            lo = ov.get("min", lo)
            hi = ov.get("max", hi)
            if "max_from" in ov:
                hi = self._max_from(ov["max_from"], src, hi, model, title, label)
        elif kind == "float":
            lo, hi = ct.get("min", 0), ct.get("max", 99.99)        # QDoubleSpinBox defaults (inferred)
        if kind == "?":
            kind, readonly = "text", True
            model.drift.append(f"{title}：「{label}」的控件类型总控不认识，只读")
        if not isinstance(value, type(default)):                  # ok/util/config.py:151-156
            readonly = True
            model.drift.append(f"{title}：「{label}」的值类型与 ok-ww 的默认值不同，ok-ww 下次启动会把它重置为 {default!r}")
        if "known" in ov and tuple(options) != tuple(ov["known"]):
            readonly = True
            new = [tr(o) for o in options if o not in ov["known"]]
            gone = [o for o in ov["known"] if o not in options]
            model.drift.append(f"{title}：「{label}」的选项变了（新增 {'、'.join(new) or '无'}；去掉 {'、'.join(gone) or '无'}），"
                               "含义可能已变，只读，请在 ok-ww 里改")
        if kind == "choice" and options and value not in options:
            model.drift.append(f"{title}：「{label}」当前值 {value!r} 不在 ok-ww 的选项里")
        if kind == "multi" and isinstance(value, list) and any(v not in options for v in value):
            model.drift.append(f"{title}：「{label}」里有不在 ok-ww 选项里的值")
        risk = ov.get("risk", "")
        if ("*", key) in DANGEROUS or (spec.id, key) in DANGEROUS:
            readonly, risk = True, _DANGER
        return Field(_k(key), label, kind, choices, help="\n".join(helps), risk=risk, min=lo, max=hi,
                     enabled_by=enabled_by, readonly=readonly)

    @staticmethod
    def _max_from(how: tuple, src: _Source, fallback: int, model: Model, title: str, label: str) -> int:
        """Upper bound from the code's own structure (TacetTask.total_number ...); Qt's bound when unreadable."""
        cls, attr = how[0], how[1]
        try:
            module = f"src.task.{cls}"
            notes = src.notes
            v = src.task(module, cls)["attrs"].get(attr)
            src.notes = notes
            if len(how) > 2 and isinstance(v, list):
                v = v[how[2]]
        except (SettingsError, IndexError):
            v = None
        if isinstance(v, int) and not isinstance(v, bool) and v > 0:
            return v
        model.drift.append(f"{title}：读不到「{label}」的上限（{cls}.{attr}），按 ok-ww 界面的上限")
        return fallback

    # ---- checks
    def _check_sources(self, g: GameConfig, work: str, model: Model) -> None:
        """Same version number but changed sources (hot fix): the options may mean something else."""
        v = self.version(g)
        if v and v.strip().lstrip("vV") != self.verified_version.lstrip("vV"):
            return                                   # open() already says the version changed
        changed = []
        for rel, digest in SOURCE_SHA256.items():
            try:
                if hashlib.sha256(Path(work, rel).read_bytes()).hexdigest() != digest:
                    changed.append(rel)
            except OSError:
                changed.append(rel)
        if changed:
            model.drift.append(f"ok-ww 版本号没变，但这些源码变了（可能是热修，选项含义未复核）：{'、'.join(changed)}")

    @staticmethod
    def _check_chain(work: str, src: _Source, reg: dict, model: Model) -> None:
        module = reg["tasks"].get("DailyTask")
        path = src.module_path(module) if module else None
        if not path:
            return
        used = src.calls_in(path, ("get_task_by_class", "run_task_by_class"))
        if used and used != KNOWN_CHAIN:
            diff = sorted(used ^ KNOWN_CHAIN)
            model.drift.append(f"日常调用的子任务有变化（{'、'.join(diff)}），这一页可能漏了它们的设置")

    @staticmethod
    def _sec(model: Model, sid: str) -> Section | None:
        return next((s for s in model.sections if s.id == sid), None)

    def _cross_check(self, model: Model) -> None:
        """Combinations ok-ww rejects when the daily starts (DailyTask.py:138-156), refreshed after each edit."""
        model.notes[:] = [n for n in model.notes if not n.startswith("⚠ ")]
        daily, nest, farm = (self._sec(model, x) for x in ("daily", "nightmare", "farm4c"))
        extra = get_path(daily.obj, _k("Additional Tasks to Run After Daily Task")) if daily else None
        extra = extra if isinstance(extra, list) else []
        warn = []
        if ADD_4C in extra:
            if farm and get_path(farm.obj, "Teleport to Boss") == "No":
                warn.append("⚠ 附加任务勾了“传送并刷取4C声骸”，但 4C 任务的“传送至Boss”是“否”：日常一开始就会报错。")
            count = get_path(farm.obj, "Repeat Farm Count") if farm else None
            warn.append(f"⚠ 附加任务勾了“传送并刷取4C声骸”：日常要等 4C 刷完（{count} 次）才报完成，总控多半会判超时。")
        if ADD_NEST in extra and nest and get_path(nest.obj, "Which to Farm") == []:
            warn.append("⚠ 附加任务勾了“自动刷所有梦魇巢穴”，但噩梦巢穴任务的“刷什么”一项都没选：日常一开始就会报错。")
        model.notes[:0] = warn

    def _on_set(self, model: Model):
        def on_set(obj: dict, fd: Field, value) -> None:
            set_path(obj, fd.key, value)
            self._cross_check(model)
        return on_set

    @staticmethod
    def _package_note(g: GameConfig, model: Model) -> None:
        basic = next((s for s in model.sections if s.id == "basic"), None)
        ad = adapters.get(g.adapter)
        installed = ad.installed_package(g) if basic and hasattr(ad, "installed_package") else None
        pkg = get_path(basic.obj, "Game Package") if basic else None
        if installed and isinstance(pkg, str) and pkg.lower() != installed:
            model.notes.append(f"基本设置的 Game Package 是 {pkg}，本机安装的资源包是 {installed}（只影响 ok-ww 自己拉起游戏）。")
