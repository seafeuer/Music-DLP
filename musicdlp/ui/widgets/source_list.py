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
    ALL_SONGS_ID, CONVERTED, DOWNLOADED, ERROR, SYNCED, Source,
)

COUNT_ROLE = Qt.UserRole + 1


def _downloaded_count(counts: dict[str, int]) -> int:
    return sum(
        counts.get(status, 0)
        for status in (DOWNLOADED, CONVERTED, SYNCED)
    )


class SourceListModel(QAbstractListModel):
    """Library rows: an optional "All songs" entry, folder headings, sources."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # Each row is ("source", Source, counts) or ("header", text, counts)
        self.rows: list[tuple] = []
        self.duplicate_titles: set[str] = set()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self.rows)):
            return None
        row = self.rows[index.row()]
        if row[0] == "header":
            if role == Qt.DisplayRole:
                return row[1]
            if role == COUNT_ROLE:
                return row[2]
            return None
        source, counts = row[1], row[2]
        if role == Qt.DisplayRole:
            return source.title
        if role == COUNT_ROLE:
            return counts
        if role == Qt.ToolTipRole:
            return f"{source.title}\n{source.url}"
        return None

    def flags(self, index: QModelIndex):
        if not index.isValid():
            return Qt.NoItemFlags
        if self.rows[index.row()][0] == "header":
            return Qt.NoItemFlags  # headings cannot be selected
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable

    def set_sources(
        self,
        sources: Iterable[Source],
        counts: dict[int, dict[str, int]],
    ) -> None:
        sources = list(sources)
        self.beginResetModel()

        # Group by folder, keeping "All songs" first and ungrouped links
        # last, so a folder never hides the link you just added.
        folders: dict[str, list[Source]] = {}
        ungrouped: list[Source] = []
        all_entries: list[Source] = []
        for source in sources:
            if source.id == ALL_SONGS_ID:
                all_entries.append(source)
                continue
            folder = (source.folder or "").strip()
            if folder:
                folders.setdefault(folder, []).append(source)
            else:
                ungrouped.append(source)

        rows: list[tuple] = [("source", source, counts.get(source.id, {}))
                             for source in all_entries]
        for name in sorted(folders, key=str.lower):
            group = folders[name]
            group_counts: dict[str, int] = {}
            for source in group:
                for status, value in counts.get(source.id, {}).items():
                    group_counts[status] = group_counts.get(status, 0) + value
            rows.append(("header", name, group_counts))
            rows.extend(
                ("source", source, counts.get(source.id, {}))
                for source in group
            )
        rows.extend(
            ("source", source, counts.get(source.id, {}))
            for source in ungrouped
        )
        self.rows = rows

        # Two links can carry the same text (for example two tweets from the
        # same account). Remember which titles repeat so the list can tell
        # them apart.
        seen: dict[str, int] = {}
        for row in self.rows:
            if row[0] != "source":
                continue
            seen[row[1].title] = seen.get(row[1].title, 0) + 1
        self.duplicate_titles = {title for title, n in seen.items() if n > 1}
        self.endResetModel()

    def source_at(self, row: int) -> Optional[Source]:
        if 0 <= row < len(self.rows) and self.rows[row][0] == "source":
            return self.rows[row][1]
        return None

    def counts_at(self, row: int) -> dict[str, int]:
        if 0 <= row < len(self.rows):
            return self.rows[row][2]
        return {}

    def row_for_id(self, source_id: int) -> Optional[int]:
        for row, entry in enumerate(self.rows):
            if entry[0] == "source" and entry[1].id == source_id:
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
        row = model.rows[source_row]
        if row[0] == "header":
            return True
        source = row[1]
        haystack = f"{source.title} {source.uploader or ''}".lower()
        return self.search in haystack


class SourceDelegate(QStyledItemDelegate):
    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        if index.data(Qt.DisplayRole) is not None and self._is_header(index):
            return QSize(0, 30)
        return QSize(0, 58)

    @staticmethod
    def _is_header(index: QModelIndex) -> bool:
        model = index.model()
        row = index.row()
        while hasattr(model, "sourceModel") and model.sourceModel():
            row = model.mapToSource(index).row()
            model = model.sourceModel()
        entries = getattr(model, "rows", None)
        return bool(entries) and 0 <= row < len(entries) and entries[row][0] == "header"

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

        entry = source_model.rows[source_row]
        if entry[0] == "header":
            self._paint_header(painter, option, entry[1], entry[2])
            return

        source, counts = entry[1], entry[2]

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

    @staticmethod
    def _paint_header(painter: QPainter, option: QStyleOptionViewItem,
                      name: str, counts: dict[str, int]) -> None:
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = option.rect.adjusted(8, 6, -8, 0)

        font = QFont(option.font)
        font.setPointSizeF(max(7.5, font.pointSizeF() - 1.5))
        font.setBold(True)
        font.setLetterSpacing(QFont.AbsoluteSpacing, 0.6)
        painter.setFont(font)
        painter.setPen(option.palette.placeholderText().color())

        total = sum(counts.values())
        label = name.upper()
        if total:
            label += f"   ·   {total}"
        painter.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, label)
        painter.restore()


class SourceListWidget(QWidget):
    source_selected = Signal(int)
    refresh_requested = Signal(int)
    delete_requested = Signal(int)
    add_requested = Signal()
    open_url_requested = Signal(str)
    select_all_requested = Signal()
    move_to_folder_requested = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.model = SourceListModel(self)
        self.proxy = SourceFilterProxy(self)
        self.proxy.setSourceModel(self.model)
        self._last_source_id: Optional[int] = None

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
        self.view.clicked.connect(self._on_clicked)
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
            self._update_buttons()
            self.source_selected.emit(source.id)

    def _on_clicked(self, index: QModelIndex) -> None:
        """A click on a folder heading should not clear the selection."""
        if not index.isValid():
            return
        source_index = self.proxy.mapToSource(index)
        if self.model.source_at(source_index.row()) is None:
            # Restore the previously selected link.
            if self._last_source_id is not None:
                self.select_source(self._last_source_id)

    def select_source(self, source_id: int) -> None:
        row = self.model.row_for_id(source_id)
        if row is None:
            return
        self._last_source_id = source_id
        source_index = self.model.index(row, 0)
        proxy_index = self.proxy.mapFromSource(source_index)
        if proxy_index.isValid():
            self.view.setCurrentIndex(proxy_index)

    def _refresh(self) -> None:
        source_id = self.selected_source_id()
        if source_id is not None and source_id != ALL_SONGS_ID:
            self.refresh_requested.emit(source_id)

    def _remove(self) -> None:
        source_id = self.selected_source_id()
        if source_id is not None and source_id != ALL_SONGS_ID:
            self.delete_requested.emit(source_id)

    def _update_buttons(self) -> None:
        source_id = self.selected_source_id()
        is_all = source_id == ALL_SONGS_ID
        self.refresh_button.setEnabled(not is_all)
        self.remove_button.setEnabled(not is_all)

    def _context_menu(self, position) -> None:
        index = self.view.indexAt(position)
        if not index.isValid():
            return
        source_index = self.proxy.mapToSource(index)
        source = self.model.source_at(source_index.row())
        if source is None:
            return

        menu = QMenu(self)
        if source.id == ALL_SONGS_ID:
            menu.addAction(
                "Select every song",
                lambda: self.select_all_requested.emit(),
            )
        else:
            menu.addAction(
                "Move to folder…",
                lambda: self.move_to_folder_requested.emit(source.id),
            )
            menu.addSeparator()
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
