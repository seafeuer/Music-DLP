"""AI naming: local Ollama, Ollama cloud, or any compatible HTTP API.

The parser in ``smart_titles`` is deterministic and free. This module is the
optional second opinion for titles the parser cannot resolve, and it is only
ever used when the user asks for it.

Two wire formats are supported:

* ``ollama``  - POST {base}/api/chat            (local Ollama or ollama.com)
* ``openai``  - POST {base}/v1/chat/completions (any compatible endpoint)

Requests always carry a normal browser User-Agent. Some hosted endpoints sit
behind a proxy that rejects Python's default agent with a 403.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

import requests

logger = logging.getLogger("musicdlp.ai")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

PROVIDER_OLLAMA = "ollama"
PROVIDER_OPENAI = "openai"

PROVIDERS = (PROVIDER_OLLAMA, PROVIDER_OPENAI)

# Presets shown in Settings.
OLLAMA_LOCAL_URL = "http://localhost:11434"
OLLAMA_CLOUD_URL = "https://ollama.com"

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
    """A problem the user can act on."""


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
    provider: str = PROVIDER_OLLAMA
    base_url: str = OLLAMA_LOCAL_URL
    model: str = ""
    api_key: str = ""
    timeout: int = 120

    @property
    def enabled(self) -> bool:
        return bool(self.model.strip() and self.base_url.strip())

    def describe(self) -> str:
        if not self.enabled:
            return "not set up"
        return f"{self.model} on {self.base_url}"


def _endpoint(settings: AISettings, path: str) -> str:
    base = settings.base_url.strip().rstrip("/")
    if not base:
        raise AIError("No server address is set.")
    return f"{base}{path}"


def _headers(settings: AISettings) -> dict:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    key = settings.api_key.strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def friendly_error(exc: AIError) -> tuple[str, str]:
    """Split an AIError into a headline and a hint for display."""
    message = exc.args[0] if exc.args else "The AI request failed."
    hint = exc.args[1] if len(exc.args) > 1 else ""
    return str(message), str(hint)


def _post(settings: AISettings, path: str, payload: dict) -> dict:
    url = _endpoint(settings, path)
    try:
        response = requests.post(
            url,
            headers=_headers(settings),
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
        )
    if response.status_code == 403:
        detail = _error_text(response)
        raise AIError(
            "The AI server refused the request.",
            detail or "The key may be out of quota, or not allowed this model.",
        )
    if response.status_code == 404:
        raise AIError(
            "The AI server does not have that model or address.",
            "Check the model name and the server address in Settings, AI.",
        )
    if response.status_code >= 400:
        raise AIError(
            f"The AI server returned an error ({response.status_code}).",
            _error_text(response),
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


# ------------------------------------------------------------------ requests


def chat(settings: AISettings, system: str, user: str) -> str:
    """Send one prompt and return the reply text."""
    if not settings.enabled:
        raise AIError(
            "AI is not set up yet.",
            "Open Settings, AI, and choose a provider and model.",
        )

    if settings.provider == PROVIDER_OLLAMA:
        data = _post(
            settings,
            "/api/chat",
            {
                "model": settings.model,
                "stream": False,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "options": {"temperature": 0.2},
            },
        )
        message = data.get("message") or {}
        return str(message.get("content") or "")

    data = _post(
        settings,
        "/v1/chat/completions",
        {
            "model": settings.model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        },
    )
    choices = data.get("choices") or []
    if not choices:
        raise AIError("The AI server returned no answer.")
    message = choices[0].get("message") or {}
    return str(message.get("content") or "")


def list_models(settings: AISettings) -> list[str]:
    """Ask the server which models it offers."""
    path = "/api/tags" if settings.provider == PROVIDER_OLLAMA else "/v1/models"
    url = _endpoint(settings, path)
    try:
        response = requests.get(url, headers=_headers(settings), timeout=20)
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
            "The server would not list its models.",
            _error_text(response),
        )
    try:
        data = response.json()
    except ValueError:
        raise AIError("The server did not send JSON.")

    if settings.provider == PROVIDER_OLLAMA:
        names = [entry.get("name") or entry.get("model") for entry in
                 (data.get("models") or [])]
    else:
        names = [entry.get("id") for entry in (data.get("data") or [])]
    return sorted(name for name in names if name)


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
    return True, f"Connected. The server replied: “{text[:60]}”"


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
