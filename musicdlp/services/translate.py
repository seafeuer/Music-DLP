"""Translate song titles and artist names into another language.

This is a separate action from naming: the song is already called something,
and the user wants that name in Japanese, Hebrew, or another language. The
model returns both the target script (夜に駆ける) and a Latin transliteration
(Yoru ni Kakeru), because a library needs one or the other depending on how
the user reads it.

Nothing here writes to the database. The dialog decides what to apply.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from musicdlp.services.ai import AIError, AISettings, _system_prompt, chat

logger = logging.getLogger("musicdlp.translate")

# Shown in the language picker. The user can also type any language name.
LANGUAGES = (
    "Japanese",
    "Hebrew",
    "Korean",
    "Chinese (Simplified)",
    "Chinese (Traditional)",
    "Spanish",
    "French",
    "German",
    "Italian",
    "Portuguese",
    "Russian",
    "Arabic",
    "Hindi",
    "English",
)

# How the two halves are combined.
STYLE_TRANSLATED = "translated"
STYLE_TRANSLITERATED = "transliterated"
STYLE_BOTH = "both"

STYLES = (STYLE_TRANSLATED, STYLE_TRANSLITERATED, STYLE_BOTH)

STYLE_LABELS = {
    STYLE_TRANSLATED: "Own script",
    STYLE_TRANSLITERATED: "Latin letters",
    STYLE_BOTH: "Both (script + Latin)",
}

# A batch this size keeps one request cheap and still saves most of the
# round trips. Bigger batches risk the model losing track of ids.
BATCH_SIZE = 10

# Upload titles can be enormous; a prompt does not need all of it.
MAX_TITLE_CHARS = 300

SYSTEM_PROMPT = """You translate song titles and artist names for a music library.

Reply with JSON only, in exactly this shape:
{"items": [{"id": "1", "title": "...", "title_latin": "...", "artist": "...",
"artist_latin": "...", "confidence": 0.0, "already_target": false}]}

Rules:
- "title" is the song title written in the target language's own script.
- The "title" given for each song is the uploader's own title, and it is
  the reliable source. Work out the real song title from it, applying the
  rules below. Some songs also come with a "name already in the library";
  that is a guess made by simple rules and may be wrong — a Japanese
  uploader often writes "Title - Vocalist", which those rules read
  backwards. When the two disagree, trust the upload title.
- "title_latin" is that same title in Latin letters: romanisation for
  Japanese, Chinese, Korean, Hebrew, Arabic, Russian; for languages that
  already use Latin letters it is the same string as "title".
- "artist" and "artist_latin" follow the same rule. Foreign artist names are
  written as a native listener would expect them (katakana for foreign names
  in Japanese, Hebrew letters for foreign names in Hebrew), not left in
  English.
- A song title is a NAME, not a sentence. Use the form the song is actually
  known by in the target language. An English title in Japanese is normally
  the katakana transliteration (プラスチック・ラブ), not a word-for-word
  translation of its meaning. Translate the meaning only when the title is a
  plain descriptive phrase, and never when the title is a person's name, a
  place, or an idiom.
- Keep it natural as a song title. Do not add punctuation that was not there.
- Never translate or change a name that is already in the target language;
  copy it through unchanged.
- Set "already_target" to true when the title was already in the target
  language.
- "confidence" is 0.0-1.0. Use below 0.5 when you are unsure which form the
  song is known by.
- Never add channel names, quality tags, or explanations.

Brackets and parentheses are a qualifier, not part of the song name. Work
out what each one means, and put it where it belongs rather than translating
it as part of the title:
- A performer named in brackets is an artist credit. Move it to "artist",
  translated the same way the rest of the artist is. This includes a
  Vocaloid or UTAU singer (Hatsune Miku, 初音ミク, Kagamine Rin, GUMI, IA,
  Teto), a featured artist, and a credited cover singer. A vocaloid singer
  is the voice of the recording, so it belongs in the artist even when a
  producer is also credited.
- "(Original)" or "(オリジナル)" stays beside the title only for Vocaloid,
  UTAU, or similar songs that have many covers, where "original" is what
  tells this recording apart from them. Translate it into the target
  language and keep it in brackets after the title. For every other song,
  drop it: an original needs no label.
- A cover marker ("cover", "covered by", "歌ってみた", "翻唱") is kept in
  the title, translated, because it marks a different recording.
- A marker that names a different recording — instrumental, off-vocal,
  remix, live, acoustic, sped up, nightcore — is kept, translated, for the
  same reason.
- Anything that is only upload noise ("official video", "HD", a year, a
  channel name) is dropped.
"""


@dataclass
class Translation:
    """One song's translated names."""

    title: str = ""
    title_latin: str = ""
    artist: str = ""
    artist_latin: str = ""
    confidence: float = 0.0
    already_target: bool = False
    reason: str = ""

    @property
    def usable(self) -> bool:
        return bool(self.title.strip()) and self.confidence >= 0.3


def combine(native: str, latin: str, style: str) -> str:
    """Join the two forms the way the chosen style asks for."""
    native = (native or "").strip()
    latin = (latin or "").strip()

    if style == STYLE_TRANSLITERATED:
        return latin or native
    if style == STYLE_TRANSLATED:
        return native or latin
    if not native:
        return latin
    if not latin or latin == native:
        return native
    return f"{native} ({latin})"


def _parse_items(reply: str) -> list[dict]:
    """Pull the item list out of a model reply, tolerating decoration."""
    text = (reply or "").strip()
    if not text:
        raise AIError("The AI returned an empty answer.")

    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()

    data = None
    for pattern in (r"\{.*\}", r"\[.*\]"):
        match = re.search(pattern, text, re.DOTALL)
        if not match:
            continue
        try:
            data = json.loads(match.group(0))
        except ValueError:
            continue
        break

    if data is None:
        raise AIError("The AI did not return usable JSON.", text[:200])

    if isinstance(data, dict):
        for key in ("items", "results", "songs", "translations"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        # A single object was returned instead of a list.
        return [data]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    raise AIError("The AI returned an unexpected shape.")


def _text(item: dict, *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _number(item: dict, key: str, default: float = 0.0) -> float:
    try:
        value = float(item.get(key))
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, value))


def _to_translation(item: dict) -> Translation:
    title = _text(item, "title", "translated", "translated_title")
    title_latin = _text(item, "title_latin", "romaji", "romanized", "translit")
    return Translation(
        title=title,
        title_latin=title_latin or title,
        artist=_text(item, "artist", "artist_native", "translated_artist"),
        artist_latin=_text(item, "artist_latin", "artist_romaji", "artist_romanized"),
        confidence=_number(item, "confidence", 0.8),
        already_target=bool(item.get("already_target")),
    )


def _build_prompt(
    items: list[tuple[int, str, str]],
    language: str,
    translate_artist: bool,
    current_titles: Optional[dict[int, str]] = None,
) -> str:
    lines = [f"Target language: {language}", ""]
    if not translate_artist:
        lines.append(
            "Leave the artist names exactly as given; copy them through "
            "unchanged into both artist fields."
        )
        lines.append("")
    lines.append("Songs:")
    for track_id, title, artist in items:
        line = (
            f'- id "{track_id}": title "{title[:MAX_TITLE_CHARS]}", '
            f'artist "{artist[:MAX_TITLE_CHARS]}"'
        )
        # The library's own name may be a guess made by the built-in rules.
        # It is shown so the model can tell, but the upload title is what
        # the answer must be based on.
        guess = (current_titles or {}).get(track_id, "").strip()
        if guess and guess != title.strip():
            line += f', name already in the library "{guess[:MAX_TITLE_CHARS]}"'
        lines.append(line)
    lines.append("")
    lines.append("Return the JSON object now.")
    return "\n".join(lines)


def translate_batch(
    settings: AISettings,
    items: Iterable[tuple[int, str, str]],
    *,
    language: str,
    translate_artist: bool = True,
    current_titles: Optional[dict[int, str]] = None,
) -> dict[int, Translation]:
    """Translate several songs in one request.

    ``items`` is ``(track_id, title, artist)``, where ``title`` should be
    the title as the uploader wrote it. ``current_titles`` maps a track id
    to the name the library shows, when that is a guess the model should
    be able to see past. Items the model leaves out are simply absent from
    the result; the caller decides what to do with them.
    """
    batch = [(int(tid), title or "", artist or "") for tid, title, artist in items]
    if not batch:
        return {}

    reply = chat(
        settings,
        _system_prompt(settings, SYSTEM_PROMPT),
        _build_prompt(batch, language, translate_artist, current_titles),
    )

    wanted = {track_id for track_id, _title, _artist in batch}
    results: dict[int, Translation] = {}
    for item in _parse_items(reply):
        raw_id = item.get("id")
        try:
            track_id = int(str(raw_id).strip())
        except (TypeError, ValueError):
            continue
        if track_id not in wanted:
            continue
        results[track_id] = _to_translation(item)

    if not results:
        raise AIError(
            "The AI did not translate any of these songs.",
            "Try again, or translate fewer at a time.",
        )
    return results


def translate_one(
    settings: AISettings,
    *,
    title: str,
    artist: str = "",
    language: str,
    translate_artist: bool = True,
    track_id: int = 0,
) -> Translation:
    """Translate a single song."""
    results = translate_batch(
        settings,
        [(track_id, title, artist)],
        language=language,
        translate_artist=translate_artist,
    )
    result = results.get(track_id)
    if result is None:
        raise AIError(
            "The AI did not translate this song.",
            "Try again, or name it first so the model has more to go on.",
        )
    return result


def translate_many(
    settings: AISettings,
    items: list[tuple[int, str, str]],
    *,
    language: str,
    translate_artist: bool = True,
    current_titles: Optional[dict[int, str]] = None,
    on_result: Optional[Callable[[int, Translation], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> tuple[list[tuple[int, Translation]], Optional[AIError]]:
    """Translate a list, in batches, reporting each song as it lands.

    Returns the translations that succeeded and the error that stopped the
    run, if one did. Songs already translated stay usable after a failure,
    which is what the review table needs.
    """
    done: list[tuple[int, Translation]] = []
    pending = list(items)

    while pending:
        if should_stop is not None and should_stop():
            break

        batch = pending[:BATCH_SIZE]
        pending = pending[BATCH_SIZE:]

        try:
            results = translate_batch(
                settings,
                batch,
                language=language,
                translate_artist=translate_artist,
                current_titles=current_titles,
            )
        except AIError as exc:
            # One bad batch should not lose the rest. Fall back to single
            # requests for this batch, then stop if even those fail.
            results, error = _retry_individually(
                settings,
                batch,
                language=language,
                translate_artist=translate_artist,
                current_titles=current_titles,
                should_stop=should_stop,
            )
            for track_id, translation in results.items():
                done.append((track_id, translation))
                if on_result is not None:
                    on_result(track_id, translation)
            if error is not None:
                return done, error
            continue

        # A model can quietly leave a song out of a batch it otherwise
        # answered. Ask about those one at a time rather than losing them.
        missing = [item for item in batch if item[0] not in results]
        if missing:
            retried, error = _retry_individually(
                settings,
                missing,
                language=language,
                translate_artist=translate_artist,
                current_titles=current_titles,
                should_stop=should_stop,
            )
            results.update(retried)
            if error is not None:
                for track_id, _title, _artist in batch:
                    translation = results.get(track_id)
                    if translation is None:
                        continue
                    done.append((track_id, translation))
                    if on_result is not None:
                        on_result(track_id, translation)
                return done, error

        for track_id, _title, _artist in batch:
            translation = results.get(track_id)
            if translation is None:
                continue
            done.append((track_id, translation))
            if on_result is not None:
                on_result(track_id, translation)

    return done, None


def _retry_individually(
    settings: AISettings,
    batch: list[tuple[int, str, str]],
    *,
    language: str,
    translate_artist: bool,
    should_stop: Optional[Callable[[], bool]],
    current_titles: Optional[dict[int, str]] = None,
) -> tuple[dict[int, Translation], Optional[AIError]]:
    results: dict[int, Translation] = {}
    if len(batch) == 1:
        try:
            return translate_batch(
                settings, batch,
                language=language, translate_artist=translate_artist,
                current_titles=current_titles,
            ), None
        except AIError as exc:
            return {}, exc

    for item in batch:
        if should_stop is not None and should_stop():
            break
        try:
            one = translate_batch(
                settings, [item],
                language=language, translate_artist=translate_artist,
                current_titles=current_titles,
            )
        except AIError as exc:
            return results, exc
        results.update(one)
    return results, None
