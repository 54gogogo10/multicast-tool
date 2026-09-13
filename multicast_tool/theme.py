"""Modern design system: palettes, fonts and a Qt-5/6-compatible QSS sheet.

The application ships two themes (``dark`` default, ``light``). Both are
pure Qt -- no third-party styling library -- so the same source keeps
building for the PySide2 / Windows 7 target.

Everything user-facing reads its colors from here:

* :func:`apply_app` styles a ``QApplication`` (Fusion base + palette + QSS).
* :func:`rate_color` returns the grey/green/amber/red used for live
  rate values so table and tiles adapt to the active theme.
* The chosen theme persists in ``QSettings`` under ``ui/theme``.

Note on dynamic colors: the QSS deliberately does NOT set ``color`` for
plain ``QLabel`` / ``QTableWidget`` items; text colors come from the
QPalette instead. That keeps ``setForeground()`` (used for live rate
coloring in ui.py) working under a stylesheet.
"""

from __future__ import annotations

import sys
from string import Template

from .qt_compat import QApplication, QColor, QFont, QPalette

THEMES = ("dark", "light")

# Design tokens. Every entry is used either by the QSS template below or
# by _palette() / rate_color(); nothing here is decorative-only.
PALETTES: dict[str, dict[str, str]] = {
    "dark": {
        "window": "#12141a",
        "card": "#1a1d26",
        "card_alt": "#1f232e",
        "input": "#232734",
        "btn": "#232734",
        "btn_hover": "#272c3a",
        "btn_pressed": "#2d3342",
        "menu_hover": "#272c3a",
        "border": "#2b303e",
        "text": "#e6e9f2",
        "text_dim": "#9aa3b8",
        "text_disabled": "#5f6880",
        "accent": "#4f8cff",
        "accent_hover": "#6a9eff",
        "accent_pressed": "#3b74e0",
        "sel": "#2a4270",
        "sel_text": "#eef2fb",
        "tab_active": "#262b38",
        "primary_disabled_bg": "#2b3a5c",
        "primary_disabled_text": "#93a7cc",
        "danger": "#e5534b",
        "danger_soft": "#38262a",
        "rate_dim": "#7d8598",
        "rate_ok": "#45c078",
        "rate_warn": "#d9a03c",
        "rate_high": "#e5534b",
        "console_bg": "#0e1016",
        "console_text": "#c3cad8",
        "scroll": "#333a4a",
        "scroll_hover": "#424c60",
    },
    "light": {
        "window": "#f3f4f8",
        "card": "#ffffff",
        "card_alt": "#f6f7fa",
        "input": "#ffffff",
        "btn": "#eef0f4",
        "btn_hover": "#e6e9f0",
        "btn_pressed": "#dcdfe8",
        "menu_hover": "#eceef4",
        "border": "#d9dde6",
        "text": "#212838",
        "text_dim": "#667085",
        "text_disabled": "#a6adbc",
        "accent": "#2f6fdb",
        "accent_hover": "#4479e0",
        "accent_pressed": "#2358b8",
        "sel": "#d9e6fb",
        "sel_text": "#212838",
        "tab_active": "#e5e9f1",
        "primary_disabled_bg": "#c9d8f3",
        "primary_disabled_text": "#ffffff",
        "danger": "#d64545",
        "danger_soft": "#fbecec",
        "rate_dim": "#8a919e",
        "rate_ok": "#1e9e50",
        "rate_warn": "#b97a12",
        "rate_high": "#d64545",
        "console_bg": "#f9fafc",
        "console_text": "#2a3142",
        "scroll": "#c9cfda",
        "scroll_hover": "#b2bac8",
    },
}

# $(token)-style template so QSS braces never collide with formatting.
_QSS = Template(
    """
QWidget {
    background: $window;
    font-size: 13px;
    selection-background-color: $sel;
    selection-color: $sel_text;
}
QLabel, QCheckBox, QRadioButton { background: transparent; }
QWidget#panel { background: transparent; }

QLabel#statCaption { color: $text_dim; font-size: 11px; background: transparent; }
QLabel#statValue { font-size: 20px; font-weight: 700; background: transparent; }
QLabel#strong { font-weight: 600; background: transparent; }
QLabel#hint {
    color: $text_dim; font-size: 12px; background: transparent;
    font-family: "Consolas", "Menlo", monospace;
}

QFrame#statTile { background: $card; border: 1px solid $border; border-radius: 10px; }

QGroupBox {
    background: $card;
    border: 1px solid $border;
    border-radius: 10px;
    margin-top: 12px;
    padding: 8px 10px 10px 10px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px; top: 4px;
    padding: 0 4px;
    color: $text_dim;
    font-size: 12px;
}

QPushButton {
    background: $btn;
    border: 1px solid $border;
    border-radius: 6px;
    padding: 5px 14px;
    min-height: 18px;
}
QPushButton:hover { background: $btn_hover; border-color: $accent; }
QPushButton:pressed { background: $btn_pressed; }
QPushButton:disabled { background: $window; color: $text_disabled; border-color: $border; }

QPushButton#primary {
    background: $accent; border: 1px solid $accent; font-weight: 600;
}
QPushButton#primary:hover { background: $accent_hover; border-color: $accent_hover; }
QPushButton#primary:pressed { background: $accent_pressed; }
QPushButton#primary:disabled {
    background: $primary_disabled_bg; border-color: $primary_disabled_bg;
}

QPushButton#danger {
    background: transparent; border: 1px solid $danger; font-weight: 600;
}
QPushButton#danger:hover { background: $danger_soft; }
QPushButton#danger:pressed { background: $danger_soft; }
QPushButton#danger:disabled {
    background: transparent; border-color: $border; color: $text_disabled;
}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background: $input;
    border: 1px solid $border;
    border-radius: 6px;
    padding: 4px 8px;
    min-height: 16px;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border-color: $accent;
}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {
    background: $window; color: $text_disabled;
}
QComboBox QLineEdit { background: transparent; border: none; padding: 0 2px; }
QComboBox QAbstractItemView {
    background: $card;
    border: 1px solid $border;
    border-radius: 6px;
    selection-background-color: $sel;
    selection-color: $sel_text;
    outline: none;
}
QComboBox QAbstractItemView::item { min-height: 26px; padding: 2px 6px; }
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
    width: 0px; border: none; background: transparent;
}

QCheckBox { spacing: 7px; }
QCheckBox::indicator {
    width: 16px; height: 16px;
    border: 1px solid $border;
    border-radius: 4px;
    background: $input;
}
QCheckBox::indicator:hover { border-color: $accent; }
QCheckBox::indicator:checked { background: $accent; border-color: $accent; }
QCheckBox::indicator:disabled { background: $window; border-color: $border; }

QTableWidget {
    background: $input;
    alternate-background-color: $card_alt;
    border: none;
    border-radius: 8px;
}
QTableWidget::item { padding: 4px 8px; }
QTableWidget::item:selected { background: $sel; color: $sel_text; }
QHeaderView::section {
    background: $card;
    color: $text_dim;
    border: none;
    border-bottom: 1px solid $border;
    padding: 7px 8px;
    font-weight: 600;
}
QTableCornerButton::section { background: $card; border: none; }

QTabWidget::pane { border: none; background: transparent; }
QTabBar { background: transparent; }
QTabBar::tab {
    background: transparent;
    color: $text_dim;
    padding: 7px 18px;
    margin-right: 6px;
    border-radius: 7px;
    font-weight: 600;
}
QTabBar::tab:hover { color: $text; }
QTabBar::tab:selected { background: $tab_active; color: $text; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: $scroll; border-radius: 4px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: $scroll_hover; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: $scroll; border-radius: 4px; min-width: 24px; }
QScrollBar::handle:horizontal:hover { background: $scroll_hover; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QMenuBar { background: $window; border-bottom: 1px solid $border; padding: 3px 6px; }
QMenuBar::item { background: transparent; padding: 5px 10px; border-radius: 5px; }
QMenuBar::item:selected { background: $menu_hover; }
QMenu { background: $card; border: 1px solid $border; border-radius: 8px; padding: 6px; }
QMenu::item { background: transparent; padding: 6px 26px 6px 14px; border-radius: 5px; }
QMenu::item:selected { background: $sel; color: $sel_text; }
QMenu::item:disabled { color: $text_disabled; }
QMenu::separator { height: 1px; background: $border; margin: 5px 8px; }

QPlainTextEdit#logEdit {
    background: $console_bg;
    border: 1px solid $border;
    border-radius: 8px;
    padding: 6px;
    font-family: "Consolas", "Menlo", "DejaVu Sans Mono", monospace;
    font-size: 12px;
    selection-background-color: $sel;
    selection-color: $sel_text;
}

QProgressBar {
    background: $input;
    border: none;
    border-radius: 5px;
    min-height: 10px; max-height: 10px;
    text-align: center;
}
QProgressBar::chunk { background: $accent; border-radius: 5px; }

QStatusBar { background: transparent; border-top: 1px solid $border; }
QStatusBar QLabel { background: transparent; }

QSplitter::handle { background: transparent; }
QSplitter::handle:vertical { height: 6px; }
QSplitter::handle:horizontal { width: 6px; }
QSplitter::handle:hover { background: $sel; border-radius: 3px; }

QToolTip {
    background: $card; color: $text;
    border: 1px solid $border; border-radius: 5px;
    padding: 4px 8px;
}
"""
)

_current: str = "dark"
_applied: bool = False


def current_theme() -> str:
    """Name of the theme currently applied to the running app."""
    return _current if _current in PALETTES else "dark"


def get_saved_theme() -> str:
    """Read the persisted theme preference (default: dark)."""
    from .qt_compat import QSettings

    val = QSettings("multicast-tool", "Multicast Test Tool").value("ui/theme", "dark")
    return val if val in PALETTES else "dark"


def save_theme(name: str) -> None:
    """Persist the theme preference."""
    if name not in PALETTES:
        return
    from .qt_compat import QSettings

    s = QSettings("multicast-tool", "Multicast Test Tool")
    s.setValue("ui/theme", name)
    s.sync()


def rate_color(pps: float) -> QColor:
    """Grey/green/amber/red for a live rate, matching the active theme."""
    p = PALETTES[current_theme()]
    if pps <= 0.5:
        return QColor(p["rate_dim"])
    if pps < 50:
        return QColor(p["rate_ok"])
    if pps < 1000:
        return QColor(p["rate_warn"])
    return QColor(p["rate_high"])


def _font() -> QFont:
    if sys.platform.startswith("win"):
        return QFont("Segoe UI", 9)
    return QFont()


def _palette(p: dict[str, str]) -> QPalette:
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(p["window"]))
    pal.setColor(QPalette.WindowText, QColor(p["text"]))
    pal.setColor(QPalette.Base, QColor(p["input"]))
    pal.setColor(QPalette.AlternateBase, QColor(p["card_alt"]))
    pal.setColor(QPalette.Text, QColor(p["text"]))
    pal.setColor(QPalette.Button, QColor(p["card"]))
    pal.setColor(QPalette.ButtonText, QColor(p["text"]))
    pal.setColor(QPalette.ToolTipBase, QColor(p["card"]))
    pal.setColor(QPalette.ToolTipText, QColor(p["text"]))
    pal.setColor(QPalette.Highlight, QColor(p["sel"]))
    pal.setColor(QPalette.HighlightedText, QColor(p["sel_text"]))
    try:  # Qt >= 5.12; keep working if the role is missing
        pal.setColor(QPalette.PlaceholderText, QColor(p["text_dim"]))
    except AttributeError:  # pragma: no cover
        pass
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor(p["text_disabled"]))
    return pal


def build_qss(name: str) -> str:
    """Render the QSS sheet for the given theme name."""
    p = PALETTES.get(name, PALETTES["dark"])
    return _QSS.substitute(p)


def apply_app(app: QApplication, name: str | None = None) -> str:
    """Style ``app`` with the named (or saved) theme. Returns the name used."""
    global _current, _applied
    if name is None:
        name = get_saved_theme()
    p = PALETTES.get(name, PALETTES["dark"])
    _current = name if name in PALETTES else "dark"
    app.setStyle("Fusion")
    app.setFont(_font())
    app.setPalette(_palette(p))
    app.setStyleSheet(build_qss(_current))
    _applied = True
    return _current


def ensure_applied(app: QApplication | None) -> None:
    """Apply the saved theme once per process, if not done already.

    Lets tests / alternative entry points get the themed look without
    each having to know about :func:`apply_app`.
    """
    if app is not None and not _applied:
        apply_app(app)
