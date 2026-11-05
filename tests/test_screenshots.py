"""Screenshot baselines for the main window.

Theme and layout changes are the ones a unit test cannot see: a clipped
button, a colour that only suits one theme, a column that collapses at
the smallest supported window. These grab the window offscreen at three
sizes in both themes and compare with a stored image.

Run once with MUSICDLP_UPDATE_BASELINES=1 to regenerate the images after
an intentional visual change. On a mismatch the new grab is written to
the pytest temporary folder so it can be looked at beside the baseline.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest
from PIL import Image, ImageChops
from PySide6.QtWidgets import QApplication

from musicdlp.core.config import Settings
from musicdlp.core.db import Database
from musicdlp.ui.main_window import MainWindow, build_context
from musicdlp.ui.theme import apply_theme

BASELINES = Path(__file__).resolve().parent / "baselines"
SIZES = ((1000, 620), (1280, 800), (1600, 1000))
THEMES = ("dark", "light")
# A pixel whose channel difference is below this is invisible anti-
# aliasing noise. Above it, count the pixel as changed.
PIXEL_TOLERANCE = 16
# Allow one percent of the window to differ: enough for a caret, a
# scrollbar easing or a font hinting change, far below a moved control.
MAX_CHANGED_RATIO = 0.01


@pytest.fixture()
def window(qtbot, qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(Settings, "save", lambda self, path=None: None)
    settings = Settings(
        theme="dark",
        mode="easy",
        output_dir=str(tmp_path / "out"),
        spotify_dir=str(tmp_path / "spotify"),
        first_run_done=True,
        notifications=False,
    )
    db = Database(tmp_path / "library.db")
    source_id = db.add_source(
        url="https://www.youtube.com/playlist?list=PLtest",
        kind="playlist",
        platform="youtube",
        external_id="PLtest",
        title="Test playlist",
        uploader="Test channel",
        item_count=2,
        thumbnail_url=None,
    )
    ids = []
    for index, media_id in enumerate(("vid1", "vid2"), start=1):
        ids.append(
            db.upsert_track(
                platform="youtube",
                media_id=media_id,
                url=f"https://www.youtube.com/watch?v={media_id}",
                title=f"Song {index}",
                uploader="Test channel",
                duration=200,
            )
        )
    db.set_source_tracks(source_id, [(ids[0], 1), (ids[1], 2)])

    logger = logging.getLogger("musicdlp.test.screens")
    logger.setLevel(logging.INFO)
    apply_theme(qapp, "dark")
    ctx = build_context(settings, db, logger)
    main = MainWindow(ctx)
    qtbot.addWidget(main)
    main.show()
    qtbot.wait(100)
    yield main, db
    try:
        db.close()
    except Exception:
        pass


def _grab(main, qapp, qtbot, size, theme):
    apply_theme(qapp, theme)
    main.resize(*size)
    main.show()
    qapp.processEvents()
    qtbot.wait(120)
    return main.grab().toImage()


def _changed_ratio(baseline: Path, actual: Path) -> float:
    with Image.open(baseline) as left, Image.open(actual) as right:
        first = left.convert("RGB")
        second = right.convert("RGB")
        if first.size != second.size:
            return 1.0
        difference = ImageChops.difference(first, second).convert("L")
        histogram = difference.histogram()
    changed = sum(histogram[PIXEL_TOLERANCE:])
    total = first.size[0] * first.size[1]
    return changed / total if total else 1.0


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", SIZES)
def test_the_main_window_looks_the_same_as_its_baseline(
    window, qapp, qtbot, tmp_path, theme, size
):
    main, _db = window
    width, height = size
    image = _grab(main, qapp, qtbot, size, theme)
    actual = tmp_path / f"main-{width}x{height}-{theme}.png"
    image.save(str(actual))
    baseline = BASELINES / actual.name

    if os.environ.get("MUSICDLP_UPDATE_BASELINES"):
        BASELINES.mkdir(parents=True, exist_ok=True)
        image.save(str(baseline))
        return

    assert baseline.is_file(), (
        f"No baseline at {baseline}. Run the screenshots test once with "
        "MUSICDLP_UPDATE_BASELINES=1 to create it, then look at it."
    )
    ratio = _changed_ratio(baseline, actual)
    assert ratio <= MAX_CHANGED_RATIO, (
        f"{ratio:.1%} of the {width}×{height} {theme} window differs from "
        f"{baseline.name}. The new grab is {actual}."
    )