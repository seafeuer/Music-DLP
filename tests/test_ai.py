"""AI naming, export/import, and settings."""

import json

import pytest

from musicdlp.services import ai
from musicdlp.services.ai import AIError, AISettings, _parse_result
from musicdlp.services.ai_export import (
    ExportedSong, ImportedName, build_prompt, parse_reply, songs_needing_help,
)


@pytest.fixture(autouse=True)
def _forget_detected_formats():
    """The detected wire format is cached per address; tests must not share it."""
    ai._dialect_cache.clear()
    yield
    ai._dialect_cache.clear()


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


# ------------------------------------------------------- reasoning models

def _settings(provider="anthropic"):
    return AISettings(
        provider=provider, base_url="http://localhost:9", model="a-model"
    )


def test_anthropic_text_is_read_alongside_a_thinking_block(monkeypatch):
    monkeypatch.setattr(
        ai, "_post",
        lambda *args, **kwargs: {
            "content": [
                {"type": "thinking", "thinking": "considering"},
                {"type": "text", "text": '{"title": "Song"}'},
            ],
            "stop_reason": "end_turn",
        },
    )
    assert ai.chat(_settings(), "system", "user") == '{"title": "Song"}'


def test_a_reply_cut_off_by_the_token_limit_is_explained(monkeypatch):
    # A reasoning model can spend the whole budget thinking and never write
    # an answer. That used to surface as "the AI returned an empty answer".
    monkeypatch.setattr(
        ai, "_post",
        lambda *args, **kwargs: {
            "content": [{"type": "thinking", "thinking": "still thinking"}],
            "stop_reason": "max_tokens",
        },
    )
    with pytest.raises(AIError) as caught:
        ai.chat(_settings(), "system", "user")
    assert "ran out of room" in caught.value.args[0]


def test_the_anthropic_request_asks_for_a_reasoning_sized_budget(monkeypatch):
    seen = {}

    def fake_post(settings, path, payload, **_kwargs):
        seen.update(payload)
        return {"content": [{"type": "text", "text": "ok"}]}

    monkeypatch.setattr(ai, "_post", fake_post)
    ai.chat(_settings(), "system", "user")
    assert seen["max_tokens"] >= 8192


def test_openai_content_parts_are_joined(monkeypatch):
    monkeypatch.setattr(
        ai, "_post",
        lambda *args, **kwargs: {
            "choices": [{
                "message": {"content": [
                    {"type": "text", "text": '{"title": '},
                    {"type": "text", "text": '"Song"}'},
                ]},
                "finish_reason": "stop",
            }]
        },
    )
    assert ai.chat(_settings("openai"), "s", "u") == '{"title": "Song"}'


def test_an_openai_reply_cut_off_for_length_is_explained(monkeypatch):
    monkeypatch.setattr(
        ai, "_post",
        lambda *args, **kwargs: {
            "choices": [{"message": {"content": ""}, "finish_reason": "length"}]
        },
    )
    with pytest.raises(AIError) as caught:
        ai.chat(_settings("openai"), "s", "u")
    assert "ran out of room" in caught.value.args[0]


def test_a_model_with_a_smaller_output_cap_is_retried(monkeypatch):
    """Older models reject max_tokens=8192; the request is retried at 4096."""
    seen = []

    def fake_post(settings, path, payload, **_kwargs):
        seen.append(payload["max_tokens"])
        if payload["max_tokens"] > 4096:
            raise AIError(
                "The AI server returned an error (400).",
                "max_tokens: 8192 > 4096, which is the maximum allowed",
            )
        return {"content": [{"type": "text", "text": "ok"}]}

    monkeypatch.setattr(ai, "_post", fake_post)
    assert ai.chat(_settings(), "s", "u") == "ok"
    # The first, tiny request is the probe that finds the wire format.
    assert seen[-2:] == [8192, 4096]


def test_an_unrelated_400_is_not_retried(monkeypatch):
    seen = []

    def fake_post(settings, path, payload, **_kwargs):
        seen.append(payload["max_tokens"])
        raise AIError("The AI server returned an error (400).", "bad request")

    monkeypatch.setattr(ai, "_post", fake_post)
    with pytest.raises(AIError):
        ai._chat_as(_settings(), "anthropic", "s", "u")
    assert seen == [8192]


# ------------------------------------------------------------ addresses

def test_a_v1_address_does_not_become_v1_v1():
    """The app tells people to type a /v1 address, so that has to work."""
    settings = AISettings(
        provider="openai", base_url="https://api.example.com/v1", model="m"
    )
    assert ai._endpoint(settings, "/v1/chat/completions") == \
        "https://api.example.com/v1/chat/completions"


def test_a_pasted_full_endpoint_is_accepted():
    for base in (
        "https://api.example.com/v1/chat/completions",
        "https://api.example.com/v1/models",
    ):
        settings = AISettings(provider="openai", base_url=base, model="m")
        assert ai._endpoint(settings, "/v1/chat/completions") == \
            "https://api.example.com/v1/chat/completions"


def test_anthropic_accepts_a_v1_address():
    settings = AISettings(
        provider="anthropic", base_url="https://api.anthropic.com/v1",
        model="m",
    )
    assert ai._endpoint(settings, "/v1/messages") == \
        "https://api.anthropic.com/v1/messages"


def test_ollama_accepts_an_api_address():
    for base in ("http://localhost:11434/api",
                 "http://localhost:11434/api/chat",
                 "http://localhost:11434/"):
        settings = AISettings(provider="ollama", base_url=base, model="m")
        assert ai._endpoint(settings, "/api/chat") == \
            "http://localhost:11434/api/chat"


def test_a_proxy_path_is_left_alone():
    settings = AISettings(
        provider="openai", base_url="https://host/proxy/v1", model="m"
    )
    assert ai._endpoint(settings, "/v1/chat/completions") == \
        "https://host/proxy/v1/chat/completions"


def test_an_empty_address_is_still_an_error():
    with pytest.raises(AIError):
        ai._endpoint(AISettings(provider="openai", base_url="", model="m"),
                     "/v1/chat/completions")


# -------------------------------------------------- finding the right API

def fake_server(monkeypatch, speaks, calls=None):
    """A server that answers only on the paths of the formats in ``speaks``."""
    def post(settings, path, payload, **_kwargs):
        if calls is not None:
            calls.append(path)
        if path == "/v1/messages" and "anthropic" in speaks:
            return {"content": [{"type": "text", "text": "ok"}],
                    "stop_reason": "end_turn"}
        if path == "/v1/chat/completions" and "openai" in speaks:
            return {"choices": [{"message": {"content": "ok"},
                                 "finish_reason": "stop"}]}
        if path == "/api/chat" and "ollama" in speaks:
            return {"message": {"content": "ok"}, "done_reason": "stop"}
        raise AIError(
            "The AI server does not have that model or address.",
            "Check the model name and the server address in Settings, AI.",
            status=404,
        )

    monkeypatch.setattr(ai, "_post", post)


def test_a_server_that_only_speaks_anthropic_works_when_openai_was_picked(
    monkeypatch,
):
    """A proxy that serves only /v1/messages must work anyway."""
    fake_server(monkeypatch, {"anthropic"})
    settings = AISettings(
        provider="openai", base_url="https://proxy.test", model="m"
    )
    assert ai.chat(settings, "s", "u") == "ok"
    assert ai.detected_dialect(settings) == "anthropic"


def test_a_server_that_only_speaks_openai_works_when_anthropic_was_picked(
    monkeypatch,
):
    fake_server(monkeypatch, {"openai"})
    settings = AISettings(
        provider="anthropic", base_url="https://api.example.com/v1", model="m"
    )
    assert ai.chat(settings, "s", "u") == "ok"
    assert ai.detected_dialect(settings) == "openai"


def test_auto_tries_ollama_first_for_a_local_address(monkeypatch):
    calls = []
    fake_server(monkeypatch, {"ollama"}, calls)
    settings = AISettings(
        provider="auto", base_url="http://localhost:11434", model="m"
    )
    assert ai.chat(settings, "s", "u") == "ok"
    assert calls[0] == "/api/chat"
    assert ai.detected_dialect(settings) == "ollama"


def test_auto_works_with_no_provider_at_all(monkeypatch):
    fake_server(monkeypatch, {"openai"})
    settings = AISettings(
        provider="auto", base_url="https://api.example.com", model="m"
    )
    assert ai.chat(settings, "s", "u") == "ok"


def test_the_format_is_found_once_and_then_remembered(monkeypatch):
    calls = []
    fake_server(monkeypatch, {"anthropic"}, calls)
    settings = AISettings(
        provider="openai", base_url="https://proxy.test", model="m"
    )
    ai.chat(settings, "s", "u")
    after_first = len(calls)
    ai.chat(settings, "s", "u")
    # The second call goes straight to the format that worked: no probing.
    assert len(calls) - after_first == 1


def test_a_changed_address_is_probed_again(monkeypatch):
    fake_server(monkeypatch, {"anthropic"})
    first = AISettings(
        provider="openai", base_url="https://one.test", model="m"
    )
    ai.chat(first, "s", "u")
    assert ai.detected_dialect(first) == "anthropic"

    other = AISettings(provider="openai", base_url="https://two.test", model="m")
    assert ai.detected_dialect(other) is None


def test_list_models_falls_back_to_another_format(monkeypatch):
    def get(settings, dialect, path):
        if dialect == "anthropic":
            return {"data": [{"id": "claude-x"}, {"id": "claude-y"}]}
        raise AIError("That server does not list its models.", "",
                      status=404)

    monkeypatch.setattr(ai, "_get", get)
    settings = AISettings(
        provider="openai", base_url="https://proxy.test", model="m"
    )
    assert ai.list_models(settings) == ["claude-x", "claude-y"]


def test_list_models_returns_nothing_when_the_server_has_none(monkeypatch):
    monkeypatch.setattr(
        ai, "_get", lambda settings, dialect, path: {"models": []}
    )
    settings = AISettings(
        provider="ollama", base_url="http://localhost:11434", model="m"
    )
    assert ai.list_models(settings) == []


def test_when_every_format_fails_the_error_says_what_was_tried(monkeypatch):
    fake_server(monkeypatch, set())
    settings = AISettings(
        provider="openai", base_url="https://proxy.test", model="m"
    )
    with pytest.raises(AIError) as caught:
        ai.chat(settings, "s", "u")
    hint = caught.value.args[1]
    assert "Tried:" in hint
    assert "Anthropic-style" in hint
    assert "Ollama" in hint


def test_an_unreachable_server_is_reported_without_trying_every_format(
    monkeypatch,
):
    calls = []

    def post(settings, path, payload, **_kwargs):
        calls.append(path)
        raise AIError("Could not reach the AI server.", "Check the address.")

    monkeypatch.setattr(ai, "_post", post)
    settings = AISettings(
        provider="openai", base_url="https://proxy.test", model="m"
    )
    with pytest.raises(AIError) as caught:
        ai.chat(settings, "s", "u")
    assert caught.value.status is None
    assert calls == ["/v1/chat/completions"]


def test_a_wrong_key_is_not_reported_as_a_missing_address(monkeypatch):
    """Every path rejects the key, so the key is what gets reported."""
    def post(settings, path, payload, **_kwargs):
        if path == "/api/chat":
            raise AIError("The AI server does not have that model or address.",
                          "", status=404)
        raise AIError("The AI server rejected the API key.",
                      "Check the key in Settings, AI.", status=401)

    monkeypatch.setattr(ai, "_post", post)
    settings = AISettings(
        provider="openai", base_url="https://proxy.test", model="m"
    )
    with pytest.raises(AIError) as caught:
        ai.chat(settings, "s", "u")
    assert "API key" in caught.value.args[0]
    assert caught.value.status == 401


def test_a_path_that_rejects_the_key_does_not_stop_the_search(monkeypatch):
    """A proxy can 401 one path and serve another; the working one wins."""
    def post(settings, path, payload, **_kwargs):
        if path == "/v1/messages":
            return {"content": [{"type": "text", "text": "ok"}],
                    "stop_reason": "end_turn"}
        raise AIError("The AI server rejected the API key.",
                      "Check the key in Settings, AI.", status=401)

    monkeypatch.setattr(ai, "_post", post)
    settings = AISettings(
        provider="auto", base_url="http://127.0.0.1:8141", model="m"
    )
    assert ai.chat(settings, "s", "u") == "ok"
    assert ai.detected_dialect(settings) == "anthropic"


def test_auto_is_an_allowed_provider():
    from musicdlp.core.config import AI_PROVIDERS, Settings

    assert "auto" in AI_PROVIDERS
    assert Settings().ai_provider == "auto"
    assert Settings(ai_provider="nonsense").validate() is None
    assert Settings(ai_provider="nonsense").ai_provider == "auto"


# ------------------------------------------------------ the custom prompt

def test_extra_instructions_are_added_to_the_naming_prompt(monkeypatch):
    seen = {}

    def fake_post(settings, path, payload, **_kwargs):
        seen["system"] = payload.get("system") or payload["messages"][0]["content"]
        return {"content": [{"type": "text",
                             "text": '{"title": "Song", "artist": "", '
                                     '"album": "", "confidence": 0.9}'}],
                "stop_reason": "end_turn"}

    monkeypatch.setattr(ai, "_post", fake_post)
    settings = AISettings(
        provider="anthropic", base_url="https://x.test", model="m",
        custom_prompt="Always keep the vocaloid singer in the artist field.",
    )
    ai.suggest_names(settings, title="Some Song")
    assert "Always keep the vocaloid singer" in seen["system"]
    # The JSON contract has to survive, or nothing can be parsed.
    assert '{"title"' in seen["system"]


def test_no_extra_instructions_leaves_the_prompt_alone():
    base = ai.SYSTEM_PROMPT
    assert ai._system_prompt(AISettings(), base) == base


def test_the_naming_prompt_explains_brackets():
    prompt = ai.SYSTEM_PROMPT
    assert "Brackets" in prompt
    assert "Vocaloid" in prompt
    assert "Original" in prompt


def test_the_translation_prompt_explains_brackets():
    from musicdlp.services.translate import SYSTEM_PROMPT as prompt

    assert "Brackets" in prompt
    assert "Vocaloid" in prompt
    assert "artist credit" in prompt


def test_extra_instructions_reach_the_translation_prompt(monkeypatch):
    from musicdlp.services import translate as translate_module

    seen = {}

    def fake_chat(settings, system, user):
        seen["system"] = system
        return '{"items": [{"id": "1", "title": "X", "confidence": 0.9}]}'

    monkeypatch.setattr(translate_module, "chat", fake_chat)
    translate_module.translate_batch(
        AISettings(custom_prompt="Prefer the official English title."),
        [(1, "T", "A")], language="Japanese",
    )
    assert "Prefer the official English title" in seen["system"]


# ------------------------------------ an address with its own API version

def _url(base, path, provider="openai"):
    return ai._endpoint(
        AISettings(provider=provider, base_url=base, model="m"), path
    )


def test_an_address_with_its_own_version_keeps_it():
    """z.ai publishes https://api.z.ai/api/paas/v4 and wants no /v1."""
    base = "https://api.z.ai/api/paas/v4"
    assert _url(base, "/v1/chat/completions") == \
        "https://api.z.ai/api/paas/v4/chat/completions"
    assert _url(base, "/v1/models") == "https://api.z.ai/api/paas/v4/models"


def test_a_versioned_address_with_a_trailing_slash():
    assert _url("https://api.z.ai/api/paas/v4/", "/v1/chat/completions") == \
        "https://api.z.ai/api/paas/v4/chat/completions"


def test_a_pasted_versioned_endpoint_is_trimmed():
    for base in (
        "https://api.z.ai/api/paas/v4/chat/completions",
        "https://api.z.ai/api/paas/v4/models",
    ):
        assert _url(base, "/v1/chat/completions") == \
            "https://api.z.ai/api/paas/v4/chat/completions"
        assert _url(base, "/v1/models") == \
            "https://api.z.ai/api/paas/v4/models"


def test_a_beta_version_is_kept():
    assert _url("https://host/v1beta", "/v1/chat/completions") == \
        "https://host/v1beta/chat/completions"
    assert _url("https://host/v1beta", "/v1/models") == \
        "https://host/v1beta/models"


def test_a_version_address_does_not_change_ollama_paths():
    assert _url("https://api.z.ai/api/paas/v4", "/api/chat", "ollama") == \
        "https://api.z.ai/api/paas/v4/api/chat"


def test_the_anthropic_endpoint_works_on_a_versioned_address():
    assert _url("https://api.z.ai/api/paas/v4", "/v1/messages", "anthropic") \
        == "https://api.z.ai/api/paas/v4/messages"


def test_ordinary_addresses_are_untouched_by_the_version_rule():
    """Everything that worked before still produces the same URL."""
    cases = [
        ("https://api.example.com", "https://api.example.com/v1/chat/completions"),
        ("https://api.example.com/v1",
         "https://api.example.com/v1/chat/completions"),
        ("https://api.example.com/v1/chat/completions",
         "https://api.example.com/v1/chat/completions"),
        ("https://host/proxy/v1", "https://host/proxy/v1/chat/completions"),
    ]
    for base, expected in cases:
        assert _url(base, "/v1/chat/completions") == expected


def test_an_anthropic_address_is_untouched():
    assert _url("https://api.anthropic.com", "/v1/messages", "anthropic") == \
        "https://api.anthropic.com/v1/messages"
    assert _url("https://api.anthropic.com/v1", "/v1/messages", "anthropic") \
        == "https://api.anthropic.com/v1/messages"
    # /api/anthropic is a path segment, not a version.
    assert _url("https://api.z.ai/api/anthropic", "/v1/messages",
                "anthropic") == "https://api.z.ai/api/anthropic/v1/messages"


def test_ollama_addresses_are_untouched():
    assert _url("http://localhost:11434", "/api/chat", "ollama") == \
        "http://localhost:11434/api/chat"
    assert _url("http://localhost:11434/api", "/api/chat", "ollama") == \
        "http://localhost:11434/api/chat"
    assert _url("https://ollama.com", "/api/chat", "ollama") == \
        "https://ollama.com/api/chat"


def test_detection_finds_a_server_with_its_own_version(monkeypatch):
    """The whole flow: a v4 server is reached without a doubled path."""
    urls = []

    class Response:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "ok"},
                                 "finish_reason": "stop"}]}

    def fake_post(url, **kwargs):
        urls.append(url)
        if url.endswith("/api/paas/v4/chat/completions"):
            return Response()
        raise ai.requests.exceptions.ConnectionError("404")

    monkeypatch.setattr(ai.requests, "post", fake_post)
    settings = AISettings(
        provider="auto", base_url="https://api.z.ai/api/paas/v4", model="m"
    )
    assert ai.chat(settings, "s", "u") == "ok"
    assert urls and all("/v1/" not in url for url in urls), urls
    assert urls[-1] == "https://api.z.ai/api/paas/v4/chat/completions"


def test_the_failure_hint_names_the_address_tried(monkeypatch):
    """When every format fails, the URL is what explains why."""

    def post(settings, path, payload, **_kwargs):
        raise AIError(
            "The AI server does not have that model or address.",
            "Check the model name and the server address in Settings, AI.",
            status=404,
        )

    monkeypatch.setattr(ai, "_post", post)
    settings = AISettings(
        provider="auto", base_url="https://api.z.ai/api/paas/v4",
        model="glm-5.3-flash",
    )
    with pytest.raises(AIError) as caught:
        ai.chat(settings, "s", "u")
    hint = caught.value.args[1]
    assert "Last address tried:" in hint
    assert "api/paas/v4" in hint
    # And it is the address the app now builds, without a doubled version.
    assert "/v4/v1/" not in hint


def test_a_reply_returned_as_an_object_is_understood(monkeypatch):
    """A gateway may hand back the answer already parsed."""
    monkeypatch.setattr(
        ai, "_post",
        lambda *args, **kwargs: {
            "choices": [{
                "message": {"content": {"title": "At the Gates",
                                        "artist": "Drake",
                                        "album": "", "confidence": 0.9}},
                "finish_reason": "stop",
            }]
        },
    )
    result = ai.suggest_names(_settings("openai"), title="Whatever")
    assert result.title == "At the Gates"
    assert result.artist == "Drake"


def test_a_reply_with_object_parts_is_understood(monkeypatch):
    monkeypatch.setattr(
        ai, "_post",
        lambda *args, **kwargs: {
            "choices": [{
                "message": {"content": [
                    {"text": '{"title": "One", '},
                    {"text": '"artist": "Two", "confidence": 0.8}'},
                ]},
                "finish_reason": "stop",
            }]
        },
    )
    result = ai.suggest_names(_settings("openai"), title="Whatever")
    assert result.title == "One"
    assert result.artist == "Two"
