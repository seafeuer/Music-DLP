"""Which copy of a duplicate song to keep.

The desktop's Duplicate songs dialog and the panel inside Spotify show the
same groups (see ``Database.find_duplicate_groups``). Both have to agree on
which copy is kept by default, or the same group would be offered twice
with different suggestions.
"""

from __future__ import annotations

# Prefer keeping the copy that is furthest along: it has the most work in
# it. A missing file is worse than a fresh row, hence the low rank.
KEEP_RANK = {
    "synced": 5,
    "converted": 4,
    "downloaded": 3,
    "needs_update": 3,
    "missing": 1,
}


def copy_rank(row) -> tuple[int, int]:
    """Higher is the better copy to keep.

    Ties go to the older row (the lower id), which is the one the user has
    had longer.
    """
    return (KEEP_RANK.get(row.track.status, 0), -row.track.id)


def best_copy(group):
    """The copy to keep from one duplicate group."""
    return max(group, key=copy_rank)


def removal_ids(group) -> list[int]:
    """The copies the dialog ticks for removal by default."""
    best = best_copy(group)
    return [
        row.track.id for row in group
        if row.track.id != best.track.id
    ]
