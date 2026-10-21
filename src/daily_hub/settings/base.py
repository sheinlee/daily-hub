"""Editing a tool's own daily settings from the hub (Alas-style forms over the tool's config file).

Rules every provider follows:
- read-only while the tool (or its GUI) runs: the tools write their config back while running and on exit;
- before a save the file is copied to backups/<game>/<time>/; a restore backs up the current file first;
- a save is refused when the file changed on disk after it was loaded (the tool or the user edited it);
- only edited values change: a file is written back in its own format, and a file whose round-trip
  (JSON or YAML) is not byte-identical is opened read-only.

Tool updates (the hub must notice and stay safe):
- every provider names the tool version its tables were checked against (verified_version) and the page
  says when the installed version differs;
- hand-written providers keep a snapshot of the keys each kind of section had at that version
  (known_keys); keys that appear later are listed read-only as "new options", keys that disappeared are
  reported, and a field whose value changed type becomes read-only;
- schema-driven providers (MaaFramework interface.json, Alas args.json, ok-script task definitions) read
  the tool's own option definitions at runtime, so new options show up with the tool's labels.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..config import PROJECT_ROOT, GameConfig

BACKUP_DIR = PROJECT_ROOT / "backups"
_BOM = b"\xef\xbb\xbf"


class SettingsError(Exception):
    pass


@dataclass
class Field:
    key: str | tuple             # path inside Section.obj: "a.b" or a tuple of parts (names with dots)
    label: str
    kind: str = "bool"           # bool | int | float | text | textarea | choice | multi | list
    choices: list = field(default_factory=list)   # [(value, label)] for choice / multi
    help: str = ""
    risk: str = ""               # non-empty: the option spends resources (shown in red)
    min: float = 0
    max: float = 999_999
    step: float = 1
    suffix: str = ""
    enabled_by: str = ""         # key of a bool field in the same section that switches this one on
    sep: str = ";"               # kind=list: separator shown in the text box
    empty_label: str = ""        # kind=list: how an empty-string entry is shown (and typed back)
    readonly: bool = False
    default: object = None       # shown when the file has no value yet (tools that store only changes)

    @property
    def root(self) -> str:
        """First path part (the key directly inside Section.obj)."""
        return str(_parts(self.key)[0])

    def accepts(self, value) -> bool:
        """Does the value in the file still have the type this field edits?  (No: the tool changed it.)"""
        if value is None:
            return self.kind in ("text", "textarea", "choice")
        if self.kind == "bool":
            return isinstance(value, bool)
        if self.kind == "int":
            return isinstance(value, int) and not isinstance(value, bool)
        if self.kind == "float":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if self.kind in ("text", "textarea"):
            return isinstance(value, str)
        if self.kind in ("list", "multi"):
            return isinstance(value, list) and all(isinstance(x, (str, int, float)) for x in value)
        if isinstance(value, (dict, list)):
            return False
        types = {type(c[0]) for c in self.choices if c[0] is not None}
        if not types or (value == "" and str in types):                              # choice
            return True
        if isinstance(value, bool):
            return bool in types
        # isinstance: ruamel keeps quoted YAML strings as str subclasses ('7' -> SingleQuotedScalarString)
        return any(isinstance(value, t) for t in types if t is not bool)


@dataclass
class Section:
    id: str
    title: str
    obj: dict                    # the dict inside the loaded data this section edits
    fields: list[Field]
    enable_key: str | None = None   # on/off switch inside obj (None: no switch)
    movable: bool = False
    subtitle: str = ""
    note: str = ""
    group: str = ""              # header the section is listed under (Alas menu groups, tool areas)
    new_keys: list[str] = field(default_factory=list)   # keys the hub has no description for (shown read-only)
    # Custom write (obj, field, value) for tools that store more than the bare value (e.g. MXU {type, value});
    # None = set_path(obj, field.key, value).
    on_set: Callable | None = None


def _parts(key) -> tuple:
    return tuple(key) if isinstance(key, (tuple, list)) else tuple(str(key).split("."))


def get_path(obj: dict, key):
    cur = obj
    for part in _parts(key):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def set_path(obj: dict, key, value) -> None:
    parts = _parts(key)
    cur = obj
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    old = cur.get(parts[-1])
    # ruamel.yaml keeps quoting style in str subclasses (DoubleQuotedScalarString ...): keep the style
    if isinstance(old, str) and type(old) is not str and isinstance(value, str):
        value = type(old)(value)
    cur[parts[-1]] = value


def has_path(obj: dict, key) -> bool:
    cur = obj
    for part in _parts(key):
        if not isinstance(cur, dict) or part not in cur:
            return False
        cur = cur[part]
    return True


class ConfigFile:
    """A config file the hub may write back.  Subclasses set data / raw / exact / reason."""
    path: str
    raw: bytes
    data: object
    exact: bool = False
    reason: str = ""

    def dump(self, data) -> bytes:
        raise NotImplementedError

    @property
    def dirty(self) -> bool:
        return self.dump(self.data) != self.raw

    def changed_on_disk(self) -> bool:
        try:
            return Path(self.path).read_bytes() != self.raw
        except OSError:
            return True

    def save(self, backup_file: Path) -> bool:
        """Write the edited data (the old file is copied to backup_file first).  False: nothing changed."""
        if not self.exact:
            raise SettingsError(f"{os.path.basename(self.path)} 不能原样写回（{self.reason}），只能在原工具里改")
        new = self.dump(self.data)
        if new == self.raw:
            return False
        if self.changed_on_disk():
            raise SettingsError(f"{os.path.basename(self.path)} 在载入后被改动过（可能是工具自己写的），"
                                "请先点“从脚本同步”，再重新修改")
        backup_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.path, backup_file)
        tmp = self.path + ".hub-tmp"
        with open(tmp, "wb") as f:
            f.write(new)
        os.replace(tmp, self.path)
        if Path(self.path).read_bytes() != new:
            raise SettingsError(f"写入后校验失败：{self.path}（备份在 {backup_file}）")
        self.raw = new
        return True


class JsonFile(ConfigFile):
    """A JSON config file loaded so that it can be written back byte-identically except for edits."""

    def __init__(self, path: str):
        self.path = path
        self.raw = Path(path).read_bytes()
        self.bom = self.raw.startswith(_BOM)
        text = self.raw.decode("utf-8-sig")
        self.newline = "\r\n" if "\r\n" in text else "\n"
        self.trailing = text.endswith("\n")
        self.data = json.loads(text)
        self.indent = 2
        for line in text.splitlines()[1:2]:
            self.indent = (len(line) - len(line.lstrip(" "))) or 2
        self.ensure_ascii = False
        self.exact = False
        for ea in (False, True):
            self.ensure_ascii = ea
            if self.dump(self.data) == self.raw:
                self.exact = True
                break
        self.reason = "" if self.exact else "JSON 格式与工具写的不一致"

    def dump(self, data) -> bytes:
        out = json.dumps(data, indent=self.indent, ensure_ascii=self.ensure_ascii).replace("\n", self.newline)
        if self.trailing:
            out += self.newline
        return (_BOM if self.bom else b"") + out.encode("utf-8")


# YAML 1.1 booleans: PyYAML-written tools read these as true/false, ruamel (YAML 1.2) as strings.
_YAML11_BOOL = re.compile(r":[ \t]+(yes|no|on|off|y|n)[ \t]*(#.*)?$", re.I | re.M)


class YamlFile(ConfigFile):
    """A YAML config file edited through ruamel.yaml round-trip (comments, order and quoting kept).

    Writable only when some ruamel layout reproduces the file byte for byte."""

    LAYOUTS = [(2, 2, 0), (2, 4, 2), (4, 4, 0), (2, 4, 0)]   # (mapping, sequence, offset)

    def __init__(self, path: str, layouts=None):
        from ruamel.yaml import YAML
        self.path = path
        self.raw = Path(path).read_bytes()
        self.bom = self.raw.startswith(_BOM)
        text = self.raw.decode("utf-8-sig")
        self.newline = "\r\n" if "\r\n" in text else "\n"
        self.exact = False
        self.reason = ""
        self.data = None
        self._yaml = None
        for m, s, o in (layouts or self.LAYOUTS):
            y = YAML(typ="rt")
            y.preserve_quotes = True
            y.width = 4096
            y.indent(mapping=m, sequence=s, offset=o)
            data = y.load(text.replace("\r\n", "\n"))
            if self.data is None:
                self.data, self._yaml = data, y
            out = self._dump_with(y, data)
            if out == self.raw:
                self.data, self._yaml, self.exact = data, y, True
                break
        if not self.exact:
            self.reason = "YAML 排版与工具写的不一致"
        elif _YAML11_BOOL.search(text):
            self.exact, self.reason = False, "文件里有 yes/no/on/off 写法的布尔值"

    def _dump_with(self, y, data) -> bytes:
        buf = io.StringIO()
        y.dump(data, buf)
        out = buf.getvalue().replace("\n", self.newline)
        return (_BOM if self.bom else b"") + out.encode("utf-8")

    def dump(self, data) -> bytes:
        return self._dump_with(self._yaml, data)


@dataclass
class Model:
    """What an editor page shows for one game and one tool configuration."""
    files: list[ConfigFile]
    sections: list[Section]
    order: list | None = None    # list inside the data holding the movable sections' objs, in run order
    notes: list[str] = field(default_factory=list)
    version: str | None = None   # installed tool version
    verified: str = ""           # version the hub's tables were checked against
    drift: list[str] = field(default_factory=list)   # what changed since that version (new / missing keys)

    @property
    def dirty(self) -> bool:
        return any(f.dirty for f in self.files)

    @property
    def exact(self) -> bool:
        return all(f.exact for f in self.files)

    @property
    def version_changed(self) -> bool:
        return bool(self.version and self.verified and _norm_version(self.version) != _norm_version(self.verified))

    def move(self, index: int, delta: int) -> bool:
        """Move a movable section (and its object in the tool's task list) up or down."""
        j = index + delta
        if self.order is None or not (0 <= index < len(self.sections)) or not (0 <= j < len(self.sections)):
            return False
        a, b = self.sections[index], self.sections[j]
        if not (a.movable and b.movable):
            return False
        ia = next(i for i, o in enumerate(self.order) if o is a.obj)
        ib = next(i for i, o in enumerate(self.order) if o is b.obj)
        self.order[ia], self.order[ib] = self.order[ib], self.order[ia]
        self.sections[index], self.sections[j] = b, a
        return True


def _norm_version(v: str) -> str:
    return str(v).strip().lstrip("vV")


class SettingsProvider:
    """One per tool.  Subclasses describe where the daily settings live and which fields to show."""
    key = ""                     # adapter key
    verified_version = ""        # tool version the field tables / schema handling were checked against
    # hand-written providers: {section kind: keys that section had at verified_version}
    known_keys: dict[str, frozenset] = {}

    def config_names(self, g: GameConfig) -> list[str]:
        return []

    def default_config(self, g: GameConfig) -> str:
        return ""

    def load(self, g: GameConfig, config_name: str) -> Model:
        raise NotImplementedError

    def open(self, g: GameConfig, config_name: str) -> Model:
        """load() plus version and drift information (what the page calls)."""
        model = self.load(g, config_name)
        model.version = self.version(g)
        model.verified = self.verified_version
        if model.version_changed:
            model.notes.insert(0, f"工具已是 {model.version}，总控的设置表按 {model.verified} 核对过；"
                                  "下面列出了检查到的变化。看起来不对时，先在原工具里改，并告诉 Claude 更新。")
        for s in model.sections:
            for fd in s.fields:
                if has_path(s.obj, fd.key) and not fd.accepts(get_path(s.obj, fd.key)):
                    fd.readonly = True
                    model.drift.append(f"{s.title}：「{fd.label}」的取值类型变了，暂时只读")
        return model

    def version(self, g: GameConfig) -> str | None:
        from .. import adapters
        return adapters.get(g.adapter).version(g)

    def audit(self, model: Model, section: Section, kind: str, table: list[Field],
              ignore: frozenset = frozenset()) -> None:
        """Compare a section's keys with the snapshot taken at verified_version (hand-written providers).

        table: the full field table for this kind (before dropping fields whose key is absent)."""
        known = self.known_keys.get(kind)
        if known is None or not isinstance(section.obj, dict):
            return
        present = {str(k) for k in section.obj.keys()}
        new = sorted(present - known - ignore)
        gone = sorted({fd.root for fd in table} - present)
        if new:
            section.new_keys = new
            model.drift.append(f"{section.title}：出现了新选项 {'、'.join(new)}（总控还没有说明，只读显示）")
        if gone:
            model.drift.append(f"{section.title}：这些选项已不存在 {'、'.join(gone)}")

    def busy(self, g: GameConfig) -> list[str]:
        """Running instances that make the files read-only (empty: editable)."""
        return []

    def file_paths(self, g: GameConfig) -> list[str]:
        return []

    # ---- save / backups (shared) ----
    @staticmethod
    def _new_stamp(g: GameConfig, suffix: str = "") -> Path:
        base = BACKUP_DIR / g.key / (time.strftime("%Y%m%d-%H%M%S") + suffix)
        p, n = base, 2
        while p.exists():               # two saves within one second must not share a folder
            p, n = base.with_name(f"{base.name}-{n}"), n + 1
        return p

    def save(self, g: GameConfig, model: Model) -> Path | None:
        """Write every dirty file.  Returns the backup folder, or None when nothing changed."""
        running = self.busy(g)
        if running:
            raise SettingsError("工具正在运行，现在保存会被它覆盖：" + "；".join(running))
        stamp = self._new_stamp(g)
        for f in model.files:
            if f.dirty and f.changed_on_disk():
                raise SettingsError(f"{os.path.basename(f.path)} 在载入后被改动过，请先点“从脚本同步”")
        wrote = False
        for f in model.files:
            wrote = f.save(stamp / _rel(g, f.path)) or wrote
        return stamp if wrote else None

    @staticmethod
    def backups(g: GameConfig) -> list[Path]:
        d = BACKUP_DIR / g.key
        if not d.is_dir():
            return []
        return sorted((p for p in d.iterdir() if p.is_dir()), reverse=True)

    def restore(self, g: GameConfig, backup: Path) -> Path:
        """Copy a backup over the tool's files (the current files are backed up first)."""
        running = self.busy(g)
        if running:
            raise SettingsError("工具正在运行，现在还原会被它覆盖：" + "；".join(running))
        targets = {_rel(g, p): p for p in self.file_paths(g)}
        files = [(p.relative_to(backup).as_posix(), p) for p in backup.rglob("*") if p.is_file()]
        files = [(rel, p) for rel, p in files if rel in targets]
        if not files:
            raise SettingsError(f"{backup} 里没有可还原的文件")
        stamp = self._new_stamp(g, "-还原前")
        for rel, p in files:
            dest = targets[rel]
            if os.path.isfile(dest):
                (stamp / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dest, stamp / rel)
            tmp = dest + ".hub-tmp"
            shutil.copyfile(p, tmp)
            os.replace(tmp, dest)
        return stamp


def _rel(g: GameConfig, path: str) -> str:
    """Backup layout: the file's path relative to the tool folder (two files may share a name)."""
    try:
        rel = os.path.relpath(path, g.tool_dir)
    except ValueError:              # another drive
        rel = os.path.basename(path)
    if rel.startswith(".."):
        rel = os.path.basename(path)
    return rel.replace("\\", "/")
