"""Theme parity and contrast.

Both themes are first-class, so the palette has to carry readable colours
in each. These tests pin the numbers a colour edit could quietly break,
and check that no widget paints a literal colour that only suits one
theme.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from musicdlp.ui.theme import DARK, LIGHT, STATUS_COLORS, tokens

AA_BODY = 4.5


def _luminance(color: str) -> float:
    value = color.lstrip("#")
    channels = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [
        c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        for c in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(foreground: str, background: str) -> float:
    lighter, darker = sorted(
        (_luminance(foreground), _luminance(background)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize(
    "role",
    ["text", "muted", "danger", "warning", "success"],
)
def test_message_colours_are_readable_on_each_surface(theme, role):
    colors = tokens(theme)
    for surface in ("window", "panel"):
        assert contrast(colors[role], colors[surface]) >= AA_BODY, (
            f"{role} on {surface} in the {theme} theme"
        )


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_the_accent_colour_carries_white_text(theme):
    """A primary button's label sits on the accent, not on a surface."""
    colors = tokens(theme)
    assert contrast(colors["on_accent"], colors["accent"]) >= AA_BODY


@pytest.mark.parametrize(
    "status", sorted(STATUS_COLORS)
)
def test_status_pills_are_readable(status):
    background, foreground = STATUS_COLORS[status]
    assert contrast(foreground, background) >= AA_BODY


def test_primary_button_label_is_readable():
    for theme in ("dark", "light"):
        colors = tokens(theme)
        assert contrast(colors["on_accent"], colors["accent"]) >= AA_BODY
        assert contrast(colors["on_accent"], colors["accent_hover"]) >= AA_BODY


def test_the_two_themes_define_the_same_roles():
    assert set(DARK) == set(LIGHT)


def _ui_sources() -> list[Path]:
    root = Path(__file__).resolve().parents[1] / "musicdlp" / "ui"
    return [
        path for path in root.rglob("*.py") if path.name != "theme.py"
    ]


def test_widgets_do_not_paint_one_theme_colour_literally():
    """A hex colour in a widget cannot follow a theme switch.

    Status pills are the one exception: they are light chips with dark
    text in both themes by design, and their colours live in theme.py.
    """
    offenders = []
    for path in _ui_sources():
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"#[0-9a-fA-F]{6}\b", text):
            line = text[: match.start()].count("\n") + 1
            offenders.append(f"{path.name}:{line} {match.group(0)}")
    assert offenders == [], (
        "Use theme.tone()/set_tone() instead of a literal colour: "
        + ", ".join(offenders)
    )


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_links_are_readable_on_the_window(theme):
    """Rich-text links sit on the window, not on the accent button."""
    colors = tokens(theme)
    assert contrast(colors["link"], colors["window"]) >= AA_BODY
    assert contrast(colors["link"], colors["panel"]) >= AA_BODY
