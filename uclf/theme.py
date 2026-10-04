"""Gruvbox dark palette and the Qt stylesheet built from it.

Colours are the canonical gruvbox dark values (see misohgro's gruvbox).  The
stylesheet is one string applied to the ``QApplication``; widgets opt into
special looks through ``objectName`` or a dynamic property (see
:func:`set_state`), which is how the status pill, the giant timer and the
medal-coloured ranks are painted.
"""

from __future__ import annotations

from qtpy import QtWidgets

GRUVBOX = {
    "bg0_h": "#1d2021",
    "bg0": "#282828",
    "bg1": "#3c3836",
    "bg2": "#504945",
    "bg3": "#665c54",
    "bg4": "#7c6f64",
    "fg0": "#fbf1c7",
    "fg1": "#ebdbb2",
    "fg2": "#d5c4a1",
    "fg3": "#bdae93",
    "gray": "#a89984",
    "gray0": "#928374",
    "red": "#cc241d",
    "red_b": "#fb4934",
    "green": "#98971a",
    "green_b": "#b8bb26",
    "yellow": "#d79921",
    "yellow_b": "#fabd2f",
    "blue": "#458588",
    "blue_b": "#83a598",
    "purple": "#b16286",
    "purple_b": "#d3869b",
    "aqua": "#689d6a",
    "aqua_b": "#8ec07c",
    "orange": "#d65d0e",
    "orange_b": "#fe8019",
}

MONO_FAMILIES = '"JetBrains Mono", "DejaVu Sans Mono", "Cascadia Mono", Consolas, "Liberation Mono", monospace'
UI_FAMILIES = '"Inter", "Noto Sans", "Segoe UI", "DejaVu Sans", sans-serif'


def color(name: str) -> str:
    return GRUVBOX[name]


def set_state(widget: QtWidgets.QWidget, name: str, value: str | bool | None) -> None:
    """Set a dynamic property and re-polish so ``[prop="value"]`` selectors apply."""
    if widget.property(name) == value:
        return
    widget.setProperty(name, value)
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)
    widget.update()


def stylesheet() -> str:
    c = GRUVBOX
    return f"""
/* The universal rule must NOT paint backgrounds, otherwise it overrides the
   per-cell colours the tables set through Qt.BackgroundRole. */
* {{
    font-family: {UI_FAMILIES};
    font-size: 14px;
    color: {c['fg1']};
    border: none;
    outline: none;
    selection-background-color: {c['bg3']};
    selection-color: {c['fg0']};
}}
QWidget {{ background: {c['bg0']}; }}
QMainWindow, QDialog {{ background: {c['bg0_h']}; }}
QWidget#PageRoot {{ background: {c['bg0_h']}; }}

/* ---------------------------------------------------------------- panels */
QGroupBox {{
    background: {c['bg0']};
    border: 1px solid {c['bg1']};
    border-radius: 10px;
    margin-top: 14px;
    padding: 10px 12px 12px 12px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    top: 2px;
    padding: 0 6px;
    color: {c['gray']};
    background: {c['bg0_h']};
}}
QLabel {{ background: transparent; }}
QLabel[role="muted"] {{ color: {c['gray0']}; font-weight: 400; }}
QLabel[role="hint"] {{ color: {c['gray']}; font-size: 12px; font-weight: 400; }}
QLabel[role="ok"] {{ color: {c['green_b']}; font-weight: 600; }}
QLabel[role="warn"] {{ color: {c['yellow_b']}; font-weight: 600; }}
QLabel[role="bad"] {{ color: {c['red_b']}; font-weight: 600; }}
QLabel#SectionKey {{ color: {c['fg2']}; font-weight: 600; }}

/* ------------------------------------------------------------- controls */
QPushButton, QComboBox, QSpinBox, QLineEdit {{
    background: {c['bg1']};
    border: 1px solid {c['bg2']};
    border-radius: 6px;
    padding: 6px 10px;
    color: {c['fg1']};
    min-height: 18px;
}}
QComboBox, QSpinBox, QLineEdit {{ background: {c['bg0_h']}; }}
QPushButton:hover, QComboBox:hover, QSpinBox:hover, QLineEdit:editable:hover {{
    border-color: {c['bg3']};
}}
QPushButton:pressed {{ background: {c['bg2']}; }}
QPushButton:disabled, QComboBox:disabled, QSpinBox:disabled, QLineEdit:disabled {{
    color: {c['gray0']};
    background: {c['bg1']};
    border-color: {c['bg2']};
}}
QPushButton:checked {{ background: {c['bg2']}; border-color: {c['bg4']}; }}
QPushButton[role="primary"] {{
    background: {c['green']};
    border-color: {c['green_b']};
    color: {c['fg0']};
    font-weight: 700;
}}
QPushButton[role="primary"]:hover {{ background: {c['green_b']}; }}
QPushButton[role="danger"] {{
    background: {c['red']};
    border-color: {c['red_b']};
    color: {c['fg0']};
    font-weight: 700;
}}
QPushButton[role="danger"]:hover {{ background: {c['red_b']}; }}
QPushButton[role="busy"] {{ background: {c['yellow']}; border-color: {c['yellow_b']}; color: {c['bg0_h']}; }}
QLineEdit[role="invalid"] {{ border: 1px solid {c['red_b']}; background: #3a2a26; }}
QLineEdit[role="filled"] {{ border: 1px solid {c['blue_b']}; }}
QLineEdit {{ font-family: {MONO_FAMILIES}; }}
QSpinBox {{ font-family: {MONO_FAMILIES}; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: top right; width: 20px; border-left: 1px solid {c['bg2']}; }}
QComboBox::down-arrow {{ image: none; border-left: 4px solid transparent; border-right: 4px solid transparent; border-top: 6px solid {c['gray']}; margin-right: 6px; }}
QComboBox QAbstractItemView {{
    background: {c['bg1']};
    border: 1px solid {c['bg3']};
    color: {c['fg1']};
    outline: none;
}}
QSpinBox::up-button, QSpinBox::down-button {{ background: {c['bg2']}; width: 16px; border: none; }}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{ background: {c['bg3']}; }}
QSpinBox::up-arrow {{ border-left: 4px solid transparent; border-right: 4px solid transparent; border-bottom: 5px solid {c['gray']}; }}
QSpinBox::down-arrow {{ border-left: 4px solid transparent; border-right: 4px solid transparent; border-top: 5px solid {c['gray']}; }}
QCheckBox {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator {{
    width: 15px; height: 15px;
    border: 1px solid {c['bg3']};
    border-radius: 4px;
    background: {c['bg0_h']};
}}
QCheckBox::indicator:checked {{ background: {c['green']}; border-color: {c['green_b']}; }}

/* --------------------------------------------------------------- tables */
QTableView {{
    background: {c['bg0']};
    alternate-background-color: {c['bg0_h']};
    border: 1px solid {c['bg1']};
    border-radius: 8px;
    gridline-color: {c['bg1']};
    font-family: {MONO_FAMILIES};
    font-size: 15px;
    selection-background-color: {c['bg2']};
    selection-color: {c['fg0']};
    padding: 2px;
}}
QTableView::item {{ padding: 6px 8px; border: none; }}
QTableView::item:selected {{ background: {c['bg2']}; color: {c['fg0']}; }}
QHeaderView {{ background: {c['bg1']}; border: none; }}
QHeaderView::section {{
    background: {c['bg1']};
    color: {c['gray']};
    padding: 7px 8px;
    border: none;
    border-bottom: 2px solid {c['bg2']};
    font-weight: 700;
    font-family: {UI_FAMILIES};
}}
QTableCornerButton::section {{ background: {c['bg1']}; border: none; }}

/* ------------------------------------------------------------------ log */
QPlainTextEdit#SerialLog {{
    background: {c['bg0_h']};
    border: 1px solid {c['bg1']};
    border-radius: 8px;
    font-family: {MONO_FAMILIES};
    font-size: 13px;
    color: {c['fg2']};
    padding: 6px;
}}
QListView#PendingView {{
    background: {c['bg0_h']};
    alternate-background-color: {c['bg0']};
    border: 1px solid {c['bg1']};
    border-radius: 8px;
    font-family: {MONO_FAMILIES};
    padding: 4px;
}}
QListView#PendingView::item {{ padding: 4px 6px; border-radius: 4px; }}
QListView#PendingView::item:selected {{ background: {c['bg2']}; }}

/* ------------------------------------------------------------- menubar */
QMenuBar {{ background: {c['bg0_h']}; border-bottom: 1px solid {c['bg1']}; padding: 2px; }}
QMenuBar::item {{ background: transparent; padding: 6px 12px; border-radius: 6px; }}
QMenuBar::item:selected {{ background: {c['bg1']}; }}
QMenu {{ background: {c['bg1']}; border: 1px solid {c['bg3']}; border-radius: 8px; padding: 6px; }}
QMenu::item {{ background: transparent; padding: 6px 26px 6px 16px; border-radius: 4px; }}
QMenu::item:selected {{ background: {c['bg3']}; color: {c['fg0']}; }}
QMenu::separator {{ height: 1px; background: {c['bg3']}; margin: 6px 4px; }}
QStatusBar {{ background: {c['bg0_h']}; color: {c['gray']}; border-top: 1px solid {c['bg1']}; }}
QStatusBar::item {{ border: none; }}
QToolTip {{
    background: {c['bg2']};
    color: {c['fg0']};
    border: 1px solid {c['bg4']};
    padding: 4px 6px;
    border-radius: 4px;
}}

/* --------------------------------------------------------------- scroll */
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['bg3']}; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {c['bg4']}; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {c['bg3']}; border-radius: 5px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; background: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

/* ------------------------------------------------------------- scoreboard */
QWidget#ScoreboardRoot {{ background: {c['bg0_h']}; }}
QLabel#BoardTitle {{
    font-size: 30px;
    font-weight: 800;
    letter-spacing: 3px;
    color: {c['fg0']};
    background: transparent;
}}
QLabel#BoardSubtitle {{ font-size: 15px; color: {c['gray']}; background: transparent; }}
QFrame#TimerPanel {{
    background: {c['bg0']};
    border: 1px solid {c['bg2']};
    border-radius: 16px;
}}
QLabel#TimerValue {{ color: {c['fg0']}; background: transparent; font-weight: 800; }}
QLabel#TimerValue[state="running"] {{ color: {c['green_b']}; }}
QLabel#TimerValue[state="stopped"] {{ color: {c['yellow_b']}; }}
QLabel#TimerValue[state="reset"] {{ color: {c['gray0']}; }}
QLabel#TimerUnit {{ color: {c['gray']}; font-size: 22px; background: transparent; }}
QLabel#RaceState {{
    font-size: 20px;
    font-weight: 800;
    letter-spacing: 4px;
    padding: 6px 16px;
    border-radius: 999px;
    background: {c['bg1']};
    color: {c['fg2']};
}}
QLabel#RaceState[state="running"] {{ background: {c['green']}; color: {c['bg0_h']}; }}
QLabel#RaceState[state="stopped"] {{ background: {c['yellow']}; color: {c['bg0_h']}; }}
QLabel#RaceState[state="waiting"] {{ background: {c['blue']}; color: {c['fg0']}; }}
QLabel#RaceState[state="reset"] {{ background: {c['red']}; color: {c['fg0']}; }}
QLabel#LastRun {{ color: {c['fg2']}; font-size: 17px; background: transparent; }}
QLabel#Pill {{
    background: {c['bg1']};
    border: 1px solid {c['bg3']};
    border-radius: 999px;
    padding: 5px 14px;
    color: {c['gray']};
    font-weight: 600;
}}
QLabel#Pill[state="ok"] {{ border-color: {c['green']}; color: {c['green_b']}; }}
QLabel#Pill[state="bad"] {{ border-color: {c['red']}; color: {c['red_b']}; }}
QLabel#Pill[state="warn"] {{ border-color: {c['yellow']}; color: {c['yellow_b']}; }}
QLabel#FieldLabel {{ color: {c['gray']}; font-size: 12px; font-weight: 700; letter-spacing: 1px; }}
"""
