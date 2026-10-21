"""Single-window PySide6 UI."""
from __future__ import annotations

import html
import os
import sys
import threading
import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QSizePolicy, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget, QToolButton, QVBoxLayout,
    QWidget,
)

from . import adapters, config, games, instance, power, theme, updates, winapi
from .config import LAUNCH_MODE_NAMES, LAUNCH_VIA_LAUNCHER, GameConfig
from .runner import DONE_S, ERROR, PENDING, RUNNING, STOPPED, Events, Runner
from .settings_view import DailySettingsPage

STATUS_COLOR = {
    PENDING: "#808080", RUNNING: "#1f6feb", DONE_S: "#1a7f37", ERROR: "#cf222e", STOPPED: "#9a6700",
}
# 操作 sits right after the game name so 独立运行 is not mistaken for another row's button
COL_ON, COL_GAME, COL_ACT, COL_TOOL, COL_LAUNCH, COL_STATUS, COL_MSG = range(7)
AUTO_START_DELAY_S = 60     # countdown before a scheduled run starts (time to cancel it)
AFTER_ACTION_DELAY_S = 60   # countdown before sleep / hibernate / shutdown
UNLOCK_WAIT_S = 600         # a scheduled run waits this long for an unlocked desktop


class Bridge(QObject):
    status = Signal(str, str, str)
    log = Signal(str)
    phase = Signal(str, str, float)
    finished = Signal()
    updates = Signal(object, bool)      # list[updates.ToolReport], forced by the user


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("游戏日常总控" + (f"（{config.EDITION}）" if config.EDITION else ""))
        self.resize(1180, 780)
        self.cfg = config.load()
        self.admin = winapi.is_admin()
        self.bridge = Bridge()
        self.runner = Runner(self.cfg, Events(
            status=self.bridge.status.emit, log=self.bridge.log.emit,
            phase=self.bridge.phase.emit, finished=self.bridge.finished.emit))
        self.bridge.status.connect(self.on_status)
        self.bridge.log.connect(self.on_log)
        self.bridge.phase.connect(self.on_phase)
        self.bridge.finished.connect(self.on_finished)
        self.bridge.updates.connect(self.on_updates)
        self._update_check_running = False
        self._phase = None  # (key, text, t0)
        self._closing = False
        self._busy_widgets: list[QWidget] = []
        self._auto_state: str | None = None   # None | "unlock" | "countdown" | "running"
        self._auto_deadline = 0.0
        self._auto_dry = False
        self._ipc = None

        self.tabs = tabs = QTabWidget()
        tabs.addTab(self._build_run_tab(), "运行")
        self.daily = DailySettingsPage(self.cfg, self, status=lambda text, ms=4000: self.statusBar().showMessage(text, ms))
        tabs.addTab(self.daily, "日常设置")
        tabs.addTab(self._build_settings_tab(), "总控设置")
        root = QWidget()
        lay = QVBoxLayout(root)
        lay.setContentsMargins(14, 12, 14, 8)
        lay.setSpacing(10)
        lay.addWidget(self._build_header())
        if not self.admin:
            warn = QLabel("未以管理员身份运行：可以浏览和修改设置，但不能运行。这些游戏与脚本都需要管理员权限，"
                          + ("请关掉后重新打开 DailyHub.exe，在 UAC 提示里点“是”。" if config.FROZEN
                             else "请用项目目录下的「启动总控.cmd」打开（会弹一次 UAC）。"))
            warn.setObjectName("warnbar")
            warn.setWordWrap(True)
            lay.addWidget(warn)
        lay.addWidget(tabs)
        self.setCentralWidget(root)

        self._tick = QTimer(self)
        self._tick.timeout.connect(self._update_progress)
        self._tick.start(1000)
        self.refresh_table()

    # ------------------------------------------------------------------ layout
    def _build_header(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("header")
        h = QHBoxLayout(bar)
        h.setContentsMargins(18, 10, 18, 10)
        title = QLabel("游戏日常总控")
        title.setObjectName("appTitle")
        h.addWidget(title)
        if config.EDITION:
            ed = QLabel(config.EDITION)
            ed.setObjectName("chip")
            ed.setStyleSheet(theme.chip_style(theme.C["muted"], "#eef1f5"))
            h.addWidget(ed)
        sub = QLabel(f"{len(self.cfg.games)} 款游戏 · 按顺序自动跑日常")
        sub.setObjectName("muted")
        h.addSpacing(8)
        h.addWidget(sub)
        h.addStretch(1)
        adm = QLabel("● 管理员" if self.admin else "● 未提权")
        adm.setObjectName("chip")
        adm.setToolTip("已以管理员身份运行" if self.admin else "未以管理员身份运行，只能浏览和修改设置")
        adm.setStyleSheet(theme.chip_style(*((theme.C["success"], theme.C["success_soft"]) if self.admin
                                             else (theme.C["warn"], theme.C["warn_soft"]))))
        h.addWidget(adm)
        return bar

    def _build_run_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["启用", "游戏", "操作", "工具", "游戏启动", "状态", "说明"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.verticalHeader().setDefaultSectionSize(44)
        hh = self.table.horizontalHeader()
        hh.setHighlightSections(False)
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        for c in (COL_ON, COL_GAME, COL_TOOL, COL_LAUNCH):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(COL_MSG, QHeaderView.Stretch)
        for c, width in ((COL_STATUS, 116), (COL_ACT, 200)):   # cells drawn by widgets: fixed widths
            hh.setSectionResizeMode(c, QHeaderView.Fixed)
            self.table.setColumnWidth(c, width)
        self.table.itemChanged.connect(self._on_item_changed)

        side = QVBoxLayout()
        side.setSpacing(8)
        self.btn_up = QPushButton("上移")
        self.btn_down = QPushButton("下移")
        self.btn_up.clicked.connect(lambda: self.move_selected(-1))
        self.btn_down.clicked.connect(lambda: self.move_selected(1))
        self.btn_check = QPushButton("预检")
        self.btn_check.setToolTip("只读检查路径、配置名和已有实例，不启动任何程序")
        self.btn_check.clicked.connect(self.precheck_all)
        self.btn_updates = QPushButton("检查脚本更新")
        self.btn_updates.setToolTip("只读检查：各脚本的版本、总控判定用的日志文字、日常设置页的选项变化。\n"
                                    "每次打开总控时也会自动检查（只深入检查版本变了的脚本）。")
        self.btn_updates.clicked.connect(lambda: self.start_update_check(force=True))
        for b in (self.btn_up, self.btn_down, self.btn_check, self.btn_updates):
            side.addWidget(b)
            self._busy_widgets.append(b)
        side.addStretch(1)

        top = QHBoxLayout()
        top.setSpacing(12)
        top.addWidget(self.table, 1)
        top.addLayout(side)
        v.addLayout(top, 0)

        ctl = QHBoxLayout()
        ctl.setSpacing(10)
        self.btn_run = QPushButton("▶  一键日常")
        self.btn_run.setObjectName("primary")
        self.btn_run.setCursor(Qt.PointingHandCursor)
        self.btn_run.clicked.connect(self.toggle_run)
        self.lbl_progress = QLabel("空闲")
        self.lbl_progress.setObjectName("muted")
        ctl.addWidget(self.btn_run)
        ctl.addSpacing(6)
        ctl.addWidget(self.lbl_progress, 1)
        after = QLabel("完成后")
        after.setObjectName("muted")
        ctl.addWidget(after)
        self.cmb_after = QComboBox()
        for k, name in power.ACTIONS.items():
            self.cmb_after.addItem(name, k)
        self.cmb_after.setToolTip("一键日常 / 独立运行正常结束后执行，执行前有 60 秒倒计时可取消；"
                                  "手动点“停止”时不执行。定时任务会按 --then 设置它。")
        ctl.addWidget(self.cmb_after)
        btn_logs = QPushButton("打开总控日志目录")
        btn_logs.clicked.connect(lambda: _open(str(config.LOG_DIR), create=True))
        ctl.addWidget(btn_logs)
        v.addLayout(ctl)

        self.logbox = QPlainTextEdit()
        self.logbox.setObjectName("log")
        self.logbox.setReadOnly(True)
        self.logbox.setMaximumBlockCount(5000)
        self.logbox.setPlaceholderText("运行日志会显示在这里。点“预检”可以先只读检查一遍各款的设置。")
        mono = QFont("Consolas", 10)
        mono.setStyleHint(QFont.Monospace)
        self.logbox.setFont(mono)
        v.addWidget(self.logbox, 1)
        return w

    def _build_settings_tab(self) -> QWidget:
        outer = QWidget()
        ov = QVBoxLayout(outer)
        self.settings_area = area = QScrollArea()
        area.setWidgetResizable(True)
        inner = QWidget()
        iv = QVBoxLayout(inner)

        self.chk_continue = QCheckBox("某款出错后继续下一款")
        self.chk_continue.setChecked(self.cfg.continue_on_error)
        iv.addWidget(self.chk_continue)

        self.forms: dict[str, dict] = {}
        for g in self.cfg.ordered():
            iv.addWidget(self._game_form(g))
        iv.addStretch(1)
        area.setWidget(inner)
        ov.addWidget(area, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_save = QPushButton("保存设置")
        self.btn_save.clicked.connect(self.save_settings)
        btn_reset = QPushButton("恢复默认")
        btn_reset.clicked.connect(self.reset_settings)
        row.addWidget(btn_reset)
        row.addWidget(self.btn_save)
        self._busy_widgets += [self.btn_save, btn_reset]
        ov.addLayout(row)
        return outer

    def _game_form(self, g: GameConfig) -> QGroupBox:
        ad = adapters.get(g.adapter)
        box = QGroupBox(f"{g.name} · {ad.tool_name}")
        form = QFormLayout(box)
        fw: dict = {}

        fw["tool_dir"] = self._path_row(form, "工具目录", g.tool_dir, directory=True)
        fw["tool_dir"].setPlaceholderText(config.tool_dir_hint(g.key))
        choices = ad.option_choices(g)
        fw["options"] = {}
        for key, label in ad.option_labels().items():
            cb = QComboBox()
            cb.setEditable(True)
            for c in choices.get(key, []):
                cb.addItem(c)
            cb.setCurrentText(str(g.options.get(key, "")))
            form.addRow(label, cb)
            fw["options"][key] = cb

        mode = QComboBox()
        for m in ad.launch_modes:
            mode.addItem(LAUNCH_MODE_NAMES[m], m)
        if len(ad.launch_modes) == 1:
            mode.setEnabled(False)
            mode.setToolTip(f"{ad.tool_name} 只支持这一种方式（原因见 README）")
        mode.setCurrentIndex(max(0, mode.findData(ad.game_mode(g))))
        form.addRow("游戏启动方式", mode)
        fw["launch_mode"] = mode

        fw["game_exe"] = self._path_row(form, "游戏程序", g.game_exe, directory=False)
        fw["game_args"] = self._line(form, "游戏启动参数", g.game_args)
        fw["game_processes"] = self._line(form, "游戏进程名（逗号分隔）", ", ".join(g.game_processes))
        fw["window_class"] = self._line(form, "游戏窗口类名", g.window_class)
        fw["window_title"] = self._line(form, "窗口标题包含（可空）", g.window_title)
        fw["close_cmd"] = self._line(form, "关闭命令（可空，先于正常关闭执行）", g.close_cmd)
        if LAUNCH_VIA_LAUNCHER in ad.launch_modes:
            fw["launcher_exe"] = self._path_row(form, "启动器程序", g.launcher_exe, directory=False)
            fw["launcher_args"] = self._line(form, "启动器参数", g.launcher_args)
            fw["launcher_processes"] = self._line(form, "启动器窗口所属进程", ", ".join(g.launcher_processes))
            fw["launcher_window_class"] = self._line(form, "启动器窗口类名（可空）", g.launcher_window_class)
            fw["launcher_window_title"] = self._line(form, "启动器窗口标题包含（可空）", g.launcher_window_title)
            click = QLineEdit(g.launcher_click)
            click.setPlaceholderText("x,y（窗口内相对位置，0~1）")
            cal = QPushButton("标定…")
            cal.setToolTip("先手动打开启动器，点这里后 5 秒内把鼠标停在“开始游戏”按钮上（不要点）")
            cal.clicked.connect(lambda _=False, k=g.key: self.calibrate_launcher(k))
            row = QHBoxLayout()
            row.addWidget(click, 1)
            row.addWidget(cal)
            form.addRow("“开始游戏”按钮位置", row)
            fw["launcher_click"] = click
            self._busy_widgets.append(cal)
        fw["timeout_min"] = self._spin(form, "脚本超时（分钟）", g.timeout_min, 1, 600)
        fw["game_ready_timeout_s"] = self._spin(form, "等待游戏窗口（秒）", g.game_ready_timeout_s, 10, 1800)
        fw["game_ready_delay_s"] = self._spin(form, "窗口就绪后再等（秒）", g.game_ready_delay_s, 0, 600)
        close_chk = QCheckBox("完成后关闭游戏（先正常关闭，超时强制结束）")
        close_chk.setChecked(g.close_game_after)
        form.addRow("", close_chk)
        fw["close_game_after"] = close_chk
        fw["close_timeout_s"] = self._spin(form, "关闭游戏等待（秒）", g.close_timeout_s, 5, 300)
        self.forms[g.key] = fw
        return box

    def _line(self, form, label, value) -> QLineEdit:
        e = QLineEdit(value)
        form.addRow(label, e)
        return e

    def _spin(self, form, label, value, lo, hi) -> QSpinBox:
        s = QSpinBox()
        s.setRange(lo, hi)
        s.setValue(int(value))
        form.addRow(label, s)
        return s

    def _path_row(self, form, label, value, directory: bool) -> QLineEdit:
        e = QLineEdit(value)
        b = QPushButton("浏览…")

        def pick():
            if directory:
                p = QFileDialog.getExistingDirectory(self, label, e.text() or os.path.expanduser("~"))
            else:
                p, _ = QFileDialog.getOpenFileName(self, label, os.path.dirname(e.text()) or "C:\\",
                                                   "程序 (*.exe)")
            if p:
                e.setText(os.path.normpath(p))
        b.clicked.connect(pick)
        row = QHBoxLayout()
        row.addWidget(e, 1)
        row.addWidget(b)
        form.addRow(label, row)
        return e

    # ------------------------------------------------------------------ table
    def refresh_table(self):
        self.table.blockSignals(True)
        games = self.cfg.ordered()
        self.table.setRowCount(len(games))
        for r, g in enumerate(games):
            ad = adapters.get(g.adapter)
            on = QTableWidgetItem()
            on.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            on.setCheckState(Qt.Checked if g.enabled else Qt.Unchecked)
            on.setData(Qt.UserRole, g.key)
            self.table.setItem(r, COL_ON, on)
            self.table.setItem(r, COL_GAME, QTableWidgetItem(g.name))
            probs = ad.problems(g)
            tool = QTableWidgetItem(f"{ad.tool_name} {'✓' if not probs else '✗'}")
            tool.setToolTip("\n".join(probs) if probs else g.tool_dir)
            tool.setForeground(QColor("#1a7f37" if not probs else "#cf222e"))
            self.table.setItem(r, COL_TOOL, tool)
            short = {"hub": "总控", "launcher": "启动器", "tool": "工具"}[ad.game_mode(g)]
            self.table.setItem(r, COL_LAUNCH, QTableWidgetItem(short))
            if self.table.item(r, COL_STATUS) is None or self.table.item(r, COL_STATUS).data(Qt.UserRole) != g.key:
                self._set_status_cells(r, g.key, PENDING, "")
            self.table.setCellWidget(r, COL_ACT, self._actions(g))
        self.table.blockSignals(False)
        # Show every row without scrolling; the log box takes the remaining space.
        height = self.table.horizontalHeader().height() + 2 * self.table.frameWidth() + 2
        height += sum(self.table.rowHeight(r) for r in range(self.table.rowCount()))
        self.table.setFixedHeight(height)

    def _actions(self, g: GameConfig) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(2, 0, 2, 0)
        run = QPushButton("独立运行")
        run.setObjectName("small")
        run.setToolTip(f"只跑这一款：{g.name}（{adapters.get(g.adapter).tool_name}）")
        run.clicked.connect(lambda _=False, k=g.key: self.run_keys([k]))
        run.setEnabled(self.admin and not self.runner.running)
        menu_btn = QToolButton()
        menu_btn.setObjectName("small")
        menu_btn.setText("打开")
        menu_btn.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(menu_btn)
        for label, path in adapters.get(g.adapter).open_targets(g).items():
            act = QAction(label, menu)
            act.triggered.connect(lambda _=False, p=path: _open(p))
            menu.addAction(act)
        menu_btn.setMenu(menu)
        h.addWidget(run)
        h.addWidget(menu_btn)
        return w

    def _row_of(self, key: str) -> int:
        for r in range(self.table.rowCount()):
            it = self.table.item(r, COL_ON)
            if it and it.data(Qt.UserRole) == key:
                return r
        return -1

    def _set_status_cells(self, row: int, key: str, status: str, msg: str):
        st = QTableWidgetItem(status)          # the text stays the data; a chip widget draws it
        st.setData(Qt.UserRole, key)
        st.setForeground(QColor(0, 0, 0, 0))
        self.table.setItem(row, COL_STATUS, st)
        holder = QWidget()
        hl = QHBoxLayout(holder)
        hl.setContentsMargins(6, 0, 6, 0)
        chip = QLabel(status)
        chip.setObjectName("chip")
        chip.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        chip.setStyleSheet(theme.chip_style(*theme.STATUS_CHIP.get(status, (STATUS_COLOR.get(status, "#000000"),
                                                                             "#eef1f5"))))
        hl.addWidget(chip, 0, Qt.AlignVCenter)
        hl.addStretch(1)
        self.table.setCellWidget(row, COL_STATUS, holder)
        m = QTableWidgetItem(msg)
        m.setToolTip(msg)
        self.table.setItem(row, COL_MSG, m)

    def _on_item_changed(self, item: QTableWidgetItem):
        if item.column() != COL_ON:
            return
        key = item.data(Qt.UserRole)
        if key in self.cfg.games:
            self.cfg.games[key].enabled = item.checkState() == Qt.Checked
            config.save(self.cfg)

    def move_selected(self, delta: int):
        r = self.table.currentRow()
        keys = [g.key for g in self.cfg.ordered()]
        if r < 0 or not (0 <= r + delta < len(keys)):
            return
        keys[r], keys[r + delta] = keys[r + delta], keys[r]
        self.cfg.order = keys
        config.save(self.cfg)
        saved = {k: (self.table.item(self._row_of(k), COL_STATUS).text(),
                     self.table.item(self._row_of(k), COL_MSG).text()) for k in keys}
        self.refresh_table()
        for k, (s, m) in saved.items():
            self._set_status_cells(self._row_of(k), k, s, m)
        self.table.selectRow(r + delta)

    # ------------------------------------------------------------------ actions
    def toggle_run(self):
        if self.runner.running:
            self.runner.stop()
            self.btn_run.setEnabled(False)
            self.btn_run.setText("正在停止…")
            self.on_log(f"{time.strftime('%H:%M:%S')} 已请求停止：结束当前脚本，游戏保持打开")
            return
        keys = [g.key for g in self.cfg.ordered() if g.enabled]
        if not keys:
            QMessageBox.information(self, "一键日常", "没有勾选任何游戏")
            return
        self.run_keys(keys)

    def run_keys(self, keys: list[str]):
        if self.runner.running:
            return
        if not self.admin:
            QMessageBox.warning(self, "需要管理员", "总控未以管理员身份运行，无法启动游戏与脚本。")
            return
        self.save_settings(quiet=True)
        for k in keys:
            self._set_status_cells(self._row_of(k), k, PENDING, "")
        if self.runner.start(keys):
            power.keep_awake(True)
            self._set_busy(True)

    def precheck_all(self):
        self.save_settings(quiet=True)
        stamp = time.strftime("%H:%M:%S")
        self.on_log(f"{stamp} ===== 预检（只读，不启动任何程序）")
        if not self.admin:
            self.on_log(f"{stamp} ✗ 总控未以管理员身份运行；非管理员时看不到提权进程的完整路径和命令行，实例检查可能不全")
        for g in self.cfg.ordered():
            ad = adapters.get(g.adapter)
            probs = ad.problems(g) + ad.running_instances(g)
            mode = ad.game_mode(g)
            if mode == "hub" and not os.path.isfile(g.game_exe or ""):
                probs.append(f"游戏程序不存在：{g.game_exe}")
            if mode == LAUNCH_VIA_LAUNCHER:
                if not os.path.isfile(g.launcher_exe or ""):
                    probs.append(f"启动器程序不存在：{g.launcher_exe}")
                if games.parse_click(g.launcher_click) is None:
                    probs.append("启动器“开始游戏”按钮位置未标定（设置页 → 标定）")
                probe = ad.launcher_probe(g)
                state = probe.current_state() if probe else None
                if state:
                    self.on_log(f"{stamp}     启动器主按钮最近一次记录的状态：{state}")
            mark = "✓" if not probs else "✗"
            on = "" if g.enabled else "（未勾选）"
            ver = ad.version(g)
            self.on_log(f"{stamp} {mark} {g.name}{on}{f'（{ad.tool_name} {ver}）' if ver else ''}："
                        + ("没有发现问题" if not probs else "；".join(probs)))
            note = ad.version_note(g)
            if note:
                self.on_log(f"{stamp}     【注意】{note}")
            for m in ad.marker_problems(g):
                self.on_log(f"{stamp}     【注意】{m}")
            for w in ad.warnings(g):
                self.on_log(f"{stamp}     【警告】{w}")
        self.refresh_table()

    def save_settings(self, quiet: bool = False):
        self.cfg.continue_on_error = self.chk_continue.isChecked()
        for key, fw in self.forms.items():
            g = self.cfg.games[key]
            g.tool_dir = fw["tool_dir"].text().strip()
            for ok, cb in fw["options"].items():
                g.options[ok] = cb.currentText().strip()
            g.launch_mode = fw["launch_mode"].currentData()
            g.game_exe = fw["game_exe"].text().strip()
            g.game_args = fw["game_args"].text().strip()
            g.game_processes = [s.strip() for s in fw["game_processes"].text().split(",") if s.strip()]
            g.window_class = fw["window_class"].text().strip()
            g.window_title = fw["window_title"].text().strip()
            g.close_cmd = fw["close_cmd"].text().strip()
            if "launcher_exe" in fw:
                g.launcher_exe = fw["launcher_exe"].text().strip()
                g.launcher_args = fw["launcher_args"].text().strip()
                g.launcher_processes = [x.strip() for x in fw["launcher_processes"].text().split(",") if x.strip()]
                g.launcher_window_class = fw["launcher_window_class"].text().strip()
                g.launcher_window_title = fw["launcher_window_title"].text().strip()
                g.launcher_click = fw["launcher_click"].text().strip()
            g.timeout_min = fw["timeout_min"].value()
            g.game_ready_timeout_s = fw["game_ready_timeout_s"].value()
            g.game_ready_delay_s = fw["game_ready_delay_s"].value()
            g.close_game_after = fw["close_game_after"].isChecked()
            g.close_timeout_s = fw["close_timeout_s"].value()
        config.save(self.cfg)
        if not quiet:
            self.refresh_table()
            self.daily.refresh_games()
            self.statusBar().showMessage(f"已保存到 {config.CONFIG_PATH}", 4000)

    def reset_settings(self):
        if QMessageBox.question(self, "恢复默认", "把各游戏的总控设置恢复为默认值？") != QMessageBox.Yes:
            return
        d = config.defaults()
        for k, g in d.games.items():
            g.enabled = self.cfg.games[k].enabled
        d.order = self.cfg.order
        self.cfg.games, self.cfg.continue_on_error = d.games, d.continue_on_error
        config.save(self.cfg)
        self._fill_forms()
        self.refresh_table()
        self.statusBar().showMessage("已恢复默认设置", 4000)

    def _fill_forms(self):
        """Push self.cfg back into every settings widget (so a later save cannot resurrect old values)."""
        self.chk_continue.setChecked(self.cfg.continue_on_error)
        for key, fw in self.forms.items():
            g = self.cfg.games[key]
            fw["tool_dir"].setText(g.tool_dir)
            for ok, cb in fw["options"].items():
                cb.setCurrentText(str(g.options.get(ok, "")))
            fw["launch_mode"].setCurrentIndex(max(0, fw["launch_mode"].findData(
                adapters.get(g.adapter).game_mode(g))))
            if "launcher_exe" in fw:
                fw["launcher_exe"].setText(g.launcher_exe)
                fw["launcher_args"].setText(g.launcher_args)
                fw["launcher_processes"].setText(", ".join(g.launcher_processes))
                fw["launcher_window_class"].setText(g.launcher_window_class)
                fw["launcher_window_title"].setText(g.launcher_window_title)
                fw["launcher_click"].setText(g.launcher_click)
            fw["game_exe"].setText(g.game_exe)
            fw["game_args"].setText(g.game_args)
            fw["game_processes"].setText(", ".join(g.game_processes))
            fw["window_class"].setText(g.window_class)
            fw["window_title"].setText(g.window_title)
            fw["close_cmd"].setText(g.close_cmd)
            fw["timeout_min"].setValue(int(g.timeout_min))
            fw["game_ready_timeout_s"].setValue(int(g.game_ready_timeout_s))
            fw["game_ready_delay_s"].setValue(int(g.game_ready_delay_s))
            fw["close_game_after"].setChecked(g.close_game_after)
            fw["close_timeout_s"].setValue(int(g.close_timeout_s))

    def calibrate_launcher(self, key: str):
        g = self.cfg.games[key]
        QMessageBox.information(
            self, f"标定{g.name}启动器按钮",
            "1. 先手动打开启动器，让它停在有“开始游戏”按钮的主界面；\n"
            "2. 点“确定”后 5 秒内，把鼠标停在“开始游戏”按钮中间，不要点击。\n\n"
            "总控只读取鼠标位置和启动器窗口位置，不会点击任何东西。")
        self.statusBar().showMessage("5 秒后记录鼠标位置…", 5000)
        QTimer.singleShot(5000, lambda: self._finish_calibration(key))

    def _finish_calibration(self, key: str):
        fw = self.forms[key]
        probe = config.GameConfig(key=key, name="", adapter="",
                                  launcher_processes=[x.strip() for x in fw["launcher_processes"].text().split(",")
                                                      if x.strip()],
                                  launcher_window_class=fw["launcher_window_class"].text().strip(),
                                  launcher_window_title=fw["launcher_window_title"].text().strip())
        w = games.find_launcher_window(probe)
        if w is None or w.minimized:
            QMessageBox.warning(self, "标定", "没有找到处于正常显示状态的启动器窗口（检查“启动器窗口所属进程”等设置）。")
            return
        x, y = winapi.cursor_pos()
        left, top, width, height = winapi.client_rect_on_screen(w.hwnd)
        rx, ry = (x - left) / max(width, 1), (y - top) / max(height, 1)
        if not (0 < rx < 1 and 0 < ry < 1):
            QMessageBox.warning(self, "标定", "鼠标不在启动器窗口里，请重试。")
            return
        fw["launcher_click"].setText(f"{rx:.3f},{ry:.3f}")
        self.save_settings(quiet=True)
        self.statusBar().showMessage(f"已记录按钮位置 {rx:.3f},{ry:.3f}（窗口 {width}x{height}）", 8000)

    def _set_busy(self, busy: bool):
        self.btn_run.setText("■  停止" if busy else "▶  一键日常")
        self.btn_run.setProperty("running", busy)
        theme.repolish(self.btn_run)
        self.btn_run.setEnabled(True)
        for w in self._busy_widgets:
            w.setEnabled(not busy)
        self.refresh_table()

    # ------------------------------------------------------------------ runner events
    def on_status(self, key: str, status: str, msg: str):
        r = self._row_of(key)
        if r >= 0:
            self._set_status_cells(r, key, status, msg)

    def on_phase(self, key: str, text: str, t0: float):
        self._phase = (key, text, t0)
        r = self._row_of(key)
        if r >= 0:
            self._set_status_cells(r, key, RUNNING, text)
        self._update_progress()

    def _update_progress(self):
        if not self.runner.running or not self._phase:
            if not self.runner.running:
                self.lbl_progress.setText("空闲")
            return
        key, text, t0 = self._phase
        g = self.cfg.games.get(key)
        el = int(time.time() - t0)
        self.lbl_progress.setText(f"当前：{g.name if g else key} · {text} · 已用 {el // 60:02d}:{el % 60:02d}")

    def on_log(self, line: str):
        color = None
        if "【失败】" in line or "✗" in line:
            color = "#cf222e"
        elif "【警告】" in line:
            color = "#9a6700"
        elif "【完成】" in line or "=====" in line:
            color = "#1a7f37"
        esc = html.escape(line)
        self.logbox.appendHtml(f'<span style="color:{color}">{esc}</span>' if color else esc)

    def on_finished(self):
        self._phase = None
        self._set_busy(False)
        power.keep_awake(False)
        if self._closing:
            self.close()
            return
        was_auto, self._auto_state = self._auto_state == "running", None
        if self.runner.stopped_by_user:
            if self.cmb_after.currentData() != "none":
                self._auto_say(f"队列是手动停止的，不执行“{self.cmb_after.currentText()}”", log_only=not was_auto)
            return
        self._after_run()

    # ------------------------------------------------------------------ tool updates
    def start_update_check(self, force: bool = False):
        """Background check of tool versions / markers / settings drift (updates.py)."""
        if self._update_check_running:
            return
        self._update_check_running = True
        self.btn_updates.setEnabled(False)

        def work():
            try:
                reports = updates.check(self.cfg, force=force)
            except Exception as e:     # never let the check break the hub
                self.bridge.log.emit(f"{time.strftime('%H:%M:%S')} 【警告】脚本更新检查出错：{e!r}")
                reports = None
            self.bridge.updates.emit(reports, force)
        threading.Thread(target=work, daemon=True).start()

    def on_updates(self, reports, forced: bool):
        self._update_check_running = False
        self.btn_updates.setEnabled(True)
        if reports is None:
            return
        attention = [r for r in reports if r.needs_attention]
        if not attention and not forced:
            updates.save_versions(reports)      # first start or nothing changed: just remember the versions
            return
        path = updates.write_report(reports)
        updates.save_versions(reports)
        stamp = time.strftime("%H:%M:%S")
        text = updates.summary(reports) if attention else "所有脚本的版本都和上次一样，判定用的文字也都还在。"
        for line in text.splitlines():
            self.on_log(f"{stamp} 【更新检查】{line}")
        box = QMessageBox(self)
        box.setWindowTitle("脚本更新检查")
        box.setIcon(QMessageBox.Information if not any(r.markers_missing or r.settings_error for r in attention)
                    else QMessageBox.Warning)
        box.setText(("自上次打开总控以来：\n\n" if attention else "") + text)
        box.setInformativeText("会消耗资源的新选项不会被自动改动；新出现的设置在“日常设置”页只读显示。\n"
                               "需要更新适配时，把报告交给 Claude。")
        open_btn = box.addButton("打开报告", QMessageBox.ActionRole)
        ok_btn = box.addButton("知道了", QMessageBox.AcceptRole)
        box.setEscapeButton(ok_btn)          # the title-bar X and Esc close it too
        box.setModal(False)
        box.buttonClicked.connect(lambda b: _open(path) if b is open_btn else None)
        box.show()
        self.daily.refresh_games()

    # ------------------------------------------------------------------ unattended runs
    def start_ipc(self):
        """Listen for a second start (instance.forward); only the elevated hub does this."""
        from PySide6.QtNetwork import QLocalServer
        self._ipc = QLocalServer(self)
        self._ipc.setSocketOptions(QLocalServer.UserAccessOption)
        if not self._ipc.listen(instance.NAME):
            self.on_log(f"{time.strftime('%H:%M:%S')} 【警告】单实例监听失败：{self._ipc.errorString()}")
            return
        self._ipc.newConnection.connect(self._on_ipc)

    def _on_ipc(self):
        while self._ipc.hasPendingConnections():
            sock = self._ipc.nextPendingConnection()
            state = {"buf": b"", "done": False}

            def read(sock=sock, state=state):
                if state["done"]:
                    return
                state["buf"] += bytes(sock.readAll())
                if b"\n" not in state["buf"]:
                    return
                state["done"] = True
                args = instance.parse(state["buf"].split(b"\n", 1)[0])
                sock.disconnectFromServer()
                if args is not None:
                    self.handle_args(args, forwarded=True)
            sock.readyRead.connect(read)
            sock.disconnected.connect(sock.deleteLater)
            read()

    def handle_args(self, args: list[str], forwarded: bool = False):
        if forwarded:
            self.showNormal()
            self.raise_()
            self.activateWindow()
        if "--auto" in args:
            self.auto_run(power.parse_then(args), dry="--dry" in args)

    def auto_run(self, then: str, dry: bool = False):
        """Scheduled start: wait for an unlocked desktop, count down, run the ticked games, then `then`.
        dry: the same path without running any game (to test wake-up and the power action)."""
        self._auto_say(f"定时任务启动（完成后：{power.ACTIONS[then]}{'；演练，不运行游戏' if dry else ''}）")
        if self.runner.running or self._auto_state:
            self._auto_say("已有队列在运行或等待开始，忽略这次定时启动")
            return
        if not self.admin:
            self._auto_say("✗ 总控不是管理员，无法运行（检查计划任务是否勾选了“使用最高权限运行”）")
            return
        self.cmb_after.setCurrentIndex(max(0, self.cmb_after.findData(then)))
        self._auto_dry = dry
        power.keep_awake(True)
        power.wake_display()
        self._auto_state = "unlock"
        self._auto_deadline = time.time() + UNLOCK_WAIT_S
        self._auto_poll(first=True)

    def _auto_poll(self, first: bool = False):
        if self._auto_state != "unlock":
            return
        if not power.session_locked():
            self._auto_state = "countdown"
            self._countdown("定时日常", "将在 {n} 秒后开始一键日常。", AUTO_START_DELAY_S, "立即开始",
                            self._auto_start_decided)
            return
        if time.time() >= self._auto_deadline:
            self._auto_say("✗ 屏幕一直是锁定状态，脚本没法操作游戏，跳过这次日常")
            self._auto_state = None
            power.keep_awake(False)
            self._after_run()
            return
        if first:
            self._auto_say(f"屏幕已锁定，等待解锁（最多 {UNLOCK_WAIT_S // 60} 分钟）…")
        QTimer.singleShot(10_000, self._auto_poll)

    def _auto_start_decided(self, go: bool):
        if not go:
            self._auto_say("已取消这次定时日常")
            self._auto_state = None
            power.keep_awake(False)
            return
        keys = [g.key for g in self.cfg.ordered() if g.enabled]
        if self._auto_dry or not keys:
            self._auto_say("演练：不运行任何游戏" if self._auto_dry else "没有勾选任何游戏")
            self._auto_state = None
            power.keep_awake(False)
            self._after_run()
            return
        self._auto_say("开始一键日常：" + "、".join(self.cfg.games[k].name for k in keys))
        self._auto_state = "running"
        self.run_keys(keys)
        if not self.runner.running:
            self._auto_state = None
            power.keep_awake(False)

    def _after_run(self):
        action = self.cmb_after.currentData()
        if action == "none":
            return
        name = power.ACTIONS[action]
        power.keep_awake(True)

        def decided(go: bool):
            power.keep_awake(False)
            if go:
                self._do_power(action)
            else:
                self._auto_say(f"已取消{name}")
        self._countdown("完成后操作", f"日常已结束，{{n}} 秒后{name}。", AFTER_ACTION_DELAY_S, f"立即{name}", decided)

    def _do_power(self, action: str):
        name = power.ACTIONS[action]
        self._auto_say(f"执行：{name}")

        def work():
            err = power.perform(action)
            if err:
                power.log_event(f"{name}失败：{err}")
                self.bridge.log.emit(f"{time.strftime('%H:%M:%S')} 【定时】✗ {name}失败：{err}")
            elif action == "sleep":
                power.log_event("已从睡眠中唤醒")
                self.bridge.log.emit(f"{time.strftime('%H:%M:%S')} 【定时】已从睡眠中唤醒")
        threading.Thread(target=work, daemon=True).start()

    def _countdown(self, title: str, text: str, seconds: int, go_text: str, done):
        """Non-modal countdown box: `done(True)` at 0 or on go_text, `done(False)` on 取消 / Esc."""
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setIcon(QMessageBox.Information)
        go = box.addButton(go_text, QMessageBox.AcceptRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.setModal(False)
        box.setText(text.format(n=seconds))
        state = {"left": seconds, "done": False}
        timer = QTimer(box)

        def finish(ok: bool):
            if state["done"]:
                return
            state["done"] = True
            timer.stop()
            box.done(0)
            box.deleteLater()
            done(ok)

        def tick():
            state["left"] -= 1
            if state["left"] <= 0:
                finish(True)
            else:
                box.setText(text.format(n=state["left"]))
        timer.timeout.connect(tick)
        timer.start(1000)
        box.buttonClicked.connect(lambda b: finish(b is go))
        box.rejected.connect(lambda: finish(False))
        box.show()
        box.raise_()

    def _auto_say(self, text: str, log_only: bool = False):
        self.on_log(f"{time.strftime('%H:%M:%S')} 【定时】{text}")
        if not log_only:
            power.log_event(text)

    def closeEvent(self, e):
        if not self._closing and not self.daily.confirm_discard():
            e.ignore()
            return
        if self.runner.running:
            if not self._closing:
                if QMessageBox.question(self, "退出", "队列正在运行。退出会结束当前脚本（游戏保持打开），确定？") \
                        != QMessageBox.Yes:
                    e.ignore()
                    return
                self._closing = True
                self.runner.stop()
            e.ignore()
            return
        e.accept()


def _open(path: str, create: bool = False):
    if create:
        os.makedirs(path, exist_ok=True)
    if os.path.exists(path):
        os.startfile(path)
    else:
        QMessageBox.warning(None, "打开", f"路径不存在：{path}")


def run(argv: list[str]) -> int:
    # Own taskbar identity (otherwise Windows groups the window under pythonw.exe and shows its icon).
    winapi.set_app_id("dailyhub." + (config.EDITION or "main"))
    app = QApplication(sys.argv[:1])
    app.setApplicationName("游戏日常总控")
    app.setWindowIcon(QIcon(str(theme.ASSETS / "app.ico")))
    theme.apply(app)
    win = MainWindow()
    win.show()
    if "--screenshot" not in argv:
        if win.admin:
            win.start_ipc()
        QTimer.singleShot(800, win.start_update_check)
        if "--auto" in argv:
            QTimer.singleShot(1500, lambda: win.handle_args(argv))
    else:
        i = argv.index("--screenshot")
        out = argv[i + 1] if i + 1 < len(argv) else "hub.png"
        tab = argv[i + 2] if i + 2 < len(argv) and argv[i + 2].isdigit() else "0"

        def shot():
            # tab "2" = the whole settings page content (not just the visible part)
            idx = min(int(tab), win.tabs.count() - 1)
            win.tabs.setCurrentIndex(idx)
            target = win.settings_area.widget() if idx == 2 else win
            QTimer.singleShot(300, lambda: (target.grab().save(out), app.quit()))
        QTimer.singleShot(1200, shot)
    return app.exec()
