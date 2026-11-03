"""The library list shows which panel has the keyboard."""

from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem

from musicdlp.core.models import Source
from musicdlp.ui.theme import ACCENT
from musicdlp.ui.widgets.source_list import SourceDelegate, SourceListModel


def _render(state: QStyle.StateFlag) -> QImage:
    source = Source(
        id=1,
        url="https://www.youtube.com/playlist?list=PLfocus",
        kind="playlist",
        platform="youtube",
        external_id="PLfocus",
        title="A list",
        uploader="Channel",
        item_count=1,
        thumbnail_url=None,
        added_at=None,
        refreshed_at=None,
    )
    model = SourceListModel()
    model.set_sources([source], {1: {}})

    image = QImage(320, 58, QImage.Format_ARGB32)
    image.fill(QColor("#1e1f22"))
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 320, 58)
    option.state = state
    option.font = QApplication.font()
    option.palette = QApplication.palette()
    painter = QPainter(image)
    SourceDelegate().paint(painter, option, model.index(0, 0))
    painter.end()
    return image


def _accent_pixels(image: QImage) -> int:
    accent = QColor(ACCENT)
    count = 0
    for x in range(image.width()):
        for y in range(image.height()):
            colour = image.pixelColor(x, y)
            if (
                abs(colour.red() - accent.red()) < 10
                and abs(colour.green() - accent.green()) < 10
                and abs(colour.blue() - accent.blue()) < 10
            ):
                count += 1
    return count


def test_focused_row_draws_a_ring_around_its_selection(qapp):
    selected = QStyle.State_Selected | QStyle.State_Enabled
    focused = selected | QStyle.State_HasFocus

    without_focus = _accent_pixels(_render(selected))
    with_focus = _accent_pixels(_render(focused))

    # The 3-px selection bar is already accent; the ring adds the rest.
    assert with_focus > without_focus + 500