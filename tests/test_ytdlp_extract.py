"""Extraction options: a single link must fail when it has no media."""

from __future__ import annotations

import pytest
import yt_dlp

from musicdlp.core.config import Settings
from musicdlp.core.errors import AppError
from musicdlp.services.ytdlp import YtDlpService


class FakeYDL:
    """Stands in for yt_dlp.YoutubeDL and records the options used."""

    last_opts: dict = {}
    payload: dict = {}

    def __init__(self, opts):
        FakeYDL.last_opts = dict(opts)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def extract_info(self, _url, download=False):
        return dict(FakeYDL.payload)


BASE = {
    "id": "vid1",
    "title": "A Song",
    "uploader": "Channel",
    "duration": 120,
    "thumbnail": "https://example.test/a.jpg",
    "extractor_key": "Youtube",
    "webpage_url": "https://www.youtube.com/watch?v=vid1",
    "formats": [{"format_id": "251"}],
}


def test_a_single_link_asks_yt_dlp_to_fail_when_there_is_no_media(monkeypatch):
    """Regression: ignore_no_formats_error added empty, undownloadable rows."""
    FakeYDL.payload = dict(BASE)
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)

    YtDlpService(Settings()).extract("https://www.youtube.com/watch?v=vid1")

    assert FakeYDL.last_opts["ignore_no_formats_error"] is False
    assert FakeYDL.last_opts["extract_flat"] is False


def test_a_playlist_keeps_tolerating_entries_without_formats(monkeypatch):
    FakeYDL.payload = {
        "id": "PL1",
        "title": "A list",
        "extractor_key": "Youtube",
        "_type": "playlist",
        "entries": [dict(BASE)],
    }
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)

    YtDlpService(Settings()).extract(
        "https://www.youtube.com/playlist?list=PL1"
    )

    assert FakeYDL.last_opts["ignore_no_formats_error"] is True


def test_a_post_with_no_video_is_reported_when_it_is_added(monkeypatch):
    """The raw extractor error becomes an actionable message."""
    def explode(_opts):
        raise yt_dlp.utils.DownloadError(
            "ERROR: [twitter] 123: No video could be found in this tweet"
        )

    monkeypatch.setattr(yt_dlp, "YoutubeDL", explode)

    with pytest.raises(AppError) as caught:
        YtDlpService(Settings()).extract(
            "https://x.com/user/status/123"
        )

    text = caught.value.user_text().lower()
    assert "no video" in text
    assert "only posts that contain a video" in text
