"""日常设置 tab: edit a tool's own daily settings, Alas-style (game → task list → options).

Both directions: "从脚本同步" reads the tool's files (also automatically when the page opens, when the
tool rewrites a file, and when the tool stops running); "保存到脚本" writes the edits back.

Only games whose tool has a provider in settings/ are editable; the rules (read-only while the tool
runs, backup before save, refuse when the file changed on disk, version / drift reports) live in
settings/base.py.
"""
from __future__ import annotations

import html
import os

from PySide6.QtCore import QFileSystemWatcher, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSpinBox,
    QToolButton, QVBoxLayout, QWidget,
)

from . import adapters, settings
from .config import HubConfig
from .settings.base import Field, Model, Section, SettingsError, get_path, set_path

RISK_COLOR = "#cf222e"
DRIFT_COLOR = "#9a6700"
HELP_STYLE = "color:#6e7781;"
_LIST_SEPS = (";", "；", ",", "，")
_INT_LIMIT = 2_147_483_647


class DailySettingsPage(QWidget):
    def __init__(self, cfg: HubConfig, parent=None, status=None):
        super().__init__(parent)
        self.cfg = cfg
        self.status = status or (lambda text, ms=4000: None)
        self.g = None
        self.prov = None
        self.model: Model | None = None
        self.running: list[str] = []
        self.error = ""
        self._config_name = ""
        self._filling = False
        self.external_changed = False      # the tool changed its files while this page had unsaved edits
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_fs_event)
        self._watcher.directoryChanged.connect(self._on_fs_event)
        self._fs_timer = QTimer(self)       # tools write in bursts (temp file + rename): wait for quiet
        self._fs_timer.setSingleShot(True)
        self._fs_timer.setInterval(800)
        self._fs_timer.timeout.connect(self._check_external)
        self._busy_timer = QTimer(self)     # tool started / stopped -> read-only / editable (while visible)
        self._busy_timer.setInterval(3000)
        self._busy_timer.timeout.connect(self._poll_busy)
        self._widgets: dict[str, tuple[Field, QWidget]] = {}

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 0)
        root.setSpacing(12)
        self.lst_games = QListWidget()
        self.lst_games.setMaximumWidth(220)
        self.lst_games.currentRowChanged.connect(self._on_game_row)
        root.addWidget(self.lst_games)

        right = QVBoxLayout()
        head = QHBoxLayout()
        self.lbl_title = QLabel()
        self.lbl_title.setObjectName("pageTitle")
        head.addWidget(self.lbl_title)
        self.lbl_cfg = QLabel("配置")
        self.lbl_cfg.setObjectName("muted")
        self.cmb_config = QComboBox()
        self.cmb_config.setMinimumWidth(160)
        self.cmb_config.activated.connect(self._on_config_chosen)
        head.addWidget(self.lbl_cfg)
        head.addWidget(self.cmb_config)
        head.addStretch(1)
        self.btn_reload = QPushButton("从脚本同步")
        self.btn_reload.setToolTip("脚本 → 总控：把脚本当前的设置读进来，这里还没保存的修改会被放弃；不写任何文件。\n"
                                   "打开页面时、脚本改了它的配置文件时、脚本关掉时都会自动同步。")
        self.btn_reload.clicked.connect(self.reload)
        self.btn_save = QPushButton("保存到脚本")
        self.btn_save.setObjectName("accent")
        self.btn_save.setToolTip("总控 → 脚本：把这里的修改写进脚本的配置文件（写之前自动备份），下次启动脚本时生效。")
        self.btn_save.clicked.connect(self.save)
        self.btn_restore = QToolButton()
        self.btn_restore.setText("还原备份")
        self.btn_restore.setPopupMode(QToolButton.InstantPopup)
        self.menu_restore = QMenu(self.btn_restore)
        self.menu_restore.aboutToShow.connect(self._fill_restore_menu)
        self.btn_restore.setMenu(self.menu_restore)
        self.btn_open = QToolButton()
        self.btn_open.setText("打开")
        self.btn_open.setPopupMode(QToolButton.InstantPopup)
        self.menu_open = QMenu(self.btn_open)
        self.menu_open.aboutToShow.connect(self._fill_open_menu)
        self.btn_open.setMenu(self.menu_open)
        for w in (self.btn_reload, self.btn_restore, self.btn_open, self.btn_save):
            head.addWidget(w)
        right.addLayout(head)

        self.lbl_state = QLabel()
        self.lbl_state.setObjectName("stateBox")
        self.lbl_state.setWordWrap(True)
        self.lbl_state.setTextFormat(Qt.RichText)
        right.addWidget(self.lbl_state)

        body = QHBoxLayout()
        body.setSpacing(12)
        col = QVBoxLayout()
        self.txt_filter = QLineEdit()
        self.txt_filter.setPlaceholderText("筛选任务")
        self.txt_filter.setClearButtonEnabled(True)
        self.txt_filter.textChanged.connect(self._apply_filter)
        col.addWidget(self.txt_filter)
        self.lst_tasks = QListWidget()
        self.lst_tasks.setMaximumWidth(280)
        self.lst_tasks.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.lst_tasks.setTextElideMode(Qt.ElideRight)
        self.lst_tasks.currentRowChanged.connect(self._on_task_row)
        self.lst_tasks.itemChanged.connect(self._on_task_check)
        col.addWidget(self.lst_tasks, 1)
        mv = QHBoxLayout()
        self.btn_up = QPushButton("上移")
        self.btn_down = QPushButton("下移")
        self.btn_up.clicked.connect(lambda: self._move(-1))
        self.btn_down.clicked.connect(lambda: self._move(1))
        mv.addWidget(self.btn_up)
        mv.addWidget(self.btn_down)
        col.addLayout(mv)
        body.addLayout(col)
        self.area = QScrollArea()
        self.area.setObjectName("formArea")
        self.area.setWidgetResizable(True)
        body.addWidget(self.area, 1)
        right.addLayout(body, 1)
        root.addLayout(right, 1)

        self.refresh_games()

    # ------------------------------------------------------------------ games
    def refresh_games(self):
        cur = self.g.key if self.g else None
        self._filling = True
        self.lst_games.clear()
        for g in self.cfg.ordered():
            ad = adapters.get(g.adapter)
            ok = settings.get(g.adapter) is not None
            it = QListWidgetItem(f"{g.name} · {ad.tool_name}" + ("" if ok else "（未接入）"))
            it.setData(Qt.UserRole, g.key)
            if not ok:
                it.setForeground(QColor("#8c959f"))
            self.lst_games.addItem(it)
        self._filling = False
        keys = self._game_keys()
        first = next((i for i, k in enumerate(keys) if settings.get(self.cfg.games[k].adapter)), 0)
        self.lst_games.setCurrentRow(keys.index(cur) if cur in keys else first)

    def _game_keys(self) -> list[str]:
        return [self.lst_games.item(i).data(Qt.UserRole) for i in range(self.lst_games.count())]

    def _on_game_row(self, row: int):
        if self._filling or row < 0:
            return
        key = self.lst_games.item(row).data(Qt.UserRole)
        if self.g and key == self.g.key:
            return
        if not self.confirm_discard():
            self._filling = True
            self.lst_games.setCurrentRow(self._game_keys().index(self.g.key))
            self._filling = False
            return
        self.g = self.cfg.games[key]
        self.prov = settings.get(self.g.adapter)
        self.lbl_title.setText(f"{self.g.name} · {adapters.get(self.g.adapter).tool_name}")
        self.cmb_config.clear()
        self.txt_filter.clear()
        if self.prov:
            names = self.prov.config_names(self.g)
            self.cmb_config.addItems(names)
            d = self.prov.default_config(self.g)
            if d in names:
                self.cmb_config.setCurrentIndex(names.index(d))
        self.load()

    def _on_config_chosen(self, _i: int):
        new = self.cmb_config.currentText()
        if new == self._config_name:
            return
        if not self.confirm_discard():
            self.cmb_config.setCurrentText(self._config_name)
            return
        self.load()

    # ------------------------------------------------------------------ load / save
    def load(self):
        self.model = None
        self.running = []
        self.error = ""
        self.external_changed = False
        self._fill_tasks()
        has = self.prov is not None
        for w in (self.btn_reload, self.btn_save, self.btn_restore, self.btn_up, self.btn_down,
                  self.lst_tasks, self.txt_filter):
            w.setVisible(has)
        self.lbl_cfg.setVisible(has and self.cmb_config.count() > 0)
        self.cmb_config.setVisible(has and self.cmb_config.count() > 0)
        if not has:
            self._set_form_message("这款还没接入，请在原工具里修改日常设置。\n右上角“打开”可以直接打开工具目录和配置。")
            self.lbl_state.setText("")
            return
        name = self._config_name = self.cmb_config.currentText()
        if not self.g.tool_dir.strip():
            self._set_form_message("还没设置这款的工具目录：请先在“总控设置”里填写并保存，再回到这里。")
            self.lbl_state.setText("")
            return
        try:
            self.running = self.prov.busy(self.g)
            self.model = self.prov.open(self.g, name)
        except (SettingsError, OSError, ValueError, KeyError, TypeError) as e:
            self.model = None
            self.error = f"读取失败：{e}"
            self._set_form_message(self.error + "\n\n请在原工具里修改；如果工具刚更新过，告诉 Claude 更新适配。")
            self._update_state()
            self._watch()
            return
        self._fill_tasks()
        self._update_state()
        self._watch()

    def reload(self):
        if self.confirm_discard():
            sel = self._section_index()
            self.load()
            self._select_section(sel)

    # ------------------------------------------------------------------ automatic script -> hub sync
    def _watched_files(self) -> list[str]:
        if self.model:
            return [f.path for f in self.model.files]
        if self.prov and self.g:
            return [p for p in self.prov.file_paths(self.g) if os.path.isfile(p)]
        return []

    def _watch(self):
        """Watch the tool's files and their folders (a replace-by-rename drops the file from the watcher)."""
        old = self._watcher.files() + self._watcher.directories()
        if old:
            self._watcher.removePaths(old)
        files = self._watched_files()
        dirs = sorted({os.path.dirname(p) for p in files if os.path.isdir(os.path.dirname(p))})
        paths = [p for p in files if os.path.isfile(p)] + dirs
        if paths:
            self._watcher.addPaths(paths)

    def _on_fs_event(self, _path: str):
        self._fs_timer.start()

    def _check_external(self):
        """A watched file changed: pull it in, unless the user has unsaved edits here."""
        if not self.prov or not self.g:
            return
        if self.model is None:
            if self.error:                 # e.g. the tool was half-way through writing: try again
                self.load()
            return
        changed = [f for f in self.model.files if f.changed_on_disk()]
        self._watch()
        if not changed:
            return                         # our own save, or a file in the same folder
        names = "、".join(os.path.basename(f.path) for f in changed)
        if self.dirty:
            self.external_changed = True
            self._update_state()
            self.status(f"{self.g.name}的脚本改了 {names}；这里有未保存的修改，没有自动同步", 8000)
            return
        sel = self._section_index()
        self.load()
        self._select_section(sel)
        self.status(f"脚本改了 {names}，已自动同步到这里", 6000)

    def _poll_busy(self):
        if not (self.prov and self.g and self.model is not None):
            return
        running = self.prov.busy(self.g)
        if bool(running) == bool(self.running):
            if running != self.running:
                self.running = running
                self._update_state()
            return
        if running or self.dirty:          # tool started, or it stopped while there are unsaved edits
            self.running = running
            self._fill_tasks()
            self._update_state()
            return
        sel = self._section_index()        # tool stopped: read what it wrote on exit, editable again
        self.load()
        self._select_section(sel)
        self.status(f"{adapters.get(self.g.adapter).tool_name} 已关闭，已同步它的最新设置，可以修改了", 6000)

    def showEvent(self, e):
        super().showEvent(e)
        self._busy_timer.start()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._busy_timer.stop()

    @property
    def readonly(self) -> bool:
        return bool(self.running) or self.model is None or not self.model.exact

    @property
    def dirty(self) -> bool:
        return bool(self.model and self.model.dirty)

    def confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        r = QMessageBox.question(self, "日常设置", f"{self.g.name}的设置有未保存的修改，要保存吗？",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.save()
        return r == QMessageBox.Discard

    def save(self) -> bool:
        if not (self.model and self.prov):
            return False
        try:
            backup = self.prov.save(self.g, self.model)
        except (SettingsError, OSError) as e:
            QMessageBox.warning(self, "保存失败", str(e))
            self.running = self.prov.busy(self.g)
            self._update_state()
            return False
        sel = self._section_index()
        self.load()
        self._select_section(sel)
        self.status("没有改动" if backup is None else f"已保存；改之前的文件备份在 {backup}", 8000)
        return True

    # ------------------------------------------------------------------ tasks
    def _fill_tasks(self):
        self._filling = True
        sel = self._section_index()
        self.lst_tasks.clear()
        sections = self.model.sections if self.model else []
        group = None
        for i, s in enumerate(sections):
            if s.group and s.group != group:
                group = s.group
                h = QListWidgetItem(s.group)
                h.setToolTip(s.group)
                h.setFlags(Qt.NoItemFlags)
                f = h.font()
                f.setBold(True)
                h.setFont(f)
                h.setForeground(QColor("#57606a"))
                self.lst_tasks.addItem(h)
            text = s.title if not s.subtitle or s.subtitle == s.title else f"{s.title}  ·  {s.subtitle}"
            if s.new_keys:
                text += "  ＊"
            it = QListWidgetItem(("    " if s.group else "") + text)
            it.setData(Qt.UserRole, i)
            if s.enable_key:
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)   # read-only: _on_task_check reverts clicks
                it.setCheckState(Qt.Checked if get_path(s.obj, s.enable_key) else Qt.Unchecked)
            elif not s.movable:
                f = it.font()
                f.setBold(True)
                it.setFont(f)
            self.lst_tasks.addItem(it)
        self._filling = False
        self._apply_filter(self.txt_filter.text())
        if sections:
            self._select_section(sel if sel is not None and sel < len(sections) else 0)
        else:
            self._update_move_buttons()

    def _section_index(self) -> int | None:
        it = self.lst_tasks.currentItem()
        return it.data(Qt.UserRole) if it is not None else None

    def _select_section(self, index: int | None):
        for r in range(self.lst_tasks.count()):
            if self.lst_tasks.item(r).data(Qt.UserRole) == index:
                self.lst_tasks.setCurrentRow(r)
                self._on_task_row(r)
                return

    def _section(self) -> Section | None:
        i = self._section_index()
        return self.model.sections[i] if self.model is not None and i is not None else None

    def _apply_filter(self, text: str):
        text = text.strip().lower()
        header = None
        header_visible = False
        for r in range(self.lst_tasks.count()):
            it = self.lst_tasks.item(r)
            idx = it.data(Qt.UserRole)
            if idx is None:                          # group header: shown when one of its tasks is
                if header is not None:
                    header.setHidden(not header_visible)
                header, header_visible = it, False
                continue
            s = self.model.sections[idx] if self.model else None
            hay = f"{s.title} {s.subtitle} {s.group}".lower() if s else ""
            hidden = bool(text) and text not in hay
            it.setHidden(hidden)
            header_visible = header_visible or not hidden
        if header is not None:
            header.setHidden(not header_visible)

    def _on_task_check(self, item: QListWidgetItem):
        idx = item.data(Qt.UserRole)
        if self._filling or not self.model or idx is None:
            return
        s = self.model.sections[idx]
        if not s.enable_key:
            return
        if self.readonly:
            self._filling = True
            item.setCheckState(Qt.Checked if get_path(s.obj, s.enable_key) else Qt.Unchecked)
            self._filling = False
            return
        set_path(s.obj, s.enable_key, item.checkState() == Qt.Checked)
        if self._section() is s:          # fields switched by the task's own enable key
            self._apply_enabled(s)
        self._update_state()

    def _move(self, delta: int):
        i = self._section_index()
        if self.readonly or not self.model or i is None or not self.model.move(i, delta):
            return
        self._fill_tasks()
        self._select_section(i + delta)
        self._update_state()

    def _update_move_buttons(self):
        s = self._section()
        ok = bool(s and s.movable and not self.readonly)
        self.btn_up.setEnabled(ok)
        self.btn_down.setEnabled(ok)

    def _on_task_row(self, _row: int):
        if self._filling:
            return
        self._update_move_buttons()
        s = self._section()
        if s is None:
            self._set_form_message("")
            return
        self._build_form(s)

    # ------------------------------------------------------------------ form
    def _set_form_message(self, text: str):
        lab = QLabel(text)
        lab.setWordWrap(True)
        lab.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        lab.setContentsMargins(16, 16, 16, 16)
        lab.setObjectName("formPage")
        self.area.setWidget(lab)
        self._widgets = {}

    @staticmethod
    def _help_label(text: str) -> QLabel:
        h = QLabel(text)
        h.setWordWrap(True)
        h.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        h.setStyleSheet(HELP_STYLE)
        f = h.font()
        f.setPointSize(max(f.pointSize() - 1, 8))
        h.setFont(f)
        return h

    def _build_form(self, s: Section):
        page = QWidget()
        page.setObjectName("formPage")
        form = QFormLayout(page)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        form.setContentsMargins(18, 16, 18, 16)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(8)
        if s.note:
            form.addRow(self._help_label(s.note))
        if not s.fields and not s.note and not s.new_keys:
            form.addRow(QLabel("这一项没有可改的选项。"))
        self._widgets = {}
        for fd in s.fields:
            w = self._make_widget(s, fd)
            label = QLabel(("⚠ " if fd.risk else "") + fd.label + ("（只读）" if fd.readonly else ""))
            if fd.risk:
                label.setStyleSheet(f"color:{RISK_COLOR};")
            tip = "\n".join(x for x in (fd.risk and f"⚠ {fd.risk}", fd.help) if x)
            label.setToolTip(tip)
            w.setToolTip(tip)
            form.addRow(label, w)
            if tip:
                form.addRow("", self._help_label(tip))
            self._widgets[fd.key] = (fd, w)
        if s.new_keys:
            lines = []
            for k in s.new_keys:
                v = s.obj.get(k)
                shown = v if isinstance(v, (str, int, float, bool)) or v is None else type(v).__name__
                lines.append(f"{k} = {shown!r}"[:160])
            box = QLabel("工具更新后新出现的选项（总控还没有说明，只读；请在原工具里改）：\n" + "\n".join(lines))
            box.setWordWrap(True)
            box.setStyleSheet(f"color:{DRIFT_COLOR};")
            box.setTextInteractionFlags(Qt.TextSelectableByMouse)
            form.addRow(box)
        self._apply_enabled(s)
        self.area.setWidget(page)

    def _make_widget(self, s: Section, fd: Field) -> QWidget:
        v = get_path(s.obj, fd.key)
        if v is None and fd.default is not None:
            v = fd.default
        if fd.kind == "bool" and fd.accepts(v):
            w = QCheckBox()
            w.setChecked(bool(v))
            w.toggled.connect(lambda on, s=s, fd=fd: self._set(s, fd, bool(on)))
        elif fd.kind == "int" and fd.accepts(v):
            w = QSpinBox()
            lo, hi = int(max(fd.min, -_INT_LIMIT)), int(min(fd.max, _INT_LIMIT))
            w.setRange(min(lo, v), max(hi, v))
            w.setSingleStep(int(fd.step) or 1)
            w.setSuffix(fd.suffix)
            w.setValue(v)
            w.valueChanged.connect(lambda x, s=s, fd=fd: self._set(s, fd, int(x)))
        elif fd.kind == "float" and fd.accepts(v):
            w = QDoubleSpinBox()
            w.setDecimals(3)
            w.setRange(min(fd.min, v), max(fd.max, v))
            w.setSingleStep(fd.step or 0.1)
            w.setSuffix(fd.suffix)
            w.setValue(float(v))
            was_int = isinstance(v, int)
            w.valueChanged.connect(lambda x, s=s, fd=fd, was_int=was_int:
                                   self._set(s, fd, int(x) if was_int and float(x).is_integer() else float(x)))
        elif fd.kind == "choice" and fd.accepts(v):
            w = QComboBox()
            values = [c[0] for c in fd.choices]
            for val, text in fd.choices:
                w.addItem(str(text), val)
            if v not in values:
                w.addItem(f"{v}（当前值，这里不提供修改）", v)
            w.setCurrentIndex(values.index(v) if v in values else w.count() - 1)
            w.currentIndexChanged.connect(lambda _i, w=w, s=s, fd=fd: self._set(s, fd, w.currentData()))
        elif fd.kind == "multi" and fd.accepts(v):
            w = QWidget()
            lay = QVBoxLayout(w)
            lay.setContentsMargins(0, 0, 0, 0)
            current = list(v)
            known = [c[0] for c in fd.choices]
            boxes = []
            for val, text in fd.choices:
                cb = QCheckBox(str(text))
                cb.setChecked(val in current)
                lay.addWidget(cb)
                boxes.append((val, cb))
            extra = [x for x in current if x not in known]     # values the hub has no label for: kept as they are
            if extra:
                lay.addWidget(self._help_label("另有总控不认识的已选项（保留不动）：" + "、".join(map(str, extra))))

            def changed(_on, s=s, fd=fd, boxes=boxes, extra=extra):
                self._set(s, fd, [val for val, cb in boxes if cb.isChecked()] + extra)
            for _val, cb in boxes:
                cb.toggled.connect(changed)
        elif fd.kind == "textarea" and fd.accepts(v):
            w = QPlainTextEdit()
            w.setPlainText("" if v is None else str(v))
            w.setFixedHeight(110)
            w.textChanged.connect(lambda w=w, s=s, fd=fd: self._set(s, fd, w.toPlainText()))
        elif fd.kind == "list" and fd.accepts(v):
            w = QLineEdit()
            w.setText(f"{fd.sep} ".join(fd.empty_label if (x == "" and fd.empty_label) else str(x) for x in v))
            w.textEdited.connect(lambda text, s=s, fd=fd: self._set(s, fd, _parse_list(text, fd)))
        elif fd.kind == "text" and fd.accepts(v):
            w = QLineEdit()
            w.setText("" if v is None else str(v))
            w.textEdited.connect(lambda text, s=s, fd=fd: self._set(s, fd, text))
        else:                          # the value no longer has the type this field edits: show it, read-only
            w = QLineEdit()
            w.setText(repr(v) if isinstance(v, (dict, list)) else ("" if v is None else str(v)))
            fd.readonly = True
        return w

    def _set(self, s: Section, fd: Field, value):
        if self._filling or self.readonly or fd.readonly:
            return
        if s.on_set:
            s.on_set(s.obj, fd, value)
        else:
            set_path(s.obj, fd.key, value)
        self._apply_enabled(s)
        self._update_state()

    def _apply_enabled(self, s: Section):
        for fd, w in self._widgets.values():
            on = not self.readonly and not fd.readonly
            if fd.enabled_by:
                dep = next((d for d, _w in self._widgets.values() if d.key == fd.enabled_by), None)
                val = get_path(s.obj, fd.enabled_by)
                on = on and bool(dep.default if val is None and dep is not None else val)
            w.setEnabled(on)

    # ------------------------------------------------------------------ state line
    def _update_state(self):
        parts = []
        if self.error:
            parts.append(f'<span style="color:{RISK_COLOR}">{_esc(self.error)}</span>')
        if self.running:
            parts.append(f'<span style="color:{RISK_COLOR}">工具正在运行，只能查看：' + _esc("；".join(self.running))
                         + "。关掉它之后这里会自动同步并恢复可编辑。</span>")
        if self.external_changed:
            parts.append(f'<span style="color:{DRIFT_COLOR}"><b>脚本那边改过设置</b>：这里的修改还没保存，所以没有自动同步。'
                         "点“从脚本同步”会放弃这里的修改并读入脚本的设置；直接“保存到脚本”会被拒绝。</span>")
        if self.model:
            if self.model.version:
                parts.append(f'<span style="{HELP_STYLE}">版本 {_esc(self.model.version)}'
                             + (f"（设置表按 {_esc(self.model.verified)} 核对）" if self.model.verified else "")
                             + "</span>")
            parts += [_esc(n) for n in self.model.notes]
            if self.model.drift:
                parts.append(f'<span style="color:{DRIFT_COLOR}">'
                             + "<br>".join(_esc(d) for d in self.model.drift[:12])
                             + ("<br>…" if len(self.model.drift) > 12 else "") + "</span>")
            for f in self.model.files:
                if not f.exact:
                    parts.append(f'<span style="color:{RISK_COLOR}">{_esc(os.path.basename(f.path))} 只能查看：'
                                 f"{_esc(f.reason)}</span>")
            if self.model.dirty:
                parts.append("<b>有未保存的修改</b>")
            paths = "、".join(f.path for f in self.model.files)
            parts.append(f'<span style="{HELP_STYLE}">文件：{_esc(paths)}</span>')
        self.lbl_state.setText("<br>".join(parts))
        self.btn_save.setEnabled(bool(self.model) and self.dirty and not self.readonly)

    # ------------------------------------------------------------------ menus
    def _fill_restore_menu(self):
        self.menu_restore.clear()
        if not (self.prov and self.g):
            return
        backups = self.prov.backups(self.g)
        if not backups:
            self.menu_restore.addAction("还没有备份").setEnabled(False)
            return
        for b in backups[:15]:
            self.menu_restore.addAction(b.name, lambda b=b: self._restore(b))
        self.menu_restore.addSeparator()
        self.menu_restore.addAction("打开备份目录", lambda: _open(str(backups[0].parent)))

    def _restore(self, backup):
        if QMessageBox.question(self, "还原备份",
                                f"用 {backup.name} 的备份覆盖{self.g.name}现在的配置？\n（现在的文件会先另存一份备份）") \
                != QMessageBox.Yes:
            return
        try:
            pre = self.prov.restore(self.g, backup)
        except (SettingsError, OSError) as e:
            QMessageBox.warning(self, "还原失败", str(e))
            return
        self.load()
        self.status(f"已还原 {backup.name}；还原前的文件备份在 {pre}", 8000)

    def _fill_open_menu(self):
        self.menu_open.clear()
        if not self.g:
            return
        for label, path in adapters.get(self.g.adapter).open_targets(self.g).items():
            self.menu_open.addAction(label, lambda p=path: _open(p))


def _esc(text: str) -> str:
    return html.escape(str(text))


def _parse_list(text: str, fd: Field) -> list:
    for sep in _LIST_SEPS[1:]:
        text = text.replace(sep, _LIST_SEPS[0])
    items = [x.strip() for x in text.split(_LIST_SEPS[0]) if x.strip()]
    items = ["" if (fd.empty_label and x == fd.empty_label) else x for x in items]
    return items or ([""] if fd.empty_label else [])


def _open(path: str):
    if os.path.exists(path):
        os.startfile(path)
    else:
        QMessageBox.warning(None, "打开", f"路径不存在：{path}")
