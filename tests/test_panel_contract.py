"""The bridge contract the panel inside Spotify relies on.

The panel is one JavaScript file that reads fields by name. If the bridge
stops sending one of them, the panel does not fail loudly: a title goes
blank, a sync plan looks empty, a button silently disables itself. These
tests pin the routes the panel calls and the fields it reads.

The route list is checked against the panel source, so adding a new call
without describing it here fails. The response shapes are checked against
a real bridge, so removing a field fails too.
"""

from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

import pytest

from musicdlp.core.config import Settings
from musicdlp.core.db import Database
from musicdlp.services.bridge import Bridge

PANEL = Path(__file__).resolve().parents[1] / "spicetify" / "musicdlp-panel" / "index.js"


class FakePipeline:
    is_running = False


class FakeLibrary:
    """Only the calls the bridge can make during these reads."""

    def __init__(self, db):
        self.db = db

    def relink_from_folder(self, folder):
        return {"relinked": 0, "missing": 0}

    def delete_track(self, track_id, **_kwargs):
        self.db.delete_track(track_id)


# Every route the panel calls. A route is a (method, path) pair, because
# the same path can mean different things. Dynamic ids are written {id}.
ROUTES = {
    ("GET", "/api/health"),
    ("GET", "/api/status"),
    ("GET", "/api/folders"),
    ("GET", "/api/tracks"),
    ("GET", "/api/jobs/{id}"),
    ("GET", "/api/duplicates"),
    ("GET", "/api/integrity"),
    ("GET", "/api/export/songs"),
    ("POST", "/api/links"),
    ("POST", "/api/jobs"),
    ("POST", "/api/cancel"),
    ("POST", "/api/folders"),
    ("POST", "/api/folders/rename"),
    ("POST", "/api/folders/delete"),
    ("POST", "/api/sync/plan"),
    ("GET", "/api/sync/requests"),
    ("POST", "/api/sync/ack"),
    ("POST", "/api/metadata"),
    ("POST", "/api/cover"),
    ("POST", "/api/cover/bulk"),
    ("POST", "/api/lookup"),
    ("POST", "/api/translate"),
    ("POST", "/api/ai/names"),
    ("POST", "/api/gdrive"),
    ("POST", "/api/rewrite"),
    ("POST", "/api/duplicates/remove"),
    ("POST", "/api/reconnect"),
    ("POST", "/api/import/names"),
}

class Maybe:
    """A field that may legitimately be null (JSON null)."""

    def __init__(self, kind):
        self.kind = kind


# The fields the panel reads from each response. Values are the expected
# Python types; "<items>" describes every item of a list.
SHAPES = {
    "/api/health": {
        "ok": bool,
        "app": str,
        "version": str,
        "pipeline_running": bool,
        "dlp_folder": str,
        "output_dir": str,
        "library_version": int,
    },
    "/api/status": {
        "pipeline_running": bool,
        "progress": dict,
        "library_version": int,
    },
    "/api/sync/requests": {
        "requests": {
            "<items>": {
                "id": str,
                "folder_id": int,
            },
        },
    },
    "/api/folders": {
        "<items>": {
            "id": int,
            "title": str,
            "tracks": int,
        },
    },
    "/api/tracks": {
        "<items>": {
            "id": int,
            "title": str,
            "artist": str,
            "album": str,
            "track_number": Maybe(int),
            "status": str,
            "display_status": str,
            "has_file": bool,
            "has_raw": bool,
            "file_missing": bool,
            "needs_update": bool,
        },
    },
    "/api/duplicates": {
        "count": int,
        "groups": {
            "<items>": {
                "keep": int,
                "tracks": {
                    "<items>": {"id": int, "title": str, "status": str},
                },
            },
        },
    },
    "/api/integrity": {
        "checked": int,
        "shared": {
            "<items>": {
                "path": str,
                "tracks": {"<items>": {"id": int, "title": str}},
            },
        },
        "changed": {
            "<items>": {"track_id": int, "title": str, "path": str},
        },
    },
    "/api/export/songs": {
        "count": int,
        "prompt": str,
    },
}


def _normalise(path: str) -> str:
    path = path.split("?", 1)[0]
    if path.startswith("/api/jobs/"):
        return "/api/jobs/{id}"
    return path


def _calls_in_panel() -> set[tuple[str, str]]:
    source = PANEL.read_text(encoding="utf-8")
    found = re.findall(r'api\(\s*"(GET|POST)"\s*,\s*"([^"]+)"', source)
    return {(method, _normalise(path)) for method, path in found}


def _paths_in_panel() -> set[str]:
    """Every /api path named in the panel, including one built first.

    ``loadTracks`` puts its path in a variable so a folder can be added to
    it; the method still comes from the direct-call scan below.
    """
    source = PANEL.read_text(encoding="utf-8")
    return {
        _normalise(path)
        for path in re.findall(r'"(/api/[^"]*)"', source)
    }


def test_the_panel_calls_no_route_the_contract_does_not_know():
    declared = {path for _method, path in ROUTES}

    assert _paths_in_panel() <= declared, (
        "The panel calls a route missing from the contract: "
        f"{sorted(_paths_in_panel() - declared)}"
    )
    assert _calls_in_panel() <= ROUTES, (
        "The panel calls a method/path pair missing from the contract: "
        f"{sorted(_calls_in_panel() - ROUTES)}"
    )
    # Every route the contract names is really used, so the list cannot
    # drift into a wish list nobody calls.
    assert declared <= _paths_in_panel(), (
        "The contract names a route the panel never calls: "
        f"{sorted(declared - _paths_in_panel())}"
    )


def _check(value, shape, where: str) -> None:
    if isinstance(shape, Maybe):
        if value is None:
            return
        shape = shape.kind
    if isinstance(shape, dict) and set(shape) == {"<items>"}:
        assert isinstance(value, list), f"{where} should be a list"
        for index, item in enumerate(value):
            _check(item, shape["<items>"], f"{where}[{index}]")
        return
    if isinstance(shape, dict):
        assert isinstance(value, dict), f"{where} is not an object"
        for field, expected in shape.items():
            if field == "<items>":
                for index, item in enumerate(value):
                    _check(item, expected, f"{where}[{index}]")
                continue
            assert field in value, f"{where} has no {field!r}"
            _check(value[field], expected, f"{where}.{field}")
        return
    if shape is int:
        # JSON numbers are int; a bool is also an int in Python but never
        # what a count field should be.
        assert isinstance(value, int) and not isinstance(value, bool), (
            f"{where} should be an integer, got {type(value).__name__}"
        )
        return
    if shape is str:
        assert isinstance(value, str), (
            f"{where} should be a string, got {type(value).__name__}"
        )
        return
    assert isinstance(value, shape), (
        f"{where} should be {shape.__name__}, got {type(value).__name__}"
    )


@pytest.fixture()
def env(tmp_path):
    db = Database(tmp_path / "library.db")
    folder_id = db.add_folder("Rap")
    first = db.upsert_track(
        platform="youtube", media_id="a", url="https://youtu.be/a",
        title="One", uploader="Artist", duration=100,
    )
    second = db.upsert_track(
        platform="youtube", media_id="b", url="https://youtu.be/b",
        title="One", uploader="Artist", duration=100,
    )
    db.move_tracks_to_folder([first, second], folder_id)

    settings = Settings(bridge_token="contract-token", bridge_port=0)
    bridge = Bridge(settings, db, FakeLibrary(db), FakePipeline(), port=0)
    assert bridge.start()
    yield bridge, db
    bridge.stop()
    db.close()


def _get(bridge, path):
    request = urllib.request.Request(
        f"http://127.0.0.1:{bridge.port}{path}"
    )
    request.add_header("Authorization", "Bearer contract-token")
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read() or b"{}")


@pytest.mark.parametrize("path", sorted(SHAPES))
def test_every_read_the_panel_makes_has_the_fields_it_uses(env, path):
    bridge, _db = env

    payload = _get(bridge, path)

    _check(payload, SHAPES[path], path)


def test_a_track_for_a_folder_is_the_same_shape_as_the_full_list(env):
    bridge, _db = env
    everything = _get(bridge, "/api/tracks")
    in_folder = _get(bridge, "/api/tracks?folder=1")

    assert len(everything) == 2
    assert len(in_folder) == 2
    assert set(in_folder[0]) >= set(SHAPES["/api/tracks"]["<items>"])