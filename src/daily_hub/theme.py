"""Light theme: one palette and one stylesheet for the whole hub.

The Fusion style is used so the stylesheet renders the same on every Windows version.  Widgets opt into
special looks through objectName: primary (一键日常), accent (保存), small (row buttons), header, chip,
warnbar, log, stateBox, pageTitle, formArea / formPage, muted.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

C = {
    "bg": "#f3f5f8",
    "card": "#ffffff",
    "border": "#e3e7ed",
    "border_strong": "#cfd6df",
    "text": "#1f2328",
    "muted": "#667085",
    "faint": "#a3abb7",
    "accent": "#2563eb",
    "accent_hover": "#1d4ed8",
    "accent_soft": "#e8f0fe",
    "hover": "#f2f5fa",
    "success": "#15803d",
    "success_soft": "#e7f6ec",
    "warn": "#b45309",
    "warn_soft": "#fff4e5",
    "danger": "#dc2626",
    "danger_hover": "#b91c1c",
    "danger_soft": "#fdecec",
}

# status text -> (text colour, background) for the status chips (runner status names)
STATUS_CHIP = {
    "待运行": (C["muted"], "#eef1f5"),
    "运行中": (C["accent"], C["accent_soft"]),
    "已完成": (C["success"], C["success_soft"]),
    "异常/跳过": (C["danger"], C["danger_soft"]),
    "已停止": (C["warn"], C["warn_soft"]),
}

ASSETS = Path(__file__).with_name("assets")
_ASSETS = ASSETS.as_posix()

QSS = f"""
QMainWindow, QDialog {{ background: {C['bg']}; }}
QToolTip {{ background: #1f2937; color: #ffffff; border: none; padding: 6px 8px; border-radius: 6px; }}
QStatusBar {{ background: {C['bg']}; color: {C['muted']}; }}
QLabel#muted {{ color: {C['muted']}; }}

/* header */
QFrame#header {{ background: {C['card']}; border: 1px solid {C['border']}; border-radius: 12px; }}
QLabel#appTitle {{ font-size: 15pt; font-weight: bold; color: {C['text']}; }}
QLabel#chip {{ border-radius: 10px; padding: 2px 10px; font-weight: bold; }}
QLabel#warnbar {{ background: {C['warn_soft']}; color: {C['warn']}; border: 1px solid #f6d7a7;
                  border-radius: 8px; padding: 8px 12px; }}

/* tabs */
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {C['muted']}; padding: 7px 18px; margin: 0 4px 6px 0;
               border-radius: 8px; font-weight: bold; }}
QTabBar::tab:selected {{ background: {C['card']}; color: {C['accent']}; border: 1px solid {C['border']}; }}
QTabBar::tab:hover:!selected {{ background: #e8ecf2; color: {C['text']}; }}

/* buttons */
QPushButton, QToolButton {{ background: {C['card']}; color: {C['text']}; border: 1px solid {C['border_strong']};
                           border-radius: 7px; padding: 6px 14px; }}
QPushButton:hover, QToolButton:hover {{ border-color: {C['accent']}; background: {C['hover']}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {C['accent_soft']}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {C['faint']}; background: #f6f7f9; border-color: {C['border']}; }}
QToolButton::menu-indicator {{ image: url({_ASSETS}/chevron-down.svg); subcontrol-position: right center;
                              subcontrol-origin: padding; right: 6px; width: 10px; height: 10px; }}
QToolButton[popupMode="2"] {{ padding-right: 22px; }}
QPushButton#primary {{ background: {C['accent']}; color: #ffffff; border: none; border-radius: 10px;
                      padding: 10px 30px; font-size: 13pt; font-weight: bold; }}
QPushButton#primary:hover {{ background: {C['accent_hover']}; }}
QPushButton#primary[running="true"] {{ background: {C['danger']}; }}
QPushButton#primary[running="true"]:hover {{ background: {C['danger_hover']}; }}
QPushButton#primary:disabled {{ background: #9db7f0; color: #ffffff; }}
QPushButton#accent {{ background: {C['accent']}; color: #ffffff; border: none; font-weight: bold; }}
QPushButton#accent:hover {{ background: {C['accent_hover']}; }}
QPushButton#accent:disabled {{ background: #c9d6f2; color: #ffffff; }}
QPushButton#small, QToolButton#small {{ padding: 4px 10px; border-radius: 6px; min-width: 64px; }}
QToolButton#small[popupMode="2"] {{ padding-right: 20px; min-width: 36px; }}

/* inputs */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {{
    background: {C['card']}; border: 1px solid {C['border_strong']}; border-radius: 6px; padding: 4px 8px;
    selection-background-color: {C['accent']}; selection-color: #ffffff; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus {{
    border-color: {C['accent']}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    background: #f6f7f9; color: {C['faint']}; border-color: {C['border']}; }}
QComboBox {{ padding-right: 24px; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{ image: url({_ASSETS}/chevron-down.svg); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{ background: {C['card']}; border: 1px solid {C['border']}; outline: 0; padding: 4px;
                              selection-background-color: {C['accent_soft']}; selection-color: {C['text']}; }}

/* check boxes (also the ones inside tables and lists) */
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator, QTableView::indicator, QListView::indicator {{
    width: 16px; height: 16px; border: 1px solid {C['border_strong']}; border-radius: 4px; background: {C['card']}; }}
QCheckBox::indicator:hover, QTableView::indicator:hover, QListView::indicator:hover {{ border-color: {C['accent']}; }}
QCheckBox::indicator:checked, QTableView::indicator:checked, QListView::indicator:checked {{
    background: {C['accent']}; border-color: {C['accent']}; image: url({_ASSETS}/check.svg); }}
QCheckBox::indicator:disabled {{ background: #eef0f3; border-color: {C['border']}; }}
QCheckBox::indicator:checked:disabled {{ background: #9db7f0; border-color: #9db7f0; }}

/* table */
QTableWidget {{ background: {C['card']}; border: 1px solid {C['border']}; border-radius: 12px;
               gridline-color: transparent; selection-background-color: {C['accent_soft']};
               selection-color: {C['text']}; outline: 0; }}
QTableWidget::item {{ padding: 0 8px; border-bottom: 1px solid #eef1f5; }}
QTableWidget::item:selected {{ background: {C['accent_soft']}; color: {C['text']}; }}
QHeaderView::section {{ background: {C['card']}; color: {C['muted']}; border: none;
                       border-bottom: 1px solid {C['border']}; padding: 8px; font-weight: bold; }}
QHeaderView {{ background: transparent; border: none; }}
QTableCornerButton::section {{ background: {C['card']}; border: none; }}

/* lists (日常设置) */
QListWidget {{ background: {C['card']}; border: 1px solid {C['border']}; border-radius: 12px; padding: 6px; outline: 0; }}
QListWidget::item {{ padding: 7px 8px; border-radius: 7px; }}
QListWidget::item:selected {{ background: {C['accent_soft']}; color: {C['accent']}; }}
QListWidget::item:hover:!selected {{ background: {C['hover']}; }}

/* cards */
QGroupBox {{ background: {C['card']}; border: 1px solid {C['border']}; border-radius: 12px;
            margin-top: 18px; padding: 16px 14px 10px 14px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 14px; top: 2px; padding: 0 6px; color: {C['accent']}; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea#formArea {{ background: {C['card']}; border: 1px solid {C['border']}; border-radius: 12px; }}
QWidget#formPage, QWidget#settingsPage {{ background: transparent; }}
QLabel#stateBox {{ background: {C['card']}; border: 1px solid {C['border']}; border-radius: 10px; padding: 8px 12px; }}
QLabel#pageTitle {{ font-size: 14pt; font-weight: bold; }}
QPlainTextEdit#log {{ background: {C['card']}; border: 1px solid {C['border']}; border-radius: 12px; padding: 8px; }}

/* menus */
QMenu {{ background: {C['card']}; border: 1px solid {C['border']}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 6px 22px 6px 14px; border-radius: 5px; }}
QMenu::item:selected {{ background: {C['accent_soft']}; color: {C['accent']}; }}
QMenu::item:disabled {{ color: {C['faint']}; }}
QMenu::separator {{ height: 1px; background: {C['border']}; margin: 4px 8px; }}

/* scroll bars */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px 2px; }}
QScrollBar::handle:vertical {{ background: #cdd3db; border-radius: 4px; min-height: 32px; }}
QScrollBar::handle:vertical:hover {{ background: #aeb6c2; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px 4px; }}
QScrollBar::handle:horizontal {{ background: #cdd3db; border-radius: 4px; min-width: 32px; }}
QScrollBar::handle:horizontal:hover {{ background: #aeb6c2; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
"""


def apply(app: QApplication) -> None:
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    pal = app.palette()
    for role, key in ((QPalette.Window, "bg"), (QPalette.Base, "card"), (QPalette.AlternateBase, "hover"),
                      (QPalette.Button, "card"), (QPalette.WindowText, "text"), (QPalette.Text, "text"),
                      (QPalette.ButtonText, "text"), (QPalette.ToolTipBase, "text"),
                      (QPalette.Highlight, "accent"), (QPalette.PlaceholderText, "faint")):
        pal.setColor(role, QColor(C[key]))
    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    app.setPalette(pal)
    app.setStyleSheet(QSS)


def chip_style(fg: str, bg: str) -> str:
    return f"color:{fg}; background:{bg};"


def repolish(widget) -> None:
    """Re-apply the stylesheet after a dynamic property used in a selector changed."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()
