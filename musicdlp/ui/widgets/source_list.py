"""Source (playlist/link) list model, delegate, and widget."""

from __future__ import annotations

from typing import Iterable, Optional

from PySide6.QtCore import QAbstractListModel, QModelIndex, QSize, QSortFilterProxyModel, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QLineEdit, QListView, QMenu,
    QPushButton, QStyle, QStyledItemDelegate, QStyleOptionViewItem,
    QVBoxLayout, QWidget,
)

from musicdlp.core.models import (
    CONVERTED, DOWNLOADED, ERROR, SYNCED, Source,
)

COUNT_ROLE = Qt.UserRole + 1


def _downloaded_count(counts: dict[str, int]) -> int:
    return sum(
        counts.get(status, 0)
        for status in (DOWNLOADED, CONVERTED, SYNCED)
    )


class SourceListModel(QAbstractListModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.rows: list[tuple[Source, dict[str, int]]] = []
        self.duplicate_titles: set[str] = set()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self.rows)):
            return None
        source, counts = self.rows[index.row()]
        if role == Qt.DisplayRole:
            return source.title
        if role == COUNT_ROLE:
            return counts
        if role == Qt.ToolTipRole:
            return f"{source.title}\n{source.url}"
        return None

    def set_sources(
        self,
        sources: Iterable[Source],
        counts: dict[int, dict[str, int]],
    ) -> None:
        self.beginResetModel()
        self.rows = [(source, counts.get(source.id, {})) for source in sources]
        # Two links can carry the same text (for example two tweets from the
        # same account). Remember which titles repeat so the list can tell
        # them apart.
        seen: dict[str, int] = {}
        for source, _counts in self.rows:
            seen[source.title] = seen.get(source.title, 0) + 1
        self.duplicate_titles = {title for title, n in seen.items() if n > 1}
        self.endResetModel()

    def source_at(self, row: int) -> Optional[Source]:
        if 0 <= row < len(self.rows):
            return self.rows[row][0]
        return None

    def counts_at(self, row: int) -> dict[str, int]:
        if 0 <= row < len(self.rows):
            return self.rows[row][1]
        return {}

    def row_for_id(self, source_id: int) -> Optional[int]:
        for row, (source, _counts) in enumerate(self.rows):
            if source.id == source_id:
                return row
        return None


class SourceFilterProxy(QSortFilterProxyModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.search = ""

    def set_search(self, text: str) -> None:
        self.beginFilterChange()
        self.search = (text or "").strip().lower()
        self.endFilterChange()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        model = self.sourceModel()
        if model is None or not self.search:
            return True
        if not (0 <= source_row < len(model.rows)):
            return True
        source, _counts = model.rows[source_row]
        haystack = f"{source.title} {source.uploader or ''}".lower()
        return self.search in haystack


class SourceDelegate(QStyledItemDelegate):
    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        return QSize(0, 58)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        model = index.model()
        if model is None:
            return
        source_row = index.row()
        source_model = model
        while hasattr(source_model, "sourceModel") and source_model.sourceModel():
            source_row = source_model.mapToSource(index).row()
            source_model = source_model.sourceModel()
        if not hasattr(source_model, "rows") or not (0 <= source_row < len(source_model.rows)):
            return
        source, counts = source_model.rows[source_row]

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = option.rect.adjusted(2, 2, -2, -2)

        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)
        if selected:
            painter.setBrush(QColor(59, 130, 246, 45))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(rect, 7, 7)
            painter.setBrush(QColor("#3b82f6"))
            bar = rect.adjusted(0, 6, 0, -6)
            bar.setWidth(3)
            painter.drawRoundedRect(bar, 1.5, 1.5)
        elif hovered:
            painter.setBrush(QColor(128, 128, 128, 30))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(rect, 7, 7)

        text_rect = rect.adjusted(14, 7, -30, -7)
        title_font = QFont(option.font)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(option.palette.text().color())

        title = source.title
        if title in source_model.duplicate_titles:
            marker = (source.external_id or source.url)[-4:]
            title = f"{title}  ·{marker}"
        title = QFontMetrics(title_font).elidedText(
            title, Qt.ElideRight, text_rect.width()
        )
        painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignTop, title)

        total = sum(counts.values())
        downloaded = _downloaded_count(counts)
        errors = counts.get(ERROR, 0)
        track_word = "song" if total == 1 else "songs"
        subtitle = f"{total} {track_word} · {downloaded} downloaded"
        if errors:
            subtitle += f" · {errors} error" + ("" if errors == 1 else "s")

        sub_font = QFont(option.font)
        sub_font.setPointSizeF(max(7.5, sub_font.pointSizeF() - 1.2))
        painter.setFont(sub_font)
        painter.setPen(option.palette.placeholderText().color())
        subtitle = QFontMetrics(sub_font).elidedText(
            subtitle, Qt.ElideRight, text_rect.width()
        )
        painter.drawText(text_rect.adjusted(0, 20, 0, 0), Qt.AlignLeft | Qt.AlignTop, subtitle)

        if total:
            if errors:
                dot = QColor("#ef4444")
            elif downloaded >= total:
                dot = QColor("#22c55e")
            elif downloaded:
                dot = QColor("#f59e0b")
            else:
                dot = QColor("#9ca3af")
            painter.setBrush(dot)
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(rect.right() - 18, rect.center().y() - 4, 8, 8)

        painter.restore()


class SourceListWidget(QWidget):
    source_selected = Signal(int)
    refresh_requested = Signal(int)
    delete_requested = Signal(int)
    add_requested = Signal()
    open_url_requested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.model = SourceListModel(self)
        self.proxy = SourceFilterProxy(self)
        self.proxy.setSourceModel(self.model)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        header = QLabel("Library")
        header.setStyleSheet("font-weight: 600; font-size: 14px;")
        layout.addWidget(header)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter library…")
        self.search.textChanged.connect(self.proxy.set_search)
        layout.addWidget(self.search)

        self.view = QListView()
        self.view.setModel(self.proxy)
        self.view.setItemDelegate(SourceDelegate(self.view))
        self.view.setSelectionMode(QListView.SingleSelection)
        self.view.setUniformItemSizes(True)
        self.view.setVerticalScrollMode(QListView.ScrollPerPixel)
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._context_menu)
        self.view.selectionModel().currentChanged.connect(self._on_current_changed)
        layout.addWidget(self.view, 1)

        footer = QHBoxLayout()
        footer.setSpacing(6)
        self.add_button = QPushButton("Add")
        self.add_button.clicked.connect(self.add_requested)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self._refresh)
        self.remove_button = QPushButton("Remove")
        self.remove_button.setObjectName("danger")
        self.remove_button.clicked.connect(self._remove)
        footer.addWidget(self.add_button)
        footer.addWidget(self.refresh_button)
        footer.addWidget(self.remove_button)
        layout.addLayout(footer)

    def set_busy(self, busy: bool) -> None:
        enabled = not busy
        self.add_button.setEnabled(enabled)
        self.refresh_button.setEnabled(enabled)
        self.remove_button.setEnabled(enabled)

    def set_sources(
        self,
        sources: Iterable[Source],
        counts: dict[int, dict[str, int]],
        selected_id: Optional[int] = None,
    ) -> None:
        current = selected_id
        if current is None:
            current = self.selected_source_id()
        self.model.set_sources(sources, counts)
        if current is not None:
            self.select_source(current)

    def select_source(self, source_id: int) -> None:
        row = self.model.row_for_id(source_id)
        if row is None:
            return
        source_index = self.model.index(row, 0)
        proxy_index = self.proxy.mapFromSource(source_index)
        if proxy_index.isValid():
            self.view.setCurrentIndex(proxy_index)

    def selected_source_id(self) -> Optional[int]:
        index = self.view.currentIndex()
        if not index.isValid():
            return None
        source_index = self.proxy.mapToSource(index)
        source = self.model.source_at(source_index.row())
        return source.id if source else None

    def _on_current_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        if not current.isValid():
            return
        source_index = self.proxy.mapToSource(current)
        source = self.model.source_at(source_index.row())
        if source:
            self.source_selected.emit(source.id)

    def _refresh(self) -> None:
        source_id = self.selected_source_id()
        if source_id is not None:
            self.refresh_requested.emit(source_id)

    def _remove(self) -> None:
        source_id = self.selected_source_id()
        if source_id is not None:
            self.delete_requested.emit(source_id)

    def _context_menu(self, position) -> None:
        index = self.view.indexAt(position)
        if not index.isValid():
            return
        source_index = self.proxy.mapToSource(index)
        source = self.model.source_at(source_index.row())
        if source is None:
            return

        menu = QMenu(self)
        menu.addAction("Refresh", lambda: self.refresh_requested.emit(source.id))
        menu.addAction(
            "Open link in browser",
            lambda: self.open_url_requested.emit(source.url),
        )
        menu.addAction(
            "Copy link",
            lambda: QApplication.clipboard().setText(source.url),
        )
        menu.addSeparator()
        menu.addAction("Remove…", lambda: self.delete_requested.emit(source.id))
        menu.exec(self.view.viewport().mapToGlobal(position))
