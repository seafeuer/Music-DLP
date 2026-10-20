"""Actionable errors shown to the user."""

from __future__ import annotations

from typing import Optional


class Cancelled(Exception):
    """Raised when a job is cancelled by the user."""


class AppError(Exception):
    """An error with a message and an optional fix hint for the UI."""

    def __init__(
        self,
        message: str,
        hint: str = "",
        kind: str = "error",
        cause: Optional[BaseException] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.kind = kind
        self.cause = cause

    def user_text(self) -> str:
        return f"{self.message}\n\n{self.hint}" if self.hint else self.message


def classify_ytdlp_error(exc: BaseException) -> AppError:
    """Turn a yt-dlp exception into a message a non-expert can act on."""
    text = str(exc)
    lowered = text.lower()

    if "sign in to confirm" in lowered or "not a bot" in lowered:
        return AppError(
            "YouTube is asking for a sign-in check.",
            "Open Settings → Download and choose your browser for cookies, "
            "or select a cookies.txt file, then retry.",
            kind="bot",
            cause=exc,
        )

    if "403" in lowered or "forbidden" in lowered:
        return AppError(
            "The site refused the download (403).",
            "This usually means cookies are missing or expired. Choose your "
            "browser under Settings → Download, or refresh cookies.txt.",
            kind="bot",
            cause=exc,
        )

    if "private" in lowered or "members-only" in lowered:
        return AppError(
            "This item is private or members-only.",
            "Use browser cookies from an account that can view it.",
            kind="private",
            cause=exc,
        )

    if "unavailable" in lowered or "removed" in lowered or "deleted" in lowered:
        return AppError(
            "This item is unavailable.",
            "It may have been removed, made private, or be region-locked.",
            kind="unavailable",
            cause=exc,
        )

    if "unsupported url" in lowered or "no suitable extractor" in lowered:
        return AppError(
            "This link is not supported.",
            "Check that the URL points to a video, post, or playlist.",
            kind="unsupported",
            cause=exc,
        )

    if "requested format is not available" in lowered:
        return AppError(
            "No downloadable audio or video format was found.",
            "The post may contain only images, or the site changed. Try again "
            "later or update yt-dlp.",
            kind="format",
            cause=exc,
        )

    if "timed out" in lowered or "timeout" in lowered:
        return AppError(
            "The site took too long to respond.",
            "Check your connection and try again.",
            kind="network",
            cause=exc,
        )

    if "connection" in lowered or "network" in lowered or "resolve" in lowered:
        return AppError(
            "A network error occurred.",
            "Check your internet connection and try again.",
            kind="network",
            cause=exc,
        )

    if "cookies" in lowered:
        return AppError(
            "The cookies file could not be used.",
            "Re-export cookies.txt or choose browser cookies in Settings.",
            kind="cookies",
            cause=exc,
        )

    return AppError(
        "The download failed.",
        text.strip()[:500] if text else "",
        kind="error",
        cause=exc,
    )


def classify_ffmpeg_error(stderr: str) -> AppError:
    lowered = (stderr or "").lower()
    if "no such file" in lowered:
        return AppError(
            "The source audio file is missing.",
            "Download the track again.",
            kind="file",
        )
    if "invalid data" in lowered or "could not find codec" in lowered:
        return AppError(
            "The downloaded file is not valid audio.",
            "Delete it and download the track again.",
            kind="file",
        )
    if "permission denied" in lowered:
        return AppError(
            "Windows denied access to the output folder.",
            "Choose a different output folder in Settings.",
            kind="file",
        )
    if "no space left" in lowered or "disk full" in lowered:
        return AppError(
            "The disk is full.",
            "Free some space, then retry.",
            kind="disk",
        )
    return AppError(
        "Audio conversion failed.",
        (stderr or "").strip()[-500:],
        kind="ffmpeg",
    )
