"""Filename rendering, sanitizing, and collision handling."""

from pathlib import Path

from musicdlp.services.filenames import (
    render_template,
    sanitize_filename,
    unique_path,
)


def test_keeps_unicode():
    assert sanitize_filename("夜に駆ける") == "夜に駆ける"


def test_replaces_invalid_characters():
    assert sanitize_filename('AC/DC: Back in Black?') == "AC_DC_ Back in Black_"


def test_collapses_newlines_and_controls():
    assert sanitize_filename("Line one\nLine two\t") == "Line one Line two"


def test_reserved_windows_names():
    assert sanitize_filename("CON") == "_CON"
    assert sanitize_filename("nul.txt") == "_nul.txt"


def test_strips_trailing_dots_and_spaces():
    assert sanitize_filename("  Song...  ") == "Song"


def test_empty_falls_back():
    assert sanitize_filename("   ") == "Untitled"


def test_render_template_default_title():
    assert render_template(
        "{title}", title="A Song", artist="Artist"
    ) == "A Song"


def test_render_template_placeholders():
    assert render_template(
        "{artist} - {title} [{track}]",
        title="Song",
        artist="Artist",
        track=3,
    ) == "Artist - Song [03]"


def test_render_template_unknown_placeholder_is_empty():
    assert render_template("{title}{nope}", title="Song") == "Song"


def test_render_template_cannot_escape_directory(tmp_path):
    # Separators are sanitized, so the result is a single path component.
    rendered = render_template("../{title}", title="../../evil")
    assert "/" not in rendered
    assert "\\" not in rendered


def test_unique_path_plain(tmp_path):
    path = unique_path(tmp_path, "Song", ".mp3")
    assert path == tmp_path / "Song.mp3"


def test_unique_path_adds_id_then_number(tmp_path):
    (tmp_path / "Song.mp3").write_bytes(b"x")
    path = unique_path(tmp_path, "Song", ".mp3", fallback_id="abc")
    assert path.name == "Song [abc].mp3"

    path.write_bytes(b"x")
    path = unique_path(tmp_path, "Song", ".mp3", fallback_id="abc")
    assert path.name == "Song (2).mp3"


def test_unique_path_respects_length(tmp_path):
    limit = len(str(tmp_path)) + 40
    path = unique_path(
        tmp_path, "x" * 300, ".mp3", max_path_length=limit
    )
    assert len(str(path)) <= limit
    assert path.suffix == ".mp3"


def test_render_template_accepts_a_number_format_spec():
    assert render_template(
        "{track:02d} - {title}", title="Song", track=3
    ) == "03 - Song"
    assert render_template("{track:d}. {title}", title="Song", track=3) == "3. Song"


def test_render_template_accepts_media_id_as_a_placeholder():
    assert render_template(
        "{media_id} - {title}", title="Song", media_id="abc123"
    ) == "abc123 - Song"
