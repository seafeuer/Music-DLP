"""Source (playlist/link) list model, delegate, and widget."""

from __future__ import annotations

from typing import Iterable, Optional

from PySide6.QtCore import (
    QAbstractListModel, QMimeData, QModelIndex, QSize, QSortFilterProxyModel,
    Qt, Signal,
)
from PySide6.QtGui import QColor, QDrag, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication, QGridLayout, QLabel, QLineEdit, QListView, QMenu,
    QPushButton, QStackedWidget, QStyle, QStyledItemDelegate,
    QStyleOptionViewItem, QVBoxLayout, QWidget,
)

from musicdlp.core.models import (
    ALL_SONGS_ID, CONVERTED, DOWNLOADED, ERROR, SYNCED, Source,
)
from musicdlp.ui.theme import ACCENT, tone
from musicdlp.ui.widgets.search_box import SearchBox

COUNT_ROLE = Qt.UserRole + 1
INDENT_ROLE = Qt.UserRole + 2
TRACKS_MIME = "application/x-musicdlp-tracks"
SOURCE_MIME = "application/x-musicdlp-source"


def _downloaded_count(counts: dict[str, int]) -> int:
    return sum(
        counts.get(status, 0)
        for status in (DOWNLOADED, CONVERTED, SYNCED)
    )


def _alpha(color: str, alpha: int) -> QColor:
    """A colour from the theme at a given opacity."""
    value = QColor(color)
    value.setAlpha(alpha)
    return value


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
        if role == INDENT_ROLE:
            return row[3]
        if role == Qt.ToolTipRole:
            if source.is_folder:
                return f"{source.title}\nDrag songs here to file them."
            return f"{source.title}\n{source.url}"
        return None

    def flags(self, index: QModelIndex):
        if not index.isValid():
            return Qt.NoItemFlags
        entry = self.rows[index.row()]
        if entry[0] == "header":
            return Qt.NoItemFlags  # headings cannot be selected
        source = entry[1]
        flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if source.id != ALL_SONGS_ID and not source.is_folder:
            flags |= Qt.ItemIsDragEnabled
        if source.is_folder:
            flags |= Qt.ItemIsDropEnabled
        return flags

    def set_sources(
        self,
        sources: Iterable[Source],
        counts: dict[int, dict[str, int]],
    ) -> None:
        sources = list(sources)
        self.beginResetModel()

        all_entries = [s for s in sources if s.id == ALL_SONGS_ID]
        folders = [s for s in sources if s.is_folder]
        links = [
            s for s in sources
            if s.id != ALL_SONGS_ID and not s.is_folder
        ]

        by_folder: dict[str, list[Source]] = {}
        unfiled: list[Source] = []
        for link in links:
            name = (link.folder or "").strip()
            if name:
                by_folder.setdefault(name.lower(), []).append(link)
            else:
                unfiled.append(link)

        # ("source", Source, counts, indent)
        rows: list[tuple] = [
            ("source", source, counts.get(source.id, {}), 0)
            for source in all_entries
        ]

        known = {folder.title.lower() for folder in folders}
        for folder in folders:
            rows.append(("source", folder, counts.get(folder.id, {}), 0))
            for link in by_folder.get(folder.title.lower(), []):
                rows.append(("source", link, counts.get(link.id, {}), 1))
        # A link naming a folder that does not exist yet is still shown.
        for name, group in by_folder.items():
            if name in known:
                continue
            rows.append(("header", name.title(), {}, 0))
            rows.extend(
                ("source", link, counts.get(link.id, {}), 1)
                for link in group
            )

        rows.extend(
            ("source", source, counts.get(source.id, {}), 0)
            for source in unfiled
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
        indent = entry[3] if len(entry) > 3 else 0

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = option.rect.adjusted(2, 2, -2, -2)
        if indent:
            rect = rect.adjusted(18, 0, 0, 0)

        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)
        drop_target = (
            source.is_folder
            and getattr(option.widget, "_hovered_folder", None) == source.id
        )
        if drop_target:
            painter.setBrush(_alpha(ACCENT, 60))
            painter.setPen(QPen(QColor(ACCENT), 1.5))
            painter.drawRoundedRect(rect, 7, 7)
            painter.setPen(Qt.NoPen)
        elif selected:
            painter.setBrush(_alpha(ACCENT, 51))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(rect, 7, 7)
            painter.setBrush(QColor(ACCENT))
            bar = rect.adjusted(0, 6, 0, -6)
            bar.setWidth(3)
            painter.drawRoundedRect(bar, 1.5, 1.5)
        elif hovered:
            painter.setBrush(QColor(128, 128, 128, 30))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(rect, 7, 7)

        # Selection stays painted when focus moves to the song table, so
        # it cannot also mean "this list has the keyboard". A ring says
        # that explicitly, which is what WCAG 2.4.7 asks for.
        focused = bool(option.state & QStyle.State_HasFocus)
        if focused:
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(ACCENT), 2))
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 6, 6)
            painter.setPen(Qt.NoPen)

        text_rect = rect.adjusted(14, 7, -30, -7)
        if source.is_folder:
            # A native folder icon says "container" without the chevron,
            # which looked like something that could be expanded.
            icon_rect = rect.adjusted(2, 9, 0, 0)
            icon_rect.setWidth(14)
            icon_rect.setHeight(14)
            painter.drawPixmap(
                icon_rect,
                option.widget.style().standardIcon(
                    QStyle.SP_DirIcon
                ).pixmap(14, 14),
            )
            text_rect = rect.adjusted(22, 7, -30, -7)

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
        if source.is_folder:
            subtitle = f"{total} {track_word} · drop songs here"
        else:
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
                dot = QColor(tone("danger"))
            elif downloaded >= total:
                dot = QColor(tone("success"))
            elif downloaded:
                dot = QColor(tone("warning"))
            else:
                dot = QColor(option.palette.placeholderText().color())
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


class SourceListView(QListView):
    """The library list: accepts songs and links dragged onto a folder."""

    tracks_dropped = Signal(int, list)
    source_dropped = Signal(int, int)  # source_id, folder_id

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(False)
        self.setDragDropMode(QListView.DragDrop)
        self.setDragEnabled(True)
        self.setDefaultDropAction(Qt.MoveAction)
        self._hovered_folder: Optional[int] = None

    # ---------------------------------------------------------------- drag

    def startDrag(self, supported_actions) -> None:
        source = self._source_at(self.currentIndex())
        if source is None or source.is_folder or source.id == ALL_SONGS_ID:
            return
        payload = QMimeData()
        payload.setData(SOURCE_MIME, str(source.id).encode("ascii"))
        payload.setText(source.title)
        drag = QDrag(self)
        drag.setMimeData(payload)
        drag.exec(Qt.MoveAction)

    def _source_at(self, index: QModelIndex) -> Optional[Source]:
        if not index.isValid():
            return None
        model = self.model()
        if model is None:
            return None
        source_index = model.mapToSource(index)
        entries = getattr(model.sourceModel(), "rows", None)
        if not entries or not (0 <= source_index.row() < len(entries)):
            return None
        entry = entries[source_index.row()]
        if entry[0] != "source":
            return None
        return entry[1]

    # ------------------------------------------------------------------ drop

    def _folder_at(self, position) -> Optional[int]:
        source = self._source_at(self.indexAt(position))
        if source is None or not source.is_folder:
            return None
        return source.id

    def dragEnterEvent(self, event) -> None:
        if self._accepts(event):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    @staticmethod
    def _accepts(event) -> bool:
        data = event.mimeData()
        return data.hasFormat(TRACKS_MIME) or data.hasFormat(SOURCE_MIME)

    def dragMoveEvent(self, event) -> None:
        if not self._accepts(event):
            super().dragMoveEvent(event)
            return
        folder_id = self._folder_at(event.position().toPoint())
        self._set_hover(folder_id)
        if folder_id is None:
            event.ignore()
        else:
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        self._set_hover(None)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        if not self._accepts(event):
            super().dropEvent(event)
            return
        folder_id = self._folder_at(event.position().toPoint())
        self._set_hover(None)
        if folder_id is None:
            event.ignore()
            return

        data = event.mimeData()
        if data.hasFormat(TRACKS_MIME):
            raw = bytes(data.data(TRACKS_MIME)).decode("ascii")
            ids = [int(value) for value in raw.split(",") if value.strip()]
            if ids:
                self.tracks_dropped.emit(folder_id, ids)
                event.acceptProposedAction()
            return

        if data.hasFormat(SOURCE_MIME):
            raw = bytes(data.data(SOURCE_MIME)).decode("ascii").strip()
            if raw.isdigit():
                self.source_dropped.emit(int(raw), folder_id)
                event.acceptProposedAction()

    def _set_hover(self, folder_id: Optional[int]) -> None:
        if folder_id == self._hovered_folder:
            return
        self._hovered_folder = folder_id
        self.viewport().update()


class SourceListWidget(QWidget):
    source_selected = Signal(int)
    refresh_requested = Signal(int)
    delete_requested = Signal(int)
    add_requested = Signal()
    open_url_requested = Signal(str)
    select_all_requested = Signal()
    move_to_folder_requested = Signal(int)
    tracks_dropped = Signal(int, list)
    source_dropped = Signal(int, int)
    new_folder_requested = Signal()
    rename_folder_requested = Signal(int)
    folder_template_requested = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.model = SourceListModel(self)
        self.proxy = SourceFilterProxy(self)
        self.proxy.setSourceModel(self.model)
        self._last_source_id: Optional[int] = None
        self._busy = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        header = QLabel("Library")
        header.setObjectName("sectionTitle")
        layout.addWidget(header)

        self.search = SearchBox("Filter links…")
        self.search.setToolTip(
            "Show only links and folders whose name matches. To search the "
            "songs themselves, use the box at the top of the window."
        )
        self.search.textChanged.connect(self._on_filter_text)
        layout.addWidget(self.search)

        self.view = SourceListView()
        self.view.setAccessibleName("Links and folders")
        self.view.setModel(self.proxy)
        self.view.setItemDelegate(SourceDelegate(self.view))
        self.view.setSelectionMode(QListView.SingleSelection)
        self.view.setUniformItemSizes(True)
        self.view.setVerticalScrollMode(QListView.ScrollPerPixel)
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._context_menu)
        self.view.selectionModel().currentChanged.connect(self._on_current_changed)
        self.view.tracks_dropped.connect(self.tracks_dropped)
        self.view.source_dropped.connect(self.source_dropped)

        self.empty = QLabel(
            "No links yet."
        )
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.setObjectName("muted")

        self.stack = QStackedWidget()
        self.stack.addWidget(self.view)
        self.stack.addWidget(self.empty)
        layout.addWidget(self.stack, 1)

        footer = QGridLayout()
        footer.setSpacing(6)
        self.add_button = QPushButton("Paste link…")
        self.add_button.setToolTip("Put the cursor in the link box at the top of the window")
        self.add_button.clicked.connect(self.add_requested)
        self.folder_button = QPushButton("New folder")
        self.folder_button.setToolTip(
            "Create a folder, then drag links or songs onto it"
        )
        self.folder_button.clicked.connect(self.new_folder_requested)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setToolTip("Read the selected link again (F5)")
        self.refresh_button.clicked.connect(self._refresh)
        self.remove_button = QPushButton("Remove")
        self.remove_button.setObjectName("danger")
        self.remove_button.setToolTip("Remove the selected link from the library")
        self.remove_button.clicked.connect(self._remove)
        # Two rows: at the narrowest window a single row of four buttons
        # was cut to "N folder", "Add lin…", "Refre", "Remo".
        footer.addWidget(self.add_button, 0, 0)
        footer.addWidget(self.folder_button, 0, 1)
        footer.addWidget(self.refresh_button, 1, 0)
        footer.addWidget(self.remove_button, 1, 1)
        layout.addLayout(footer)

    def set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        enabled = not self._busy
        self.add_button.setEnabled(enabled)
        self.folder_button.setEnabled(enabled)
        # Re-apply the per-row rules as well: a folder has nothing to
        # refresh, and nothing may be changed while a job is running.
        self._update_buttons()

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
        self._update_empty_state()
        if current is not None:
            self.select_source(current)

    def _update_empty_state(self) -> None:
        """An empty library says what to do; it is not a blank panel."""
        empty = self.model.rowCount() == 0
        self.stack.setCurrentWidget(self.empty if empty else self.view)
        self.search.setVisible(not empty)
        self._update_buttons()

    def _on_filter_text(self, text: str) -> None:
        self.proxy.set_search(text)
        # A filter that matches nothing should say so rather than showing
        # an empty white box.
        if self.model.rowCount() and self.proxy.rowCount() == 0:
            self.empty.setText(
                "No link matches that filter.<br><br>"
                "Clear the box above to see every link."
            )
            self.stack.setCurrentWidget(self.empty)
        else:
            self.empty.setText(
                "No links yet."
            )
            self._update_empty_state()

    def select_source(self, source_id: int) -> None:
        row = self.model.row_for_id(source_id)
        if row is None:
            return
        self._last_source_id = source_id
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
        is_folder = False
        if source_id is not None and not is_all:
            row = self.model.row_for_id(source_id)
            if row is not None:
                is_folder = self.model.rows[row][1].is_folder
        busy = getattr(self, "_busy", False)
        has_selection = source_id is not None
        self.refresh_button.setEnabled(
            not busy and has_selection and not is_all and not is_folder
        )
        self.remove_button.setEnabled(
            not busy and has_selection and not is_all
        )

    def _context_menu(self, position) -> None:
        # A job is already reading or writing this library. Changing links
        # underneath it would leave the pipeline working on rows that no
        # longer exist, so the menu is simply not offered.
        if getattr(self, "_busy", False):
            return
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
        elif source.is_folder:
            menu.addAction(
                "Rename folder…",
                lambda: self.rename_folder_requested.emit(source.id),
            )
            menu.addAction(
                "File name pattern…",
                lambda: self.folder_template_requested.emit(source.id),
            )
            menu.addSeparator()
            menu.addAction(
                "Remove folder…",
                lambda: self.delete_requested.emit(source.id),
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
