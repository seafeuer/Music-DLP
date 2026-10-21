"""Qt theme: palettes, stylesheet, and status colors."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from musicdlp.core.models import (
    CANCELLED, CONVERTED, CONVERTING, DOWNLOADED, DOWNLOADING, ERROR,
    MISSING, NEEDS_UPDATE, NEW, QUEUED, SYNCED, SYNCING,
)

# (background, foreground) for status pills. Light pills with dark text read
# well on both the light and dark window backgrounds.
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

ACCENT = "#3b82f6"
ACCENT_HOVER = "#2563eb"
DANGER = "#dc2626"


def _is_dark(theme: str) -> bool:
    if theme == "dark":
        return True
    if theme == "light":
        return False
    hints = QApplication.instance().styleHints() if QApplication.instance() else None
    if hints is not None and hasattr(hints, "colorScheme"):
        try:
            return hints.colorScheme() == Qt.ColorScheme.Dark
        except Exception:
            pass
    return True


def build_palette(theme: str) -> QPalette:
    dark = _is_dark(theme)
    palette = QPalette()
    if dark:
        window = QColor("#1e1f22")
        base = QColor("#2b2d31")
        alt = QColor("#26282b")
        text = QColor("#e6e6e6")
        muted = QColor("#9aa0a6")
        button = QColor("#2f3136")
        highlight = QColor(ACCENT)
        highlight_text = QColor("#ffffff")
    else:
        window = QColor("#f5f6f8")
        base = QColor("#ffffff")
        alt = QColor("#eef1f5")
        text = QColor("#1f2328")
        muted = QColor("#5f6670")
        button = QColor("#e9ecef")
        highlight = QColor("#2563eb")
        highlight_text = QColor("#ffffff")

    palette.setColor(QPalette.Window, window)
    palette.setColor(QPalette.WindowText, text)
    palette.setColor(QPalette.Base, base)
    palette.setColor(QPalette.AlternateBase, alt)
    palette.setColor(QPalette.ToolTipBase, base)
    palette.setColor(QPalette.ToolTipText, text)
    palette.setColor(QPalette.Text, text)
    palette.setColor(QPalette.Button, button)
    palette.setColor(QPalette.ButtonText, text)
    palette.setColor(QPalette.BrightText, QColor("#ffffff"))
    palette.setColor(QPalette.Link, highlight)
    palette.setColor(QPalette.Highlight, highlight)
    palette.setColor(QPalette.HighlightedText, highlight_text)
    palette.setColor(QPalette.PlaceholderText, muted)
    palette.setColor(QPalette.Disabled, QPalette.Text, muted)
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, muted)
    return palette


def build_stylesheet(theme: str) -> str:
    dark = _is_dark(theme)
    bg = "#1e1f22" if dark else "#f5f6f8"
    panel = "#2b2d31" if dark else "#ffffff"
    border = "#3a3d42" if dark else "#d7dbe0"
    muted = "#9aa0a6" if dark else "#5f6670"
    hover = "#3a3d42" if dark else "#eef1f5"
    checkbox_bg = "#3a3d42" if dark else "#ffffff"
    checkbox_border = "#5c6066" if dark else "#9aa1a9"
    disabled_bg = "#2a2c30" if dark else "#e6e8ec"

    return f"""
    QWidget {{ font-size: 13px; }}
    QMainWindow, QDialog {{ background: {bg}; }}
    QToolBar {{ border: none; spacing: 6px; }}
    QFrame#panel {{ background: {panel}; border: 1px solid {border};
                    border-radius: 8px; }}
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
        background: {panel}; border: 1px solid {border};
        border-radius: 6px; padding: 5px 8px; min-height: 20px;
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
        border: 1px solid {ACCENT};
    }}
    QPushButton {{
        background: {panel}; border: 1px solid {border};
        border-radius: 6px; padding: 6px 12px;
    }}
    QPushButton:hover {{ background: {hover}; }}
    QPushButton:disabled {{ color: {muted}; }}
    QPushButton#primary:disabled {{
        background: {disabled_bg};
        border: 1px solid {border};
        color: {muted};
        font-weight: 600;
    }}
    QPushButton#primary {{
        background: {ACCENT}; border: 1px solid {ACCENT};
        color: white; font-weight: 600; padding: 7px 16px;
    }}
    QPushButton#primary:hover {{ background: {ACCENT_HOVER}; }}
    QPushButton#danger {{ color: {DANGER}; }}
    QTableView {{
        background: {panel}; alternate-background-color: {bg};
        border: 1px solid {border}; border-radius: 8px;
        selection-background-color: {ACCENT}; selection-color: white;
    }}
    QTableView::item {{ padding: 4px; }}
    QTableView::indicator, QListView::indicator {{
        width: 15px; height: 15px; border-radius: 3px;
        border: 1px solid {checkbox_border}; background: {checkbox_bg};
    }}
    QTableView::indicator:checked, QListView::indicator:checked {{
        background: {ACCENT}; border: 1px solid {ACCENT};
        image: none;
    }}
    QTableView::indicator:checked:hover, QListView::indicator:checked:hover {{
        background: {ACCENT_HOVER};
    }}
    QCheckBox::indicator {{
        width: 15px; height: 15px; border-radius: 3px;
        border: 1px solid {checkbox_border}; background: {checkbox_bg};
    }}
    QCheckBox::indicator:checked {{
        background: {ACCENT}; border: 1px solid {ACCENT};
    }}
    QCheckBox::indicator:disabled {{
        border: 1px solid {border}; background: {disabled_bg};
    }}
    QHeaderView::section {{
        background: {panel}; border: none; border-bottom: 1px solid {border};
        padding: 7px 6px; font-weight: 600;
    }}
    QListView {{
        background: {panel}; border: 1px solid {border};
        border-radius: 8px; outline: none;
    }}
    QListView::item {{ border-radius: 6px; }}
    QTabWidget::pane {{ border: 1px solid {border}; border-radius: 8px; }}
    QTabBar::tab {{ padding: 7px 14px; }}
    QPlainTextEdit, QTextEdit {{
        background: {panel}; border: 1px solid {border};
        border-radius: 8px;
    }}
    QSplitter::handle {{ background: transparent; }}
    QStatusBar {{ background: {panel}; border-top: 1px solid {border}; }}
    QLabel#muted {{ color: {muted}; }}
    QLabel#mutedSmall {{ color: {muted}; font-size: 11px; }}
    QToolTip {{ background: {panel}; color: {muted};
                border: 1px solid {border}; padding: 4px; }}
    """


def apply_theme(app: QApplication, theme: str) -> None:
    app.setStyle("Fusion")
    app.setPalette(build_palette(theme))
    app.setStyleSheet(build_stylesheet(theme))
