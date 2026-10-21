"""AI naming, export/import, and settings."""

import json

import pytest

from musicdlp.services import ai
from musicdlp.services.ai import AIError, AISettings, _parse_result
from musicdlp.services.ai_export import (
    ExportedSong, ImportedName, build_prompt, parse_reply, songs_needing_help,
)


# --------------------------------------------------------------- parsing


def test_parse_result_reads_plain_json():
    result = _parse_result(
        '{"title": "At The Gates", "artist": "Drake", "confidence": 0.9}'
    )
    assert result.title == "At The Gates"
    assert result.artist == "Drake"
    assert result.confidence == pytest.approx(0.9)
    assert result.usable


def test_parse_result_reads_fenced_json_with_prose():
    reply = (
        "Sure, here is the answer:\n```json\n"
        '{"title": "Song", "artist": "Artist", "album": "Album", '
        '"confidence": 0.8}\n```\nHope that helps.'
    )
    result = _parse_result(reply)
    assert result.title == "Song"
    assert result.album == "Album"


def test_parse_result_clamps_confidence():
    assert _parse_result(
        '{"title": "x", "artist": "y", "confidence": 7}'
    ).confidence == 1.0


def test_parse_result_rejects_junk():
    with pytest.raises(AIError):
        _parse_result("I cannot help with that.")


def test_settings_report_whether_they_are_usable():
    assert not AISettings().enabled
    configured = AISettings(model="gpt-oss:20b", base_url="https://ollama.com")
    assert configured.enabled
    assert "gpt-oss" in configured.describe()


# --------------------------------------------------------------- export


def test_prompt_contains_the_songs_and_instructions():
    songs = [
        ExportedSong(id=1, title="Messy title", channel="Some channel"),
        ExportedSong(id=2, title="Another", channel="Other", description="d"),
    ]
    prompt = build_prompt(songs)
    assert "Reply with JSON only" in prompt
    assert "Messy title" in prompt
    assert '"id": "1"' in prompt


def test_parse_reply_reads_a_list():
    reply = json.dumps([
        {"id": "1", "title": "A", "artist": "B", "confidence": 0.9},
        {"id": "2", "title": "C", "artist": "", "confidence": 0.3},
    ])
    results = parse_reply(reply)
    assert [item.id for item in results] == [1, 2]
    assert results[0].artist == "B"


def test_parse_reply_reads_fenced_list_with_prose():
    reply = "Here you go:\n```json\n[{\"id\": \"7\", \"title\": \"T\"}]\n```"
    results = parse_reply(reply)
    assert results[0].id == 7
    assert results[0].title == "T"


def test_parse_reply_accepts_a_single_object():
    results = parse_reply('{"id": "5", "title": "Solo", "artist": "Me"}')
    assert results[0].id == 5


def test_parse_reply_skips_unusable_rows():
    reply = json.dumps([
        {"id": "1", "title": ""},
        {"id": "not-a-number", "title": "x"},
        {"id": "2", "title": "Good"},
    ])
    results = parse_reply(reply)
    assert [item.id for item in results] == [2]


def test_parse_reply_rejects_nothing_usable():
    with pytest.raises(ValueError):
        parse_reply("no json here")
    with pytest.raises(ValueError):
        parse_reply("[]")


class _Row:
    def __init__(self, track_id, title, confidence):
        self.track = type(
            "T", (), {
                "id": track_id,
                "title": title,
                "uploader": "Channel",
                "description": "",
                "smart_confidence": confidence,
            }
        )()


def test_only_unconfident_songs_are_exported():
    rows = [
        _Row(1, "Known", 0.9),
        _Row(2, "Unknown", 0.3),
        _Row(3, "No idea", None),
    ]
    songs = songs_needing_help(rows)
    assert [song.id for song in songs] == [2, 3]


# --------------------------------------------------------------- requests


def test_missing_settings_raise_a_helpful_error():
    with pytest.raises(AIError) as caught:
        ai.chat(AISettings(), "system", "user")
    assert "not set up" in str(caught.value)


def test_bearer_key_is_sent_when_present():
    headers = ai._headers(
        AISettings(model="m", base_url="https://x", api_key="secret")
    )
    assert headers["Authorization"] == "Bearer secret"
    # Some proxies reject Python's default agent outright.
    assert "Mozilla" in headers["User-Agent"]


def test_no_authorization_header_without_a_key():
    headers = ai._headers(AISettings(model="m", base_url="http://localhost"))
    assert "Authorization" not in headers


def test_ollama_endpoint_is_built_correctly():
    settings = AISettings(base_url="https://ollama.com/")
    assert ai._endpoint(settings, "/api/chat") == "https://ollama.com/api/chat"


# --------------------------------------------------------------- settings


def test_settings_survive_a_byte_order_mark(tmp_path):
    """Editors on Windows often add a BOM; it must not wipe the settings."""
    from musicdlp.core.config import Settings

    path = tmp_path / "config.json"
    path.write_text(
        '﻿{"theme": "dark", "ai_model": "gpt-oss:20b", '
        '"ai_base_url": "https://ollama.com"}',
        encoding="utf-8",
    )
    loaded = Settings.load(path)
    assert loaded.theme == "dark"
    assert loaded.ai_model == "gpt-oss:20b"
    assert loaded.ai_configured


def test_settings_round_trip_including_ai(tmp_path):
    from musicdlp.core.config import Settings

    path = tmp_path / "config.json"
    settings = Settings(
        ai_provider="ollama",
        ai_base_url="https://ollama.com",
        ai_model="gpt-oss:20b",
        ai_api_key="secret",
        ai_find_artwork=False,
    )
    settings.save(path)
    loaded = Settings.load(path)
    assert loaded.ai_provider == "ollama"
    assert loaded.ai_model == "gpt-oss:20b"
    assert loaded.ai_api_key == "secret"
    assert loaded.ai_find_artwork is False
