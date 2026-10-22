"""AI naming: local Ollama, Ollama cloud, or any compatible HTTP API.

The parser in ``smart_titles`` is deterministic and free. This module is the
optional second opinion for titles the parser cannot resolve, and it is only
ever used when the user asks for it.

Three wire formats are supported:

* ``ollama``    - POST {base}/api/chat            (local Ollama or ollama.com)
* ``openai``    - POST {base}/v1/chat/completions (any compatible endpoint)
* ``anthropic`` - POST {base}/v1/messages         (Anthropic-style API)

The provider is only a hint. An address and a key are enough: the first time
a server is used, each format is tried and the one that answers is
remembered, so a proxy that only speaks one of them still works even when
the wrong provider was picked in Settings.

Requests always carry a normal browser User-Agent. Some hosted endpoints sit
behind a proxy that rejects Python's default agent with a 403.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from typing import Optional

import requests

logger = logging.getLogger("musicdlp.ai")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

PROVIDER_AUTO = "auto"
PROVIDER_OLLAMA = "ollama"
PROVIDER_OPENAI = "openai"
PROVIDER_ANTHROPIC = "anthropic"

PROVIDERS = (PROVIDER_AUTO, PROVIDER_OLLAMA, PROVIDER_OPENAI, PROVIDER_ANTHROPIC)

# The wire formats, as opposed to the provider names shown in Settings.
DIALECTS = (PROVIDER_ANTHROPIC, PROVIDER_OPENAI, PROVIDER_OLLAMA)

DIALECT_LABELS = {
    PROVIDER_ANTHROPIC: "Anthropic-style API",
    PROVIDER_OPENAI: "[OI]-compatible API",
    PROVIDER_OLLAMA: "Ollama",
}

# Presets shown in Settings.
OLLAMA_LOCAL_URL = "http://localhost:11434"
OLLAMA_CLOUD_URL = "https://ollama.com"

# The format each server was found to speak, so the search happens once.
# Keyed by address and model: the same server may be asked for two models.
_dialect_cache: dict[tuple[str, str], str] = {}
_cache_lock = threading.Lock()

SYSTEM_PROMPT = """You clean up messy music video titles for a music library.

Reply with JSON only, in exactly this shape:
{"title": "...", "artist": "...", "album": "...", "confidence": 0.0}

Rules:
- "artist" is the ORIGINAL artist who made the song, not the channel that
  uploaded it, and not a leak or reupload account.
- "title" is the song name alone: no channel names, no "official video",
  no "lyrics", no quality tags (HD, CDQ, 320kbps), no years, no upload noise.
- Put featured performers in "artist", separated by commas.
- "album" is the release it belongs to, or an empty string if unknown.
- "confidence" is 0.0-1.0. Be honest: use below 0.6 when guessing.
- If you cannot identify the song, return the cleaned title with an empty
  artist and a confidence below 0.4. Never invent an artist.
"""


class AIError(Exception):
    """A problem the user can act on.

    ``status`` is the HTTP status when there was one. ``None`` means the
    server could not be reached at all, which is the one failure worth
    reporting straight away instead of trying another wire format.
    """

    def __init__(
        self, message: str, hint: str = "", *, status: Optional[int] = None
    ) -> None:
        if hint:
            super().__init__(message, hint)
        else:
            super().__init__(message)
        self.status = status


@dataclass
class AIResult:
    title: str = ""
    artist: str = ""
    album: str = ""
    confidence: float = 0.0

    @property
    def usable(self) -> bool:
        return bool(self.title) and self.confidence >= 0.35


@dataclass
class AISettings:
    provider: str = PROVIDER_AUTO
    base_url: str = OLLAMA_LOCAL_URL
    model: str = ""
    api_key: str = ""
    timeout: int = 120
    # Models that reason before they answer spend part of this budget on
    # that reasoning. 2048 was small enough that a batch of songs could be
    # cut off before any answer text was written.
    max_tokens: int = 8192

    @property
    def enabled(self) -> bool:
        return bool(self.model.strip() and self.base_url.strip())

    def describe(self) -> str:
        if not self.enabled:
            return "not set up"
        return f"{self.model} on {self.base_url}"


# Endpoints the providers expose, longest first so a pasted full URL is
# recognised before its shorter prefix.
_KNOWN_ENDPOINTS = (
    "/v1/chat/completions",
    "/v1/models",
    "/v1/messages",
    "/api/chat",
    "/api/tags",
)


def _strip_endpoint_suffix(base: str, path: str) -> str:
    """Drop the part of an address that the request path already adds.

    People paste what they were told to paste: ``https://host/v1``, or
    sometimes the whole ``https://host/v1/chat/completions``. Appending
    ``/v1/chat/completions`` to either would give ``/v1/v1/...`` and a
    404, so the duplicate is removed here instead of failing later.
    """
    lowered = base.lower()
    for known in (path,) + _KNOWN_ENDPOINTS:
        if lowered.endswith(known.lower()):
            return base[: -len(known)].rstrip("/")
    for prefix in ("/v1", "/api"):
        if lowered.endswith(prefix):
            return base[: -len(prefix)].rstrip("/")
    return base


def _endpoint(settings: AISettings, path: str) -> str:
    base = settings.base_url.strip().rstrip("/")
    if not base:
        raise AIError("No server address is set.")
    return f"{_strip_endpoint_suffix(base, path)}{path}"


def _headers(settings: AISettings, dialect: str = "") -> dict:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    key = settings.api_key.strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
        if dialect == PROVIDER_ANTHROPIC:
            # Anthropic's own API reads x-api-key; compatible gateways read
            # the bearer token. Sending both covers either kind of server.
            headers["x-api-key"] = key
            headers["anthropic-version"] = "2023-06-01"
        elif dialect == PROVIDER_OPENAI:
            # Azure-style servers read this header instead of a bearer token.
            # Anything else ignores it.
            headers["api-key"] = key
    return headers


def friendly_error(exc: AIError) -> tuple[str, str]:
    """Split an AIError into a headline and a hint for display."""
    message = exc.args[0] if exc.args else "The AI request failed."
    hint = exc.args[1] if len(exc.args) > 1 else ""
    return str(message), str(hint)


def _post(
    settings: AISettings,
    path: str,
    payload: dict,
    *,
    dialect: str = "",
) -> dict:
    url = _endpoint(settings, path)
    try:
        response = requests.post(
            url,
            headers=_headers(settings, dialect),
            data=json.dumps(payload),
            timeout=settings.timeout,
        )
    except requests.exceptions.Timeout as exc:
        raise AIError(
            "The AI server took too long to answer.",
            "Try again, or choose a smaller model.",
        ) from exc
    except requests.exceptions.ConnectionError as exc:
        raise AIError(
            "Could not reach the AI server.",
            "Check the server address, and that the server is running.",
        ) from exc

    if response.status_code == 401:
        raise AIError(
            "The AI server rejected the API key.",
            "Check the key in Settings, AI.",
            status=401,
        )
    if response.status_code == 403:
        detail = _error_text(response)
        raise AIError(
            "The AI server refused the request.",
            detail or "The key may be out of quota, or not allowed this model.",
            status=403,
        )
    if response.status_code == 404:
        raise AIError(
            "The AI server does not have that model or address.",
            "Check the model name and the server address in Settings, AI.",
            status=404,
        )
    if response.status_code >= 400:
        raise AIError(
            f"The AI server returned an error ({response.status_code}).",
            _error_text(response),
            status=response.status_code,
        )

    try:
        return response.json()
    except ValueError as exc:
        raise AIError(
            "The AI server sent something that was not JSON.",
            "Check that the address points at the API, not a web page.",
        ) from exc


def _error_text(response) -> str:
    try:
        data = response.json()
    except ValueError:
        return (response.text or "").strip()[:300]
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error)[:300]
        if error:
            return str(error)[:300]
        if data.get("message"):
            return str(data["message"])[:300]
    return json.dumps(data)[:300]


def _raise_if_truncated(reason: object) -> None:
    """Explain a reply that stopped because the model ran out of room.

    Reasoning models can spend the whole output budget thinking and then
    write no answer at all, which otherwise looks like an empty reply.
    """
    if str(reason or "").strip().lower() in ("max_tokens", "length"):
        raise AIError(
            "The model ran out of room before it answered.",
            "It may think before it replies. Try again, or send fewer "
            "songs at a time.",
        )


# ------------------------------------------------------------- wire formats


def _path_for(dialect: str, kind: str) -> str:
    """The request path for one format: a chat call or the model list."""
    if dialect == PROVIDER_OLLAMA:
        return "/api/chat" if kind == "chat" else "/api/tags"
    if dialect == PROVIDER_ANTHROPIC:
        return "/v1/messages" if kind == "chat" else "/v1/models"
    return "/v1/chat/completions" if kind == "chat" else "/v1/models"


def _payload(
    settings: AISettings,
    dialect: str,
    system: str,
    user: str,
    *,
    budget: Optional[int] = None,
) -> dict:
    """The body for one format. ``budget`` caps the reply, for probes."""
    limit = max(16, int(budget or settings.max_tokens or 8192))
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    if dialect == PROVIDER_OLLAMA:
        return {
            "model": settings.model,
            "stream": False,
            "messages": messages,
            "options": {"temperature": 0.2, "num_predict": limit},
        }
    if dialect == PROVIDER_ANTHROPIC:
        return {
            "model": settings.model,
            "max_tokens": limit,
            "temperature": 0.2,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
    return {
        "model": settings.model,
        "temperature": 0.2,
        "max_tokens": limit,
        "messages": messages,
    }


def _read_reply(dialect: str, data: dict) -> str:
    """Pull the text out of a reply, whichever format it came in."""
    if dialect == PROVIDER_OLLAMA:
        message = data.get("message") or {}
        text = str(message.get("content") or "")
        if not text.strip():
            _raise_if_truncated(data.get("done_reason"))
        return text

    if dialect == PROVIDER_ANTHROPIC:
        blocks = data.get("content") or []
        if isinstance(blocks, str):
            return blocks
        text = "".join(
            str(block.get("text") or "")
            for block in blocks
            if isinstance(block, dict)
        )
        if not text.strip():
            _raise_if_truncated(data.get("stop_reason"))
        return text

    choices = data.get("choices") or []
    if not choices:
        raise AIError("The AI server returned no answer.")
    choice = choices[0] if isinstance(choices[0], dict) else {}
    message = choice.get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        # Some compatible servers return content as a list of parts.
        text = "".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, dict)
        )
    else:
        text = str(content or "")
    if not text.strip():
        _raise_if_truncated(choice.get("finish_reason"))
    return text


def _cache_key(settings: AISettings) -> tuple[str, str]:
    return (
        settings.base_url.strip().rstrip("/").lower(),
        settings.model.strip().lower(),
    )


def detected_dialect(settings: AISettings) -> Optional[str]:
    """The format this server was found to speak, if it is already known."""
    with _cache_lock:
        return _dialect_cache.get(_cache_key(settings))


def _remember_dialect(settings: AISettings, dialect: str) -> None:
    with _cache_lock:
        _dialect_cache[_cache_key(settings)] = dialect


def forget_dialect(settings: AISettings) -> None:
    """Forget what was detected, so the next call looks again.

    Used when the address, model, or key changes.
    """
    with _cache_lock:
        _dialect_cache.pop(_cache_key(settings), None)


def _dialect_order(settings: AISettings) -> list[str]:
    """The formats to try, best guess first.

    The provider picked in Settings is a hint, not a requirement. The
    address itself is the stronger clue: a localhost port or an ollama
    hostname almost certainly speaks Ollama's own API.
    """
    order: list[str] = []
    if settings.provider in DIALECTS:
        order.append(settings.provider)

    base = settings.base_url.strip().lower()
    if "11434" in base or "ollama" in base:
        order += [PROVIDER_OLLAMA, PROVIDER_OPENAI, PROVIDER_ANTHROPIC]
    elif "anthropic" in base:
        order += [PROVIDER_ANTHROPIC, PROVIDER_OPENAI, PROVIDER_OLLAMA]
    else:
        order += [PROVIDER_OPENAI, PROVIDER_ANTHROPIC, PROVIDER_OLLAMA]

    unique: list[str] = []
    for dialect in order:
        if dialect not in unique:
            unique.append(dialect)
    return unique


def _probe(settings: AISettings, dialect: str) -> None:
    """Ask one tiny question in one format. Raises when it is not accepted.

    Only the HTTP result matters, not the text: a reasoning model may use
    the whole allowance thinking, and that still proves the path is right.
    """
    _post(
        settings,
        _path_for(dialect, "chat"),
        _payload(settings, dialect, "Reply with one word.", "Ready?", budget=24),
        dialect=dialect,
    )


def _best_failure(failures: list[tuple[str, AIError]]) -> tuple[str, AIError]:
    """The failure worth showing the user.

    A rejected key or an exhausted quota explains the problem; a 404 only
    means that path does not exist on this server, which is expected while
    the formats are being tried.
    """
    def rank(item: tuple[str, AIError]) -> int:
        status = item[1].status or 0
        if status in (401, 403):
            return 0
        if status and status != 404:
            return 1
        return 2

    return min(failures, key=rank)


def _detect(settings: AISettings) -> str:
    """Find the format this server speaks, and remember it."""
    failures: list[tuple[str, AIError]] = []
    for dialect in _dialect_order(settings):
        try:
            _probe(settings, dialect)
        except AIError as exc:
            if exc.status is None:
                # The server is not answering at all; another path will not
                # help, and this message is the useful one.
                raise
            failures.append((dialect, exc))
            logger.debug(
                "%s did not answer as %s: %s",
                settings.base_url, DIALECT_LABELS[dialect], exc.args[0],
            )
            continue
        _remember_dialect(settings, dialect)
        logger.info(
            "%s speaks the %s format",
            settings.base_url, DIALECT_LABELS[dialect],
        )
        return dialect

    _dialect, exc = _best_failure(failures)
    message = exc.args[0] if exc.args else "The AI server did not answer."
    detail = exc.args[1] if len(exc.args) > 1 else ""
    tried = ", ".join(DIALECT_LABELS[item] for item, _exc in failures)
    hint = f"{detail} Tried: {tried}.".strip()
    raise AIError(message, hint, status=exc.status)


# ------------------------------------------------------------------ requests


def chat(settings: AISettings, system: str, user: str) -> str:
    """Send one prompt and return the reply text."""
    if not settings.enabled:
        raise AIError(
            "AI is not set up yet.",
            "Open Settings, AI, and choose a provider and model.",
        )

    dialect = detected_dialect(settings) or _detect(settings)
    try:
        return _chat_as(settings, dialect, system, user)
    except AIError as exc:
        # The remembered format stopped working, for example because the
        # server was replaced. Look again once rather than failing.
        if exc.status not in (400, 404, 405):
            raise
        forget_dialect(settings)
        retry = _detect(settings)
        if retry == dialect:
            raise
        return _chat_as(settings, retry, system, user)


def _chat_as(settings: AISettings, dialect: str, system: str, user: str) -> str:
    """One chat call in one known format."""
    payload = _payload(settings, dialect, system, user)
    path = _path_for(dialect, "chat")
    try:
        data = _post(settings, path, payload, dialect=dialect)
    except AIError as exc:
        # Older Anthropic models cap their output at 4096 and reject a
        # larger request outright. Asking again with a smaller budget is
        # better than telling the user their model is broken.
        detail = str(exc.args[1]) if len(exc.args) > 1 else ""
        if (
            dialect != PROVIDER_ANTHROPIC
            or "max_tokens" not in detail
            or payload["max_tokens"] <= 4096
        ):
            raise
        payload["max_tokens"] = 4096
        data = _post(settings, path, payload, dialect=dialect)
    return _read_reply(dialect, data)


def _get(settings: AISettings, dialect: str, path: str) -> dict:
    url = _endpoint(settings, path)
    try:
        response = requests.get(
            url, headers=_headers(settings, dialect), timeout=20
        )
    except requests.exceptions.ConnectionError as exc:
        raise AIError(
            "Could not reach the AI server.",
            "If it should be on this computer, start Ollama and try again.",
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise AIError(
            "Could not reach the AI server.",
            "Check the server address.",
        ) from exc
    if response.status_code >= 400:
        raise AIError(
            "That server does not list its models.",
            _error_text(response) or "Type the model name in the box instead.",
            status=response.status_code,
        )
    try:
        return response.json()
    except ValueError as exc:
        raise AIError("The server did not send JSON.") from exc


def _model_names(dialect: str, data: dict) -> list[str]:
    if dialect == PROVIDER_OLLAMA:
        entries = data.get("models") or []
        names = [
            entry.get("name") or entry.get("model")
            for entry in entries if isinstance(entry, dict)
        ]
    else:
        entries = data.get("data") or []
        names = [
            entry.get("id") for entry in entries if isinstance(entry, dict)
        ]
    return sorted({str(name) for name in names if name})


def list_models(settings: AISettings) -> list[str]:
    """Ask the server which models it offers, whichever format it speaks."""
    order = _dialect_order(settings)
    known = detected_dialect(settings)
    if known is not None:
        order = [known] + [item for item in order if item != known]

    failures: list[tuple[str, AIError]] = []
    for dialect in order:
        try:
            data = _get(settings, dialect, _path_for(dialect, "models"))
        except AIError as exc:
            if exc.status is None:
                raise
            failures.append((dialect, exc))
            continue
        # A model list is served on the same path by more than one format,
        # so it says nothing about which one to use for chat. Leave that
        # decision to the first real request.
        return _model_names(dialect, data)

    _dialect, exc = _best_failure(failures)
    message = exc.args[0] if exc.args else "The server did not list its models."
    tried = ", ".join(DIALECT_LABELS[item] for item, _exc in failures)
    raise AIError(
        message,
        f"Type the model name in the box instead. Tried: {tried}.",
        status=exc.status,
    )


def test_connection(settings: AISettings) -> tuple[bool, str]:
    """Check the settings, returning a message fit for a dialog."""
    try:
        reply = chat(
            settings,
            "You are a connectivity test.",
            "Reply with the single word: ready",
        )
    except AIError as exc:
        # Never format the exception itself: a two-argument AIError would
        # print as a Python tuple.
        message = exc.args[0] if exc.args else "The connection test failed."
        hint = exc.args[1] if len(exc.args) > 1 else ""
        return False, f"{message}\n{hint}".strip() if hint else str(message)

    text = (reply or "").strip()
    if not text:
        return False, "The server answered, but with an empty reply."
    label = DIALECT_LABELS.get(detected_dialect(settings) or "", "")
    style = f" ({label})" if label else ""
    return True, f"Connected{style}. The server replied: “{text[:60]}”"


# -------------------------------------------------------------------- naming


def suggest_names(
    settings: AISettings,
    *,
    title: str,
    channel: str = "",
    description: str = "",
) -> AIResult:
    """Ask the model for a clean song title and artist."""
    prompt = [
        f"Title: {title}",
    ]
    if channel:
        prompt.append(f"Channel: {channel}")
    if description:
        prompt.append(f"Description: {description[:300]}")
    prompt.append("")
    prompt.append("Return the JSON object now.")

    reply = chat(settings, SYSTEM_PROMPT, "\n".join(prompt))
    return _parse_result(reply)


def _parse_result(reply: str) -> AIResult:
    text = (reply or "").strip()
    if not text:
        raise AIError("The AI returned an empty answer.")

    # Models sometimes wrap JSON in prose or a code fence.
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise AIError("The AI did not return usable JSON.", text[:200])

    try:
        data = json.loads(match.group(0))
    except ValueError as exc:
        raise AIError("The AI returned malformed JSON.", text[:200]) from exc

    if not isinstance(data, dict):
        raise AIError("The AI returned an unexpected shape.")

    def text_of(key: str) -> str:
        value = data.get(key)
        return str(value).strip() if value is not None else ""

    try:
        confidence = float(data.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0

    return AIResult(
        title=text_of("title"),
        artist=text_of("artist"),
        album=text_of("album"),
        confidence=max(0.0, min(1.0, confidence)),
    )
