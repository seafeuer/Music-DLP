"""The batch that was running when the app last stopped.

A download can be long. Closing the window used to throw the rest of it
away, and a crash or a restart threw it away silently. This small file
records what was running so the next start can offer to resume it.

It is written only when a job actually starts and removed when the
pipeline reports that it finished (including a cancel or a failure), so
a file on disk means the previous run never got to finish.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("musicdlp.pending")


@dataclass
class PendingJob:
    track_ids: list[int] = field(default_factory=list)
    stages: list[str] = field(default_factory=list)
    force: bool = False
    only_synced: bool = False
    retag: bool = False
    started_at: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "PendingJob":
        if not isinstance(data, dict):
            raise ValueError("pending job is not an object")
        raw_ids = data.get("track_ids") or []
        track_ids = []
        for value in raw_ids:
            track_id = int(value)
            if track_id <= 0:
                raise ValueError("track ids must be positive")
            if track_id not in track_ids:
                track_ids.append(track_id)
        stages = [str(stage) for stage in (data.get("stages") or [])]
        return cls(
            track_ids=track_ids,
            stages=stages,
            force=bool(data.get("force")),
            only_synced=bool(data.get("only_synced")),
            retag=bool(data.get("retag")),
            started_at=str(data.get("started_at") or ""),
        )

    def to_dict(self) -> dict:
        return {
            "track_ids": list(self.track_ids),
            "stages": list(self.stages),
            "force": self.force,
            "only_synced": self.only_synced,
            "retag": self.retag,
            "started_at": self.started_at,
        }


def new_job(track_ids, stages, *, force=False, only_synced=False,
            retag=False) -> PendingJob:
    return PendingJob(
        track_ids=list(track_ids),
        stages=list(stages),
        force=bool(force),
        only_synced=bool(only_synced),
        retag=bool(retag),
        started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def save(path: Path, job: PendingJob) -> None:
    """Write the marker. A failure here must never stop a job."""
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(job.to_dict(), indent=2), encoding="utf-8"
        )
        os.replace(temporary, path)
    except OSError as exc:
        logger.warning("Could not record the running job: %s", exc)


def load(path: Path) -> Optional[PendingJob]:
    """Read the marker, or None when there is no usable one."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return PendingJob.from_dict(data)
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("Ignoring an unreadable pending job: %s", exc)
        return None


def clear(path: Path) -> None:
    path = Path(path)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("Could not clear the pending job: %s", exc)