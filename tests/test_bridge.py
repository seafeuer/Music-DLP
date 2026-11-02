"""The loopback API the Music DLP panel inside Spotify calls."""

import json
import time
import urllib.error
import urllib.request

import pytest
from PySide6.QtCore import Qt

from musicdlp.core.config import Settings
from musicdlp.core.db import Database
from musicdlp.services.bridge import Bridge, sync_panel_config

TOKEN = "test-token"


class FakePipeline:
    is_running = False


class FakeLibrary:
    def __init__(self, db):
        self.db = db
        self.calls = []

    def add_links(self, urls, **_kwargs):
        self.calls.append(("add_links", list(urls)))

        class Result:
            sources = [1]
            tracks_total = 2
            tracks_new = 2
            errors = []

        return Result()

    def set_metadata(self, track_id, *, title, artist="", album="", track_number=None):
        self.calls.append(("set_metadata", track_id))
        self.db.update_track(
            track_id,
            meta_title=title,
            meta_artist=artist,
            meta_album=album,
            meta_track=track_number,
        )

    def refresh_cover(self, track_id, **_kwargs):
        self.calls.append(("refresh_cover", track_id))
        return "updated"

    def restore_video_cover(self, track_id):
        self.calls.append(("restore_video_cover", track_id))


@pytest.fixture
def env(tmp_path):
    db = Database(tmp_path / "library.db")
    folder_id = db.add_folder("Rap")
    track_id = db.upsert_track(
        platform="youtube",
        media_id="abc",
        url="https://youtu.be/abc",
        title="How Come (Prod. Pierre)",
        uploader="Young Thug",
        duration=140,
    )
    db.move_tracks_to_folder([track_id], folder_id)

    settings = Settings(bridge_token=TOKEN, bridge_port=0)
    library = FakeLibrary(db)
    bridge = Bridge(settings, db, library, FakePipeline(), port=0)
    assert bridge.start()
    yield bridge, db, library, folder_id, track_id
    bridge.stop()
    db.close()


def _url(bridge, path):
    return f"http://127.0.0.1:{bridge.port}{path}"


def _call(bridge, path, *, method="GET", body=None, token=TOKEN, origin=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(_url(bridge, path), data=data, method=method)
    if token is not None:
        request.add_header("Authorization", f"Bearer {token}")
    if origin:
        request.add_header("Origin", origin)
    if data:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, json.loads(raw) if raw else {}


def test_health_reports_the_app_and_folder(env):
    bridge, *_ = env
    status, payload = _call(bridge, "/api/health")

    assert status == 200
    assert payload["app"] == "Music DLP"
    assert payload["dlp_folder"] == "Music DLP"
    assert payload["pipeline_running"] is False


def test_a_request_without_a_token_is_refused(env):
    bridge, *_ = env
    status, payload = _call(bridge, "/api/health", token=None)

    assert status == 403
    assert "error" in payload


def test_a_request_with_the_wrong_token_is_refused(env):
    bridge, *_ = env
    status, _ = _call(bridge, "/api/health", token="not-the-token")

    assert status == 403


def test_a_request_from_another_website_is_refused(env):
    bridge, *_ = env
    status, _ = _call(bridge, "/api/health", origin="https://evil.example")

    assert status == 403


def test_the_spotify_origin_is_allowed_and_gets_cors_headers(env):
    bridge, *_ = env
    request = urllib.request.Request(
        _url(bridge, "/api/health"),
        method="GET",
    )
    request.add_header("Authorization", f"Bearer {TOKEN}")
    request.add_header("Origin", "https://xpui.app.spotify.com")
    with urllib.request.urlopen(request, timeout=5) as response:
        assert response.status == 200
        assert (
            response.headers["Access-Control-Allow-Origin"]
            == "https://xpui.app.spotify.com"
        )


def test_a_preflight_is_answered(env):
    bridge, *_ = env
    request = urllib.request.Request(_url(bridge, "/api/tracks"), method="OPTIONS")
    request.add_header("Origin", "https://xpui.app.spotify.com")
    with urllib.request.urlopen(request, timeout=5) as response:
        assert response.status == 204
        assert "Authorization" in response.headers["Access-Control-Allow-Headers"]


def test_folders_are_listed_with_their_song_counts(env):
    bridge, *_ = env
    status, payload = _call(bridge, "/api/folders")

    assert status == 200
    assert [folder["title"] for folder in payload] == ["Rap"]
    assert payload[0]["tracks"] == 1


def test_songs_of_a_folder_are_listed(env):
    bridge, db, _library, folder_id, _track_id = env
    status, payload = _call(bridge, f"/api/tracks?folder={folder_id}")

    assert status == 200
    assert len(payload) == 1
    assert payload[0]["title"] == "How Come (Prod. Pierre)"
    assert payload[0]["artist"] == "Young Thug"


def test_an_unknown_route_is_a_404(env):
    bridge, *_ = env
    status, _ = _call(bridge, "/api/nothing")

    assert status == 404


def test_a_link_is_queued_and_its_job_reports_the_result(env):
    bridge, _db, library, _folder_id, _track_id = env
    status, payload = _call(
        bridge, "/api/links", method="POST", body={"urls": ["https://youtu.be/x"]}
    )

    assert status == 200
    job_id = payload["id"]
    assert library.calls and library.calls[0][0] == "add_links"

    for _ in range(50):
        job_status, job = _call(bridge, f"/api/jobs/{job_id}")
        if job["status"] != "running":
            break
        time.sleep(0.05)

    assert job_status == 200
    assert job["status"] == "done"
    assert job["result"]["tracks_new"] == 2


def test_a_sync_plan_matches_the_folder_to_local_files(env):
    bridge, _db, _library, folder_id, _track_id = env
    status, payload = _call(
        bridge,
        "/api/sync/plan",
        method="POST",
        body={
            "folder_id": folder_id,
            "local_tracks": [
                {
                    "uri": "spotify:local:Young+Thug:Singles:How+Come:140",
                    "name": "How Come (Prod. Pierre)",
                    "artists": [{"name": "Young Thug"}],
                    "album": {"name": "Singles"},
                    "duration": {"milliseconds": 140000},
                }
            ],
            "playlist_uris": [],
        },
    )

    assert status == 200
    assert payload["to_add"] == ["spotify:local:Young+Thug:Singles:How+Come:140"]
    assert payload["to_remove"] == []
    assert payload["summary"]["matched"] == 1


def test_a_sync_plan_reports_songs_with_no_local_file(env):
    bridge, _db, _library, folder_id, _track_id = env
    status, payload = _call(
        bridge,
        "/api/sync/plan",
        method="POST",
        body={"folder_id": folder_id, "local_tracks": [], "playlist_uris": []},
    )

    assert status == 200
    assert payload["to_add"] == []
    assert payload["unmatched"][0]["title"] == "How Come (Prod. Pierre)"


def test_metadata_is_saved(env):
    bridge, db, library, _folder_id, track_id = env
    status, payload = _call(
        bridge,
        "/api/metadata",
        method="POST",
        body={"track_id": track_id, "title": "How Come", "artist": "Young Thug"},
    )

    assert status == 200
    assert payload["track"]["title"] == "How Come"
    assert library.calls[-1][0] == "set_metadata"


def test_metadata_for_an_unknown_song_is_a_404(env):
    bridge, *_ = env
    status, _ = _call(
        bridge, "/api/metadata", method="POST", body={"track_id": 999, "title": "x"}
    )

    assert status == 404


def test_a_cover_lookup_calls_the_library(env):
    bridge, _db, library, _folder_id, track_id = env
    status, payload = _call(
        bridge,
        "/api/cover",
        method="POST",
        body={"track_id": track_id, "action": "lookup"},
    )

    assert status == 200
    assert payload["result"] == "updated"
    assert ("refresh_cover", track_id) in library.calls


def test_starting_a_job_asks_for_the_gui_thread(env):
    bridge, _db, _library, _folder_id, track_id = env
    seen = []
    # Direct connection so the emission is observable without an event
    # loop; in the app the receiver lives on the GUI thread and Qt queues it.
    bridge.start_pipeline.connect(
        lambda rows, stages: seen.append((rows, stages)),
        Qt.ConnectionType.DirectConnection,
    )

    status, payload = _call(
        bridge,
        "/api/jobs",
        method="POST",
        body={"track_ids": [track_id], "stages": ["download"]},
    )

    assert status == 200
    assert payload["started"] is True
    assert seen and seen[0][1] == ["download"]


def test_starting_a_job_for_an_unknown_song_is_a_400(env):
    bridge, *_ = env
    status, _ = _call(
        bridge, "/api/jobs", method="POST", body={"track_ids": [999], "stages": ["download"]}
    )

    assert status == 400


def test_fixing_covers_for_several_songs_reports_counts(env):
    bridge, _db, library, _folder_id, track_id = env
    status, payload = _call(
        bridge,
        "/api/cover/bulk",
        method="POST",
        body={"track_ids": [track_id]},
    )

    assert status == 200
    job = payload
    for _ in range(50):
        _, job = _call(bridge, f"/api/jobs/{job['id']}")
        if job["status"] != "running":
            break
        time.sleep(0.05)

    assert job["status"] == "done"
    assert job["result"]["counts"] == {"updated": 1}
    assert ("refresh_cover", track_id) in library.calls


def test_a_body_that_is_not_json_is_rejected(env):
    bridge, *_ = env
    request = urllib.request.Request(
        _url(bridge, "/api/links"), data=b"not json", method="POST"
    )
    request.add_header("Authorization", f"Bearer {TOKEN}")
    try:
        urllib.request.urlopen(request, timeout=5)
        raise AssertionError("expected a 400")
    except urllib.error.HTTPError as exc:
        assert exc.code == 400


# ------------------------------------------------------- panel config sync


def _write_panel(root, parts, config_json):
    path = root.joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "/* panel */\nvar MUSICDLP_CONFIG = " + config_json + ";\n(function(){})();\n",
        encoding="utf-8",
    )
    return path


def test_panel_config_is_rewritten_in_both_copies(tmp_path):
    source = _write_panel(
        tmp_path,
        ("spicetify", "CustomApps", "musicdlp", "index.js"),
        '{"port":1,"token":"stale","dlpFolder":"x"}',
    )
    live = _write_panel(
        tmp_path,
        ("Spotify", "Apps", "xpui", "spicetify-routes-musicdlp.js"),
        '{"port":1,"token":"stale","dlpFolder":"x"}',
    )

    updated = sync_panel_config(8317, "fresh-token", "Music DLP", appdata=tmp_path)

    assert set(updated) == {source, live}
    for path in (source, live):
        text = path.read_text(encoding="utf-8")
        assert '"token":"fresh-token"' in text
        assert '"port":8317' in text
        assert "stale" not in text
        # The rest of the file must survive untouched.
        assert "(function(){})();" in text


def test_panel_config_sync_is_a_no_op_when_the_panel_is_not_installed(tmp_path):
    updated = sync_panel_config(8317, "fresh", "Music DLP", appdata=tmp_path)
    assert updated == []


def test_panel_config_sync_leaves_an_already_correct_file_alone(tmp_path):
    path = _write_panel(
        tmp_path,
        ("spicetify", "CustomApps", "musicdlp", "index.js"),
        '{"port":8317,"token":"fresh","dlpFolder":"Music DLP"}',
    )
    before = path.stat().st_mtime_ns

    updated = sync_panel_config(8317, "fresh", "Music DLP", appdata=tmp_path)

    assert updated == []
    assert path.stat().st_mtime_ns == before
