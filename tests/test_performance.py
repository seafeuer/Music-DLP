"""The song list has to stay usable at a realistic library size.

A thousand songs is a big personal library, not an extreme one. The table
is a QTableView, so painting is virtualised; what this pins is the model
build and the filter pass, which used to walk every row on each keystroke.
The bounds are deliberately loose enough for a slow CI machine and still
tight enough to catch a quadratic change.
"""

import time
import tracemalloc

from musicdlp.core.models import Source, Track, TrackRow
from musicdlp.ui.models.track_model import TrackFilterProxy, TrackTableModel

COUNT = 1000


def _rows(count=COUNT):
    source = Source(
        id=1, url="https://example.test/list", kind="playlist",
        platform="youtube", external_id="list", title="Big list",
        uploader="Artist", item_count=count, thumbnail_url=None,
        added_at=None, refreshed_at=None,
    )
    rows = []
    for index in range(count):
        track = Track(
            id=index + 1, platform="youtube", media_id=f"m{index}",
            url=f"https://youtu.be/m{index}", source_index=index,
            title=f"Song {index}", uploader=f"Artist {index % 40}",
            duration=180 + index % 60, thumbnail_url=None, description=None,
            status="new", error=None, meta_title=None, meta_artist=None,
            meta_album=None, meta_track=None, raw_path=None, mp3_path=None,
            m4a_path=None, cover_path=None, downloaded_at=None,
            converted_at=None, synced_at=None,
        )
        rows.append(TrackRow(source=source, track=track, position=index + 1))
    return rows


def test_a_thousand_songs_load_quickly(qapp):
    model = TrackTableModel()
    proxy = TrackFilterProxy()
    proxy.setSourceModel(model)
    rows = _rows()

    started = time.perf_counter()
    model.set_rows(rows)
    elapsed = time.perf_counter() - started

    assert model.rowCount() == COUNT
    assert elapsed < 0.75, f"loading {COUNT} songs took {elapsed:.2f}s"


def test_search_over_a_thousand_songs_is_quick(qapp):
    model = TrackTableModel()
    proxy = TrackFilterProxy()
    proxy.setSourceModel(model)
    model.set_rows(_rows())

    started = time.perf_counter()
    proxy.set_search("song 500")
    elapsed = time.perf_counter() - started

    assert proxy.rowCount() == 1
    assert elapsed < 0.25, f"filtering {COUNT} songs took {elapsed:.2f}s"


def test_the_model_for_a_thousand_songs_stays_small(qapp):
    tracemalloc.start()
    try:
        model = TrackTableModel()
        model.set_rows(_rows())
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert model.rowCount() == COUNT
    # A Python row is small; a runaway per-row copy or a thumbnail pixmap
    # in the model would blow far past this.
    assert peak < 100 * 1024 * 1024, f"peak was {peak / 1024 / 1024:.1f} MB"