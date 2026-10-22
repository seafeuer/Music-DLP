"""Translation service and dialog, without touching the network."""

import json

import pytest
from PySide6.QtCore import Qt

import musicdlp.services.translate as translate_module
from musicdlp.core.config import Settings
from musicdlp.core.db import Database
from musicdlp.core.models import Source, Track, TrackRow
from musicdlp.services.ai import AIError, AISettings
from musicdlp.services.translate import (
    STYLE_BOTH, STYLE_TRANSLATED, STYLE_TRANSLITERATED, Translation,
    combine, translate_batch, translate_many, translate_one,
)
from musicdlp.ui.dialogs.translate import (
    COL_ARTIST, COL_NOTE, COL_TITLE, COL_USE, TranslationDialog,
)

SETTINGS = AISettings(
    provider="anthropic", base_url="https://example.test",
    model="test-model", api_key="k",
)


def reply(items):
    return json.dumps({"items": items}, ensure_ascii=False)


# ------------------------------------------------------------------ combine


def test_combine_translated_uses_the_native_script():
    assert combine("夜に駆ける", "Yoru ni Kakeru", STYLE_TRANSLATED) == "夜に駆ける"


def test_combine_transliterated_uses_latin_letters():
    assert combine("夜に駆ける", "Yoru ni Kakeru", STYLE_TRANSLITERATED) == (
        "Yoru ni Kakeru"
    )


def test_combine_both_shows_native_with_latin_in_brackets():
    assert combine("夜に駆ける", "Yoru ni Kakeru", STYLE_BOTH) == (
        "夜に駆ける (Yoru ni Kakeru)"
    )


def test_combine_does_not_repeat_identical_forms():
    assert combine("Hola", "Hola", STYLE_BOTH) == "Hola"


def test_combine_falls_back_when_one_side_is_missing():
    assert combine("", "Yoru ni Kakeru", STYLE_BOTH) == "Yoru ni Kakeru"
    assert combine("夜に駆ける", "", STYLE_BOTH) == "夜に駆ける"


# -------------------------------------------------------------------- parse


def test_batch_maps_results_by_id(monkeypatch):
    captured = {}

    def fake_chat(settings, system, user):
        captured["prompt"] = user
        return reply([
            {"id": "7", "title": "夜に駆ける", "title_latin": "Yoru ni Kakeru",
             "artist": "YOASOBI", "artist_latin": "YOASOBI",
             "confidence": 0.95, "already_target": False},
        ])

    monkeypatch.setattr(translate_module, "chat", fake_chat)
    results = translate_batch(
        SETTINGS, [(7, "夜に駆ける", "YOASOBI")], language="Japanese"
    )

    assert 7 in results
    assert results[7].title == "夜に駆ける"
    assert results[7].confidence == pytest.approx(0.95)
    assert "Target language: Japanese" in captured["prompt"]


def test_reply_wrapped_in_a_code_fence_is_still_read(monkeypatch):
    monkeypatch.setattr(
        translate_module, "chat",
        lambda *a, **k: 'Here you go:\n```json\n'
        + reply([{"id": "1", "title": "שירת הים", "title_latin": "Shirat HaYam"}])
        + "\n```\nEnjoy!",
    )
    results = translate_batch(SETTINGS, [(1, "Song of the Sea", "")],
                              language="Hebrew")
    assert results[1].title == "שירת הים"


def test_a_bare_list_is_accepted(monkeypatch):
    monkeypatch.setattr(
        translate_module, "chat",
        lambda *a, **k: json.dumps([{"id": "3", "title": "レモン",
                                     "title_latin": "Remon"}]),
    )
    results = translate_batch(SETTINGS, [(3, "Lemon", "")], language="Japanese")
    assert results[3].title == "レモン"


def test_unknown_ids_are_ignored(monkeypatch):
    monkeypatch.setattr(
        translate_module, "chat",
        lambda *a, **k: reply([
            {"id": "1", "title": "レモン"},
            {"id": "999", "title": "Not asked for"},
        ]),
    )
    results = translate_batch(SETTINGS, [(1, "Lemon", "")], language="Japanese")
    assert set(results) == {1}


def test_a_reply_with_no_matching_items_is_an_error(monkeypatch):
    monkeypatch.setattr(translate_module, "chat", lambda *a, **k: "sorry")
    with pytest.raises(AIError):
        translate_batch(SETTINGS, [(1, "Lemon", "")], language="Japanese")


def test_translate_one_returns_the_single_result(monkeypatch):
    monkeypatch.setattr(
        translate_module, "chat",
        lambda *a, **k: reply([{"id": "5", "title": "唱",
                                "title_latin": "Shou", "confidence": 0.9}]),
    )
    result = translate_one(SETTINGS, title="Show", language="Japanese", track_id=5)
    assert isinstance(result, Translation)
    assert result.title == "唱"


# ------------------------------------------------------------------ batches


def test_many_translates_in_batches(monkeypatch):
    calls = []

    def fake_chat(settings, system, user):
        calls.append(user)
        # Echo back every id mentioned in the prompt.
        ids = [line.split('"')[1] for line in user.splitlines()
               if line.startswith("- id ")]
        return reply([
            {"id": track_id, "title": f"t{track_id}", "title_latin": f"t{track_id}"}
            for track_id in ids
        ])

    monkeypatch.setattr(translate_module, "chat", fake_chat)
    items = [(i, f"Song {i}", "Artist") for i in range(1, 24)]
    done, error = translate_many(SETTINGS, items, language="Japanese")

    assert error is None
    assert len(done) == 23
    assert len(calls) == 3  # 10 + 10 + 3


def test_many_falls_back_to_single_requests(monkeypatch):
    calls = []

    def fake_chat(settings, system, user):
        calls.append(user)
        if len([line for line in user.splitlines() if line.startswith("- id ")]) > 1:
            return "not json at all"
        track_id = [line for line in user.splitlines()
                    if line.startswith("- id ")][0].split('"')[1]
        return reply([{"id": track_id, "title": f"ok{track_id}"}])

    monkeypatch.setattr(translate_module, "chat", fake_chat)
    done, error = translate_many(
        SETTINGS, [(1, "A", ""), (2, "B", "")], language="Japanese"
    )

    assert error is None
    assert {track_id for track_id, _t in done} == {1, 2}
    assert len(calls) == 3  # one failed batch, then one request each


def test_many_keeps_earlier_results_when_the_model_dies(monkeypatch):
    def fake_chat(settings, system, user):
        lines = [line for line in user.splitlines() if line.startswith("- id ")]
        if len(lines) == 1 and '"1"' in lines[0]:
            return reply([{"id": "1", "title": "t1"}])
        raise AIError("The AI server took too long to answer.", "")

    monkeypatch.setattr(translate_module, "chat", fake_chat)
    items = [(i, f"Song {i}", "") for i in range(1, 4)]
    done, error = translate_many(SETTINGS, items, language="Japanese")

    assert [track_id for track_id, _t in done] == [1]
    assert error is not None


def test_many_retries_a_song_the_model_left_out(monkeypatch):
    """A model that answers a batch but skips an id must not lose that song."""
    calls = []

    def fake_chat(settings, system, user):
        lines = [line for line in user.splitlines() if line.startswith("- id ")]
        calls.append(len(lines))
        if len(lines) > 1:
            return reply([{"id": "1", "title": "t1"}])  # forgets song 2
        return reply([{"id": "2", "title": "t2"}])

    monkeypatch.setattr(translate_module, "chat", fake_chat)
    done, error = translate_many(
        SETTINGS, [(1, "A", ""), (2, "B", "")], language="Japanese"
    )

    assert error is None
    assert {track_id for track_id, _t in done} == {1, 2}
    assert calls == [2, 1]


def test_many_reports_each_result_as_it_lands(monkeypatch):
    monkeypatch.setattr(
        translate_module, "chat",
        lambda *a, **k: reply([{"id": "1", "title": "t1"},
                               {"id": "2", "title": "t2"}]),
    )
    seen = []
    translate_many(
        SETTINGS, [(1, "A", ""), (2, "B", "")], language="Japanese",
        on_result=lambda track_id, _t: seen.append(track_id),
    )
    assert sorted(seen) == [1, 2]


# ------------------------------------------------------------------- config


def test_translation_choices_round_trip(tmp_path):
    path = tmp_path / "config.json"
    settings = Settings(translate_language="Hebrew", translate_style="translated",
                        translate_artist=False)
    settings.save(path)

    loaded = Settings.load(path)
    assert loaded.translate_language == "Hebrew"
    assert loaded.translate_style == "translated"
    assert loaded.translate_artist is False


def test_unknown_style_falls_back_to_both():
    assert Settings(translate_style="nonsense").translate_style == "both"


def test_blank_language_falls_back_to_japanese():
    assert Settings(translate_language="  ").translate_language == "Japanese"


def test_anthropic_is_an_allowed_provider():
    assert Settings(ai_provider="anthropic").ai_provider == "anthropic"


# ------------------------------------------------------------------- dialog


def _row(track_id=1, title="夜に駆ける", artist="YOASOBI"):
    source = Source(
        id=1, url="u", kind="playlist", platform="youtube", external_id="x",
        title="A playlist", uploader="Channel", item_count=1,
        thumbnail_url=None, added_at=None, refreshed_at=None,
    )
    track = Track(
        id=track_id, platform="youtube", media_id=f"m{track_id}", url="u",
        source_index=None, title=title, uploader=artist, duration=200,
        thumbnail_url=None, description=None, status="converted", error=None,
        meta_title=None, meta_artist=None, meta_album=None, meta_track=None,
        raw_path=None, mp3_path=None, m4a_path=None, cover_path=None,
        downloaded_at=None, converted_at=None, synced_at=None,
    )
    return TrackRow(source=source, track=track, position=1)


def test_dialog_starts_empty_and_apply_is_disabled(qapp):
    dialog = TranslationDialog([_row()], ai_settings=SETTINGS)
    assert dialog.table.item(0, COL_TITLE).text() == ""
    assert not dialog.apply_button.isEnabled()
    assert dialog.table.item(0, COL_USE).checkState() == Qt.Unchecked


def test_dialog_fills_a_row_and_ticks_a_confident_translation(qapp):
    dialog = TranslationDialog([_row()], ai_settings=SETTINGS)
    dialog._apply_translation(
        1,
        Translation(title="夜に駆ける", title_latin="Yoru ni Kakeru",
                    artist="YOASOBI", artist_latin="YOASOBI",
                    confidence=0.95),
    )
    assert dialog.table.item(0, COL_TITLE).text() == "夜に駆ける (Yoru ni Kakeru)"
    assert dialog.table.item(0, COL_USE).checkState() == Qt.Checked
    assert dialog.apply_button.isEnabled()


def test_dialog_leaves_an_unsure_translation_unticked(qapp):
    dialog = TranslationDialog([_row()], ai_settings=SETTINGS)
    dialog._apply_translation(1, Translation(title="???", confidence=0.2))
    assert dialog.table.item(0, COL_USE).checkState() == Qt.Unchecked
    assert "Unsure" in dialog.table.item(0, COL_NOTE).text()


def test_dialog_leaves_a_song_already_in_the_language_alone(qapp):
    dialog = TranslationDialog([_row()], ai_settings=SETTINGS)
    dialog._apply_translation(
        1, Translation(title="夜に駆ける", confidence=0.9, already_target=True)
    )
    assert dialog.table.item(0, COL_USE).checkState() == Qt.Unchecked
    assert "Already" in dialog.table.item(0, COL_NOTE).text()


def test_dialog_restyles_when_the_style_changes(qapp):
    dialog = TranslationDialog([_row()], ai_settings=SETTINGS)
    dialog._apply_translation(
        1, Translation(title="夜に駆ける", title_latin="Yoru ni Kakeru",
                       confidence=0.95),
    )
    index = dialog.style_combo.findData(STYLE_TRANSLITERATED)
    dialog.style_combo.setCurrentIndex(index)
    assert dialog.table.item(0, COL_TITLE).text() == "Yoru ni Kakeru"


def test_dialog_keeps_a_hand_edited_row_when_the_style_changes(qapp):
    dialog = TranslationDialog([_row()], ai_settings=SETTINGS)
    dialog._apply_translation(
        1, Translation(title="夜に駆ける", title_latin="Yoru ni Kakeru",
                       confidence=0.95),
    )
    dialog.table.item(0, COL_TITLE).setText("My own name")
    index = dialog.style_combo.findData(STYLE_TRANSLATED)
    dialog.style_combo.setCurrentIndex(index)
    assert dialog.table.item(0, COL_TITLE).text() == "My own name"


def test_dialog_apply_returns_the_checked_rows(qapp):
    dialog = TranslationDialog([_row(), _row(track_id=2)], ai_settings=SETTINGS)
    dialog._apply_translation(1, Translation(title="夜に駆ける", confidence=0.9))
    dialog._apply_translation(2, Translation(title="レモン", confidence=0.9))
    dialog.table.item(1, COL_USE).setCheckState(Qt.Unchecked)

    dialog._apply()

    assert dialog.accepted_values == [(1, "夜に駆ける", "")]


def test_dialog_artist_checkbox_clears_the_artist_column(qapp):
    dialog = TranslationDialog([_row()], ai_settings=SETTINGS)
    dialog._apply_translation(
        1, Translation(title="夜に駆ける", artist="YOASOBI", confidence=0.9)
    )
    assert dialog.table.item(0, COL_ARTIST).text() == "YOASOBI"

    dialog.artist_check.setChecked(False)
    assert dialog.table.item(0, COL_ARTIST).text() == ""


def test_dialog_counts_results_across_polls(qapp):
    """Results land a few at a time; the final status must count them all."""
    rows = [_row(track_id=1), _row(track_id=2)]
    dialog = TranslationDialog(rows, ai_settings=SETTINGS)

    dialog._results.put(
        ("result", (1, Translation(title="一つ目", confidence=0.9)))
    )
    dialog._drain()
    dialog._results.put(
        ("result", (2, Translation(title="二つ目", confidence=0.9)))
    )
    dialog._results.put(("done", None))
    dialog._drain()

    assert "Translated 2 songs" in dialog.status.text()


def test_smart_titles_counts_ai_names_across_polls(qapp):
    from musicdlp.services.ai import AIResult
    from musicdlp.ui.dialogs.smart_titles import SmartTitlesDialog

    rows = [_row(track_id=1), _row(track_id=2)]
    for row in rows:
        row.track.smart_confidence = 0.1
    dialog = SmartTitlesDialog(rows, ai_settings=SETTINGS)

    dialog._results.put(
        ("result", (1, AIResult(title="One", confidence=0.9)))
    )
    dialog._drain()
    dialog._results.put(
        ("result", (2, AIResult(title="Two", confidence=0.9)))
    )
    dialog._results.put(("done", None))
    dialog._drain()

    assert "AI named 2 songs" in dialog.status.text()


# ------------------------------------------------- the upload title is the source

def test_the_prompt_carries_the_library_name_as_context_only():
    prompt = translate_module._build_prompt(
        [(1, "オーバーライド - 重音テトSV[吉田夜世]", "吉田夜世")],
        "English", True,
        {1: "重音テトsv[吉田夜世]"},
    )
    assert "オーバーライド - 重音テトSV[吉田夜世]" in prompt
    assert "name already in the library" in prompt


def test_no_library_name_is_added_when_it_matches():
    prompt = translate_module._build_prompt(
        [(1, "Same Title", "Artist")], "English", True, {1: "Same Title"}
    )
    assert "name already in the library" not in prompt


def test_the_prompt_says_the_upload_title_wins():
    from musicdlp.services.translate import SYSTEM_PROMPT

    assert "upload title" in SYSTEM_PROMPT
    assert "read\n  backwards" in SYSTEM_PROMPT or "backwards" in SYSTEM_PROMPT


def test_the_dialog_translates_from_the_upload_title(qapp, monkeypatch):
    """The library name may be a bad guess; it must not be the input."""
    row = _row(track_id=1, title="オーバーライド - 重音テトSV[吉田夜世]",
               artist="吉田夜世")
    row.track.meta_title = "重音テトsv[吉田夜世]"
    dialog = TranslationDialog([row], ai_settings=SETTINGS)

    seen = {}

    def fake_many(settings, items, **kwargs):
        seen["items"] = items
        seen["current"] = kwargs.get("current_titles")
        return [], None

    monkeypatch.setattr(
        "musicdlp.ui.dialogs.translate.translate_many", fake_many
    )
    dialog._start()
    dialog._stop.set()

    assert seen["items"][0][1] == "オーバーライド - 重音テトSV[吉田夜世]"
    assert seen["current"][1] == "重音テトsv[吉田夜世]"
