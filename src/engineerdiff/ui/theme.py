"""EngineerDiff Fluent 2 设计令牌、浅色/深色主题与样式表。"""

from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass
from typing import Callable

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette


@dataclass(frozen=True)
class Palette:
    name: str
    dark: bool
    app_bg: str
    page_bg: str
    card_bg: str
    secondary_surface: str
    hover_surface: str
    pressed_surface: str
    divider: str
    border_strong: str
    text: str
    text_secondary: str
    text_disabled: str
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_soft: str
    diff_red: str
    diff_red_bg: str
    diff_red_text: str
    warning: str
    warning_bg: str
    warning_text: str
    success: str
    success_bg: str
    placeholder_bg: str
    gutter_bg: str
    gutter_text: str
    scrollbar: str
    scrollbar_hover: str
    overlay: str


LIGHT = Palette(
    name="light", dark=False,
    app_bg="#F3F3F3", page_bg="#F9F9F9", card_bg="#FFFFFF",
    secondary_surface="#FAFAFA", hover_surface="#EDEDED", pressed_surface="#E3E3E3",
    divider="#E0E0E0", border_strong="#C8C8C8",
    text="#1B1B1B", text_secondary="#616161", text_disabled="#9A9A9A",
    accent="#0067C0", accent_hover="#1975C5", accent_pressed="#005A9E",
    accent_soft="#E5F1FB",
    diff_red="#C62828", diff_red_bg="#FFD7D5", diff_red_text="#A11212",
    warning="#E65100", warning_bg="#FFE2B8", warning_text="#8A3B00",
    success="#0F7B0F", success_bg="#DFF6DD",
    placeholder_bg="#F7F7F7", gutter_bg="#FAFAFA", gutter_text="#8A8A8A",
    scrollbar="#C8C8C8", scrollbar_hover="#A6A6A6", overlay="#FFFFFF",
)

DARK = Palette(
    name="dark", dark=True,
    app_bg="#202020", page_bg="#272727", card_bg="#2D2D2D",
    secondary_surface="#323232", hover_surface="#3A3A3A", pressed_surface="#454545",
    divider="#3D3D3D", border_strong="#5A5A5A",
    text="#FFFFFF", text_secondary="#C5C5C5", text_disabled="#7A7A7A",
    accent="#4CC2FF", accent_hover="#6FD0FF", accent_pressed="#3AA9E0",
    accent_soft="#1F3A50",
    diff_red="#FF99A4", diff_red_bg="#4A2426", diff_red_text="#FFB3BA",
    warning="#FCE100", warning_bg="#433519", warning_text="#FCE100",
    success="#6CCB5F", success_bg="#1E3A20",
    placeholder_bg="#2A2A2A", gutter_bg="#2A2A2A", gutter_text="#9A9A9A",
    scrollbar="#5A5A5A", scrollbar_hover="#7A7A7A", overlay="#2D2D2D",
)

_ACTIVE: Palette = LIGHT
_MODE = "system"
_LISTENERS: list[Callable[[Palette], None]] = []


def palette() -> Palette:
    return _ACTIVE


def mode() -> str:
    return _MODE


def system_is_dark() -> bool:
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication

        hints = QGuiApplication.styleHints()
        scheme = hints.colorScheme()
        if scheme == Qt.ColorScheme.Dark:
            return True
        if scheme == Qt.ColorScheme.Light:
            return False
    except Exception:
        pass
    return False


def resolve(mode_name: str | None = None) -> Palette:
    m = mode_name or _MODE
    if m == "dark":
        return DARK
    if m == "light":
        return LIGHT
    return DARK if system_is_dark() else LIGHT


def register_listener(cb: Callable[[Palette], None]) -> None:
    if cb not in _LISTENERS:
        _LISTENERS.append(cb)


def unregister_listener(cb: Callable[[Palette], None]) -> None:
    try:
        _LISTENERS.remove(cb)
    except ValueError:
        pass


def _set_aliases(p: Palette) -> None:
    g = globals()
    for field, name in (
        ("app_bg", "APP_BG"), ("page_bg", "PAGE_BG"), ("card_bg", "PANEL_BG"),
        ("secondary_surface", "SECONDARY_SURFACE"), ("hover_surface", "HOVER_BG"),
        ("pressed_surface", "PRESSED_BG"), ("divider", "BORDER"),
        ("border_strong", "BORDER_STRONG"), ("text", "TEXT"),
        ("text_secondary", "TEXT_SECONDARY"), ("text_disabled", "TEXT_DISABLED"),
        ("accent", "ACCENT"), ("accent_hover", "ACCENT_HOVER"),
        ("accent_pressed", "ACCENT_PRESS"), ("accent_soft", "ACCENT_SOFT"),
        ("diff_red", "RED"), ("diff_red_bg", "RED_BG"),
        ("diff_red_text", "RED_TEXT"), ("diff_red_bg", "ADDED_BG"),
        ("diff_red_bg", "REMOVED_BG"), ("warning", "ORANGE"),
        ("warning_bg", "ORANGE_BG"), ("warning_text", "ORANGE_TEXT"),
        ("accent", "CURRENT_BLUE"), ("accent_soft", "CURRENT_BG"),
        ("placeholder_bg", "PLACEHOLDER_BG"), ("gutter_bg", "GUTTER_BG"),
        ("gutter_text", "GUTTER_TEXT"), ("success", "SUCCESS"),
        ("warning", "WARNING"),
    ):
        g[name] = getattr(p, field)
    g["DIFF_CONFIRMED_BG"] = QColor(p.diff_red_bg)
    g["DIFF_UNCERTAIN_BG"] = QColor(p.warning_bg)
    g["DIFF_CONFIRMED_BORDER"] = QColor(p.diff_red)
    g["DIFF_UNCERTAIN_BORDER"] = QColor(p.warning)


_set_aliases(LIGHT)


def stylesheet(p: Palette) -> str:
    return f"""
QMainWindow, QDialog {{ background: {p.app_bg}; }}
QWidget {{ color: {p.text}; }}
QToolTip {{ color:{p.text}; background:{p.card_bg}; border:1px solid {p.divider};
  border-radius:4px; padding:6px 8px; }}
QGroupBox {{ border:1px solid {p.divider}; border-radius:8px; margin-top:14px;
  background:{p.card_bg}; padding:10px 12px 8px 12px; }}
QGroupBox::title {{ subcontrol-origin:margin; left:12px; padding:0 6px;
  color:{p.text}; font-weight:600; }}
QPushButton {{ background:{p.secondary_surface}; border:1px solid {p.divider};
  border-radius:4px; padding:6px 14px; min-height:20px; color:{p.text}; }}
QPushButton:hover {{ background:{p.hover_surface}; border-color:{p.border_strong}; }}
QPushButton:pressed {{ background:{p.pressed_surface}; border-color:{p.border_strong}; }}
QPushButton:disabled {{ color:{p.text_disabled}; background:{p.app_bg};
  border-color:{p.divider}; }}
QPushButton#primary {{ background:{p.accent}; color:white; border:1px solid {p.accent};
  font-weight:600; padding:7px 20px; }}
QPushButton#primary:hover {{ background:{p.accent_hover}; border-color:{p.accent_hover}; }}
QPushButton#primary:pressed {{ background:{p.accent_pressed}; border-color:{p.accent_pressed}; }}
QPushButton#primary:disabled {{ background:{p.hover_surface}; border-color:{p.divider};
  color:{p.text_disabled}; }}
QPushButton#tool {{ padding:5px 9px; min-height:18px; background:transparent;
  border-color:transparent; }}
QPushButton#tool:hover {{ background:{p.hover_surface}; border-color:{p.divider}; }}
QPushButton#tool:pressed {{ background:{p.pressed_surface}; }}
QPushButton#tool:checked {{ background:{p.accent_soft}; border-color:{p.accent};
  color:{p.text}; }}
QLineEdit {{ background:{p.secondary_surface}; border:1px solid {p.divider};
  border-radius:4px; padding:6px 10px; color:{p.text};
  selection-background-color:{p.accent}; selection-color:white; }}
QLineEdit:hover {{ background:{p.card_bg}; border-color:{p.border_strong}; }}
QLineEdit:focus {{ background:{p.card_bg}; border:1px solid {p.accent}; }}
QCheckBox {{ spacing:7px; }}
QCheckBox:disabled {{ color:{p.text_disabled}; }}
QComboBox {{ background:{p.secondary_surface}; border:1px solid {p.divider};
  border-radius:4px; padding:5px 10px; color:{p.text}; }}
QComboBox:hover {{ background:{p.card_bg}; border-color:{p.border_strong}; }}
QComboBox:focus {{ border-color:{p.accent}; background:{p.card_bg}; }}
QComboBox QAbstractItemView {{ background:{p.card_bg}; border:1px solid {p.divider};
  border-radius:6px; selection-background-color:{p.accent_soft}; color:{p.text}; padding:4px; }}
QSpinBox, QDoubleSpinBox {{ background:{p.secondary_surface}; border:1px solid {p.divider};
  border-radius:4px; padding:5px 7px; min-height:20px; color:{p.text}; }}
QSpinBox:focus, QDoubleSpinBox:focus {{ border-color:{p.accent}; background:{p.card_bg}; }}
QStatusBar {{ background:{p.secondary_surface}; border-top:1px solid {p.divider}; }}
QStatusBar QLabel {{ color:{p.text_secondary}; padding:0 6px; }}
QToolBar {{ background:{p.card_bg}; border:none; spacing:4px; }}
QSplitter::handle {{ background:{p.divider}; width:4px; }}
QSplitter::handle:hover {{ background:{p.accent}; }}
QTabBar::tab {{ background:{p.secondary_surface}; color:{p.text_secondary};
  border:1px solid {p.divider}; border-bottom:none; padding:6px 15px; margin-right:2px;
  border-top-left-radius:6px; border-top-right-radius:6px; }}
QTabBar::tab:selected {{ background:{p.card_bg}; color:{p.text};
  border-bottom:2px solid {p.accent}; }}
QTabBar::tab:hover {{ background:{p.hover_surface}; }}
QTabWidget::pane {{ border:1px solid {p.divider}; background:{p.card_bg}; border-radius:6px; }}
QMenu {{ background:{p.card_bg}; border:1px solid {p.divider}; border-radius:8px; padding:5px; }}
QMenu::item {{ padding:6px 28px 6px 18px; border-radius:4px; color:{p.text}; }}
QMenu::item:selected {{ background:{p.accent_soft}; }}
QMenu::separator {{ height:1px; background:{p.divider}; margin:4px 8px; }}
QTableWidget, QTreeWidget, QTableView, QTreeView {{ background:{p.card_bg};
  alternate-background-color:{p.secondary_surface}; color:{p.text};
  border:1px solid {p.divider}; gridline-color:{p.divider};
  selection-background-color:{p.accent_soft}; selection-color:{p.text}; }}
QHeaderView::section {{ background:{p.secondary_surface}; color:{p.text_secondary};
  border:none; border-bottom:1px solid {p.divider}; border-right:1px solid {p.divider};
  padding:6px 8px; font-weight:600; }}
QScrollBar:vertical {{ background:{p.app_bg}; width:11px; margin:0; }}
QScrollBar::handle:vertical {{ background:{p.scrollbar}; min-height:30px;
  border-radius:4px; margin:2px; }}
QScrollBar::handle:vertical:hover {{ background:{p.scrollbar_hover}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background:transparent; }}
QScrollBar:horizontal {{ background:{p.app_bg}; height:11px; margin:0; }}
QScrollBar::handle:horizontal {{ background:{p.scrollbar}; min-width:30px;
  border-radius:4px; margin:2px; }}
QScrollBar::handle:horizontal:hover {{ background:{p.scrollbar_hover}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width:0; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background:transparent; }}
QProgressBar {{ background:{p.hover_surface}; border:none; border-radius:3px;
  height:6px; text-align:center; color:transparent; }}
QProgressBar::chunk {{ background:{p.accent}; border-radius:3px; }}
QListWidget {{ background:{p.card_bg}; border:1px solid {p.divider};
  border-radius:6px; padding:4px; color:{p.text}; }}
QListWidget::item {{ padding:5px 6px; border-radius:4px; }}
QListWidget::item:hover {{ background:{p.hover_surface}; }}
QListWidget::item:selected {{ background:{p.accent_soft}; color:{p.text}; }}
QLabel#brandTitle {{ color:{p.text}; font-size:15pt; font-weight:600; }}
QLabel#brandSubtitle {{ color:{p.text_secondary}; font-size:9.5pt; }}
QLabel#brandAuthor {{ color:{p.text_secondary}; font-size:8.5pt; padding-left:2px; }}
QLabel#pageTitle {{ color:{p.text}; font-size:18pt; font-weight:600; }}
QLabel#sectionTitle {{ color:{p.text}; font-weight:600; }}
QLabel#subtle {{ color:{p.text_secondary}; }}
QWidget#appHeader {{ background:transparent; border-bottom:1px solid {p.divider}; }}
QTabBar#modeNav::tab {{
    background:transparent; color:{p.text_secondary}; border:none;
    border-bottom:2px solid transparent; padding:7px 14px; margin-right:2px;
    border-radius:4px;
}}
QTabBar#modeNav::tab:selected {{
    background:{p.accent_soft}; color:{p.text}; border-bottom:2px solid {p.accent};
}}
QTabBar#modeNav::tab:hover {{ background:{p.hover_surface}; }}
"""


APP_STYLESHEET = stylesheet(LIGHT)


def apply_theme(mode_name: str | None = None, app=None) -> Palette:
    global _ACTIVE, _MODE
    if mode_name is not None:
        _MODE = mode_name
    _ACTIVE = resolve(_MODE)
    _set_aliases(_ACTIVE)
    if app is None:
        try:
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance()
        except Exception:
            app = None
    if app is not None:
        app.setStyleSheet(stylesheet(_ACTIVE))
        pal = app.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(_ACTIVE.app_bg))
        pal.setColor(QPalette.ColorRole.WindowText, QColor(_ACTIVE.text))
        pal.setColor(QPalette.ColorRole.Base, QColor(_ACTIVE.card_bg))
        pal.setColor(QPalette.ColorRole.AlternateBase, QColor(_ACTIVE.secondary_surface))
        pal.setColor(QPalette.ColorRole.Text, QColor(_ACTIVE.text))
        pal.setColor(QPalette.ColorRole.Button, QColor(_ACTIVE.secondary_surface))
        pal.setColor(QPalette.ColorRole.ButtonText, QColor(_ACTIVE.text))
        pal.setColor(QPalette.ColorRole.Highlight, QColor(_ACTIVE.accent))
        pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF" if not _ACTIVE.dark else "#101010"))
        app.setPalette(pal)
    for cb in list(_LISTENERS):
        try:
            cb(_ACTIVE)
        except Exception:
            pass
    return _ACTIVE


def set_native_title(window, text: str) -> None:
    """Windows: 将原生标题栏文本精确设置为 ``text``，避免 Qt 追加应用显示名。"""
    if sys.platform != "win32":
        return
    try:
        hwnd = int(window.winId())
        ctypes.windll.user32.SetWindowTextW(hwnd, str(text))
    except Exception:
        pass


def apply_dark_title_bar(window) -> None:
    """Windows 10/11: 让原生标题栏跟随深色主题；失败时静默忽略。"""
    if sys.platform != "win32":
        return
    try:
        hwnd = int(window.winId())
        value = ctypes.c_int(1 if palette().dark else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
    except Exception:
        pass


FONT_FAMILIES = ["Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI",
                 "Microsoft YaHei", "Noto Sans CJK SC", "Noto Sans SC", "WenQuanYi Micro Hei"]
MONO_FAMILIES = ["Cascadia Mono", "Consolas", "Noto Sans Mono CJK SC",
                 "DejaVu Sans Mono", "Courier New"]


def ui_font(point_size: float = 9.5) -> QFont:
    f = QFont(); f.setFamilies(FONT_FAMILIES); f.setPointSizeF(point_size); return f


def mono_font(point_size: float = 10.0) -> QFont:
    f = QFont(); f.setFamilies(MONO_FAMILIES); f.setStyleHint(QFont.StyleHint.Monospace)
    f.setFixedPitch(True); f.setPointSizeF(point_size); return f


def header_font(point_size: float = 18.0) -> QFont:
    f = ui_font(point_size); f.setWeight(QFont.Weight.DemiBold); return f


def install_test_font(path: str) -> str:
    fid = QFontDatabase.addApplicationFont(path)
    return QFontDatabase.applicationFontFamilies(fid)[0] if fid >= 0 else ""
