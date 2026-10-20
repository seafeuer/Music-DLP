"""Custom painting for status pills and per-row progress bars."""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from musicdlp.core.models import display_status, status_label
from musicdlp.ui.models.track_model import PROGRESS_ROLE, ROW_ROLE
from musicdlp.ui.theme import ACCENT, STATUS_COLORS


class StatusDelegate(QStyledItemDelegate):
    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        return QSize(0, 32)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        row = index.data(ROW_ROLE)
        if row is None:
            super().paint(painter, option, index)
            return

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())

        status = display_status(row.track)
        background, foreground = STATUS_COLORS.get(
            status, ("#e5e7eb", "#374151")
        )
        pill = option.rect.adjusted(6, 7, -6, -7)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(background))
        painter.drawRoundedRect(pill, 9, 9)

        font = QFont(option.font)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(foreground))
        text = status_label(status)
        text = QFontMetrics(font).elidedText(text, Qt.ElideRight, pill.width() - 16)
        painter.drawText(
            pill.adjusted(8, 0, -8, 0),
            int(Qt.AlignVCenter | Qt.AlignLeft),
            text,
        )
        painter.restore()


class ProgressDelegate(QStyledItemDelegate):
    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        return QSize(0, 32)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        percent = index.data(PROGRESS_ROLE)

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())

        if percent is None:
            painter.setPen(option.palette.placeholderText().color())
            painter.drawText(option.rect, int(Qt.AlignCenter), "–")
            painter.restore()
            return

        percent = max(0.0, min(100.0, float(percent)))
        track = option.rect.adjusted(8, 10, -8, -10)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(128, 128, 128, 55))
        painter.drawRoundedRect(QRectF(track), 4, 4)

        if percent > 0:
            fill = QRectF(track)
            fill.setWidth(track.width() * percent / 100.0)
            painter.setBrush(QColor(ACCENT))
            painter.drawRoundedRect(fill, 4, 4)

        text_color = QColor("#ffffff") if percent >= 45 else option.palette.text().color()
        painter.setPen(text_color)
        font = QFont(option.font)
        font.setPointSizeF(max(7.5, font.pointSizeF() - 1))
        painter.setFont(font)
        painter.drawText(
            option.rect,
            int(Qt.AlignCenter),
            f"{percent:.0f}%",
        )
        painter.restore()
