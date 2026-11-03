"""Counted nouns for messages a person reads.

"Added 1 link(s), 1 song(s)" is the kind of line that makes an app feel
unfinished. ``counted`` writes the singular or plural form properly.
"""

from __future__ import annotations


def counted(number: int, singular: str, plural: str = "") -> str:
    """``1 song``, ``3 songs``, or an irregular pair when given one."""
    if plural == "":
        plural = singular + "s"
    return f"{number} {singular if number == 1 else plural}"


def are(number: int) -> str:
    return "is" if number == 1 else "are"