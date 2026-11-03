"""Qt theme: palettes, stylesheet, and status colours.

Every colour used by the UI comes from here. Widgets that need a colour in
code (a painted dot, a status pill) ask for it by *role* instead of hard
coding a hex value, and widgets styled by the stylesheet carry a role in a
dynamic property. A theme switch therefore restyles both without every
widget having to be rebuilt, which is what used to leave light-theme text
in a freshly darkened window.

Contrast is part of the palette's contract: body text, muted text, button
labels, and the three message tones all meet WCAG AA (4.5:1) against the
surface they sit on, in both themes. ``tests/test_theme_contrast.py``
checks the numbers so a later colour edit cannot quietly break them.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from musicdlp.core.models import (
    CANCELLED, CONVERTED, CONVERTING, DOWNLOADED, DOWNLOADING, ERROR,
    MISSING, NEEDS_UPDATE, NEW, QUEUED, SYNCED, SYNCING,
)

# (background, foreground) for status pills. Light pills with dark text read
# well on both the light and dark window backgrounds, and every pair below is
# far above 4.5:1, so a pill's text never depends on the theme behind it.
STATUS_COLORS: dict[str, tuple[str, str]] = {
    NEW: ("#e5e7eb", "#374151"),
    QUEUED: ("#fef3c7", "#92400e"),
    DOWNLOADING: ("#dbeafe", "#1e40af"),
    DOWNLOADED: ("#ccfbf1", "#115e59"),
    CONVERTING: ("#ede9fe", "#5b21b6"),
    CONVERTED: ("#dcfce7", "#166534"),
    SYNCING: ("#cffafe", "#155e75"),
    SYNCED: ("#bbf7d0", "#14532d"),
    ERROR: ("#fee2e2", "#991b1b"),
    CANCELLED: ("#e5e7eb", "#4b5563"),
    NEEDS_UPDATE: ("#ffedd5", "#9a3412"),
    MISSING: ("#fee2e2", "#991b1b"),
}

# The single accent used for the one primary action, selection, and focus.
# #2563eb keeps white label text at 5.2:1; the brighter #3b82f6 it replaced
# was only 3.7:1, which is below AA for a button label.
ACCENT = "#2563eb"
ACCENT_HOVER = "#1d4ed8"

DARK = {
    "window": "#1e1f22",
    "panel": "#2b2d31",
    "panel_alt": "#26282b",
    "border": "#3a3d42",
    "text": "#e6e6e6",
    "muted": "#9aa0a6",
    "accent": ACCENT,
    "accent_hover": ACCENT_HOVER,
    "on_accent": "#ffffff",
    # Anchors (rich-text links) sit on the window, not on the accent
    # button, so they need their own lighter tone in dark mode: the
    # button blue is only 3.2:1 there.
    "link": "#60a5fa",
    "danger": "#f87171",
    "warning": "#fbbf24",
    "success": "#4ade80",
    "hover": "#3a3d42",
    "disabled_bg": "#26282b",
}

LIGHT = {
    "window": "#f5f6f8",
    "panel": "#ffffff",
    "panel_alt": "#eef1f5",
    "border": "#d7dbe0",
    "text": "#1f2328",
    "muted": "#5f6670",
    "accent": ACCENT,
    "accent_hover": ACCENT_HOVER,
    "on_accent": "#ffffff",
    "link": "#1d4ed8",
    "danger": "#b91c1c",
    "warning": "#92400e",
    "success": "#15803d",
    "hover": "#eef1f5",
    "disabled_bg": "#e6e8ec",
}

# What the last apply_theme() used, so painted widgets can follow it.
_current = LIGHT


def tokens(theme: str | None = None) -> dict[str, str]:
    """The colour roles for a theme, or for the one now in use."""
    if theme is None:
        return _current
    return DARK if _is_dark(theme) else LIGHT


def tone(name: str) -> str:
    """A semantic colour: ``danger``, ``warning``, ``success`` or ``accent``."""
    return _current.get(name, _current["text"])


def set_tone(widget, name: str) -> None:
    """Give a widget a colour role that follows the theme.

    Styling by role means a theme switch repaints the text with the new
    palette; an inline ``color: #…`` would stay behind in the old theme's
    ink, which is how red error text became unreadable in light mode.
    """
    if widget.property("tone") == name:
        return
    widget.setProperty("tone", name)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def _is_dark(theme: str) -> bool:
    if theme == "dark":
        return True
    if theme == "light":
        return False
    app = QApplication.instance()
    hints = app.styleHints() if app is not None else None
    if hints is not None and hasattr(hints, "colorScheme"):
        try:
            return hints.colorScheme() == Qt.ColorScheme.Dark
        except Exception:
            pass
    return True


def build_palette(theme: str) -> QPalette:
    colors = tokens(theme)
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(colors["window"]))
    palette.setColor(QPalette.WindowText, QColor(colors["text"]))
    palette.setColor(QPalette.Base, QColor(colors["panel"]))
    palette.setColor(QPalette.AlternateBase, QColor(colors["panel_alt"]))
    palette.setColor(QPalette.ToolTipBase, QColor(colors["panel"]))
    palette.setColor(QPalette.ToolTipText, QColor(colors["text"]))
    palette.setColor(QPalette.Text, QColor(colors["text"]))
    palette.setColor(QPalette.Button, QColor(colors["panel"]))
    palette.setColor(QPalette.ButtonText, QColor(colors["text"]))
    palette.setColor(QPalette.BrightText, QColor("#ffffff"))
    palette.setColor(QPalette.Link, QColor(colors["link"]))
    palette.setColor(QPalette.Highlight, QColor(colors["accent"]))
    palette.setColor(QPalette.HighlightedText, QColor(colors["on_accent"]))
    palette.setColor(QPalette.PlaceholderText, QColor(colors["muted"]))
    palette.setColor(QPalette.Mid, QColor(colors["border"]))
    palette.setColor(QPalette.Midlight, QColor(colors["border"]))
    palette.setColor(QPalette.Dark, QColor(colors["border"]))
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor(colors["muted"]))
    palette.setColor(
        QPalette.Disabled, QPalette.WindowText, QColor(colors["muted"])
    )
    palette.setColor(
        QPalette.Disabled, QPalette.ButtonText, QColor(colors["muted"])
    )
    palette.setColor(
        QPalette.Disabled, QPalette.Base, QColor(colors["window"])
    )
    return palette


def build_stylesheet(theme: str) -> str:
    c = tokens(theme)
    return f"""
    QWidget {{ font-size: 13px; }}
    QMainWindow, QDialog {{ background: {c['window']}; }}
    QToolBar {{ border: none; spacing: 6px; }}

    QFrame#panel {{ background: {c['panel']}; border: 1px solid {c['border']};
                    border-radius: 8px; }}

    QLabel[tone="muted"] {{ color: {c['muted']}; }}
    QLabel#muted {{ color: {c['muted']}; }}
    QLabel#mutedSmall {{ color: {c['muted']}; font-size: 11px; }}
    QLabel[tone="danger"] {{ color: {c['danger']}; }}
    QLabel[tone="warning"] {{ color: {c['warning']}; }}
    QLabel[tone="success"] {{ color: {c['success']}; }}
    QLabel[tone="accent"] {{ color: {c['accent']}; }}
    QLineEdit[tone="danger"], QComboBox[tone="danger"],
    QSpinBox[tone="danger"], QPlainTextEdit[tone="danger"] {{
        border: 1px solid {c['danger']};
    }}
    QLabel#sectionTitle {{ font-weight: 700; font-size: 14px; }}
    QLabel#pageTitle {{ font-weight: 700; font-size: 18px; }}
    QLabel#brand {{ font-weight: 700; font-size: 17px; }}

    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {{
        background: {c['panel']}; border: 1px solid {c['border']};
        border-radius: 6px; padding: 5px 8px; min-height: 20px;
        selection-background-color: {c['accent']};
        selection-color: {c['on_accent']};
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus,
    QDoubleSpinBox:focus, QPlainTextEdit:focus {{
        border: 1px solid {c['accent']};
    }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox QAbstractItemView {{
        background: {c['panel']}; border: 1px solid {c['border']};
        selection-background-color: {c['accent']};
        selection-color: {c['on_accent']};
    }}

    QPushButton, QToolButton {{
        background: {c['panel']}; border: 1px solid {c['border']};
        border-radius: 6px; padding: 6px 12px;
    }}
    QPushButton:hover, QToolButton:hover {{ background: {c['hover']}; }}
    QPushButton:focus, QToolButton:focus {{
        border: 1px solid {c['accent']};
    }}
    QPushButton:disabled, QToolButton:disabled {{ color: {c['muted']}; }}
    QPushButton#primary:disabled {{
        background: {c['disabled_bg']};
        border: 1px solid {c['border']};
        color: {c['muted']};
        font-weight: 600;
    }}
    QPushButton#primary {{
        background: {c['accent']}; border: 1px solid {c['accent']};
        color: {c['on_accent']}; font-weight: 600; padding: 7px 16px;
    }}
    QPushButton#primary:hover {{ background: {c['accent_hover']}; }}
    QPushButton#primary:focus {{ border: 1px solid {c['on_accent']}; }}
    QPushButton#danger, QToolButton#danger {{ color: {c['danger']}; }}
    QPushButton#link {{
        background: transparent; border: none; padding: 2px 4px;
        color: {c['accent']};
    }}
    QPushButton#link:hover {{ text-decoration: underline; }}
    QPushButton#quiet, QToolButton#quiet {{
        background: transparent; border: 1px solid transparent;
        color: {c['muted']}; padding: 4px 8px;
    }}
    QPushButton#quiet:hover, QToolButton#quiet:hover {{
        background: {c['hover']}; color: {c['text']};
    }}

    QTableView {{
        background: {c['panel']}; alternate-background-color: {c['panel_alt']};
        border: 1px solid {c['border']}; border-radius: 8px;
        selection-background-color: {c['accent']};
        selection-color: {c['on_accent']};
        gridline-color: {c['border']};
    }}
    QTableView::item {{ padding: 4px; }}
    QHeaderView::section {{
        background: {c['panel']}; border: none;
        border-bottom: 1px solid {c['border']};
        padding: 7px 6px; font-weight: 600;
    }}
    QHeaderView::section:hover {{ background: {c['hover']}; }}
    QListView {{
        background: {c['panel']}; border: 1px solid {c['border']};
        border-radius: 8px; outline: none;
    }}
    QListView::item {{ border-radius: 6px; }}

    QTabWidget::pane {{
        border: 1px solid {c['border']}; border-radius: 8px;
        top: -1px;
    }}
    QTabBar::tab {{
        padding: 7px 14px; border: 1px solid transparent;
        border-bottom: 2px solid transparent; margin-right: 2px;
    }}
    QTabBar::tab:selected {{
        border-bottom: 2px solid {c['accent']};
        font-weight: 600;
    }}
    QTabBar::tab:hover {{ color: {c['accent']}; }}

    QSplitter::handle {{ background: transparent; }}
    QSplitter::handle:horizontal {{ width: 8px; }}
    QStatusBar {{ background: {c['panel']}; border-top: 1px solid {c['border']}; }}

    QToolTip {{
        background: {c['panel']}; color: {c['text']};
        border: 1px solid {c['border']}; padding: 4px;
    }}

    QProgressBar {{
        background: {c['panel_alt']}; border: none; border-radius: 5px;
    }}
    QProgressBar::chunk {{
        background: {c['accent']}; border-radius: 5px;
    }}

    QScrollBar:vertical {{
        background: transparent; width: 10px; margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {c['border']}; border-radius: 4px; min-height: 24px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {c['muted']}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0; border: none;
    }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: transparent;
    }}
    QScrollBar:horizontal {{
        background: transparent; height: 10px; margin: 2px;
    }}
    QScrollBar::handle:horizontal {{
        background: {c['border']}; border-radius: 4px; min-width: 24px;
    }}
    QScrollBar::handle:horizontal:hover {{ background: {c['muted']}; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0; border: none;
    }}
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
        background: transparent;
    }}

    QLabel#stepCircle[state="waiting"] {{
        background: {c['panel']}; color: {c['muted']};
        border: 1px solid {c['border']};
    }}
    QLabel#stepCircle[state="active"] {{
        background: {c['accent']}; color: {c['on_accent']};
        border: 1px solid {c['accent']};
    }}
    QLabel#stepCircle[state="done"] {{
        background: {c['success']}; color: {c['window']};
        border: 1px solid {c['success']};
    }}
    QLabel#stepCircle[state="error"] {{
        background: {c['danger']}; color: {c['window']};
        border: 1px solid {c['danger']};
    }}
    """


def apply_theme(app: QApplication, theme: str) -> None:
    global _current
    _current = tokens(theme)
    app.setStyle("Fusion")
    app.setPalette(build_palette(theme))
    app.setStyleSheet(build_stylesheet(theme))
