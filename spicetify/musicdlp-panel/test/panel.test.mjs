/* Headless tests for the Spotify panel.
 *
 * The panel is a single browser file, so it is loaded into a VM context
 * with Spotify's own globals stubbed. The functions under test are the
 * ones that talk to the bridge or to Spotify's playlist API; the React
 * components are exercised through the pure helpers they delegate to.
 *
 * Run with:  node --test spicetify/musicdlp-panel/test/
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import vm from "node:vm";

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(here, "..", "index.js"), "utf8");

function loadPanel({ local = [], playlist = [], folder = { id: 7, title: "Rap" }, responses = [] } = {}) {
  const calls = { add: [], remove: [], contents: [], fetch: [], localReads: 0 };
  const rootlist = {
    getContents: async () => ({
      items: [
        {
          type: "folder",
          name: "Music DLP",
          uri: "spotify:folder:dlp",
          items: [
            { type: "playlist", name: folder.title, uri: "spotify:playlist:rap" }
          ]
        }
      ]
    }),
    createFolder: async () => ({ uri: "spotify:folder:new" }),
    createPlaylist: async () => "spotify:playlist:new",
    move: async () => {}
  };
  const platform = {
    RootlistAPI: rootlist,
    PlaylistAPI: {
      getContents: async (uri, options) => {
        const offset = (options && options.offset) || 0;
        const limit = (options && options.limit) || 100;
        calls.contents.push({ uri, offset, limit });
        return { items: playlist.slice(offset, offset + limit) };
      },
      add: async (uri, uris) => { calls.add.push({ uri, uris }); },
      remove: async (uri, rows) => { calls.remove.push({ uri, rows }); }
    },
    LocalFilesAPI: {
      getTracks: async () => { calls.localReads += 1; return local; }
    }
  };

  const queue = responses.slice();
  const fetchStub = async (url, options) => {
    calls.fetch.push({ url, method: options.method, body: options.body ? JSON.parse(options.body) : undefined });
    if (!queue.length) throw new Error("unexpected fetch " + url);
    const next = queue.shift();
    if (next instanceof Error) throw next;
    return {
      ok: next.status === undefined || next.status < 400,
      status: next.status || 200,
      json: async () => next.body === undefined ? {} : next.body
    };
  };

  const context = {
    window: { Spicetify: { React: {}, Platform: platform } },
    fetch: fetchStub,
    AbortController,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    console
  };
  vm.createContext(context);
  const config = 'var MUSICDLP_CONFIG = {port: 8317, token: "test", dlpFolder: "Music DLP"};';
  vm.runInContext(
    source.replace("var MUSICDLP_CONFIG = __MUSICDLP_CONFIG__;", config),
    context
  );
  return { context, calls, platform, folder };
}

function uriList(count, prefix = "spotify:track:") {
  return Array.from({ length: count }, (_, i) => prefix + i);
}

test("a plan reads every page of the playlist and the whole Local Files index", async () => {
  const items = uriList(105).map((uri, i) => ({ uri, uid: "u" + i }));
  const local = [{ uri: "spotify:local:A:S:One:1", name: "One", artists: [{ name: "A" }], duration: 1 }];
  const { context, calls } = loadPanel({
    playlist: items,
    local,
    responses: [
      { body: { to_add: [], to_remove: [], unmatched: [], summary: {}, safe_to_remove: true } }
    ]
  });

  const result = await context.planSync({ id: 7, title: "Rap" });

  assert.equal(calls.contents.length, 2, "must request a second page");
  assert.deepEqual(calls.contents.map((c) => c.offset), [0, 100]);
  const body = calls.fetch.at(-1).body;
  assert.equal(body.playlist_uris.length, 105);
  assert.equal(body.local_files_complete, true);
  assert.equal(result.plan.safe_to_remove, true);
});

test("an empty Local Files index is reported as incomplete", async () => {
  const { context, calls } = loadPanel({
    playlist: [{ uri: "spotify:track:x", uid: "u1" }],
    local: [],
    responses: [
      { body: { to_add: [], to_remove: [], unmatched: [], summary: {}, safe_to_remove: false, reason: "incomplete" } }
    ]
  });

  await context.planSync({ id: 7, title: "Rap" });

  const body = calls.fetch.at(-1).body;
  assert.equal(body.local_files_complete, false);
});

test("adding many songs is chunked so Spotify accepts every call", async () => {
  const { context, calls } = loadPanel();
  const plan = { to_add: uriList(250), to_remove: [], summary: {} };

  await context.applySync("spotify:playlist:rap", plan, []);

  assert.deepEqual(calls.add.map((c) => c.uris.length), [100, 100, 50]);
});

test("removing many songs is chunked and carries their uids", async () => {
  const items = uriList(150, "spotify:local:A:S:X:").map((uri, i) => ({ uri, uid: "u" + i }));
  const { context, calls } = loadPanel();
  const plan = { to_add: [], to_remove: items.map((item) => item.uri), summary: {} };

  await context.applySync("spotify:playlist:rap", plan, items);

  assert.deepEqual(calls.remove.map((c) => c.rows.length), [100, 50]);
  assert.equal(calls.remove[0].rows[0].uid, "u0");
});

test("a stage only takes songs it can act on", () => {
  const { context } = loadPanel();
  const tracks = [
    { id: 1, has_file: true, has_raw: false, file_missing: false },
    { id: 2, has_file: false, has_raw: true, file_missing: false },
    { id: 3, has_file: true, has_raw: false, file_missing: true }
  ];

  assert.deepEqual(context.eligibleForStages(["sync"], tracks).map((t) => t.id), [1]);
  assert.deepEqual(context.eligibleForStages(["convert"], tracks).map((t) => t.id), [1, 2]);
  assert.deepEqual(context.eligibleForStages(["download", "convert", "sync"], tracks).map((t) => t.id), [1, 2, 3]);
});

test("a review dialog sends only the fields it owns", () => {
  const { context } = loadPanel();

  const body = context.reviewMetadataBody({
    track_id: 4,
    title: "New title",
    artist: "New artist",
    album: "An album",
    track_number: 9
  });

  assert.deepEqual(Object.keys(body).sort(), ["artist", "title", "track_id"]);
});

test("an album dialog sends the album and number it owns", () => {
  const { context } = loadPanel();

  const body = context.albumMetadataBody(
    { id: 4, title: "T", artist: "A", album: "", track_number: null },
    "An album",
    3
  );

  assert.equal(body.album, "An album");
  assert.equal(body.track_number, 3);
  assert.equal(body.title, "T");
});

test("a look-up dialog sends the album and number it returned", () => {
  const { context } = loadPanel();

  const body = context.lookupMetadataBody({
    track_id: 4, title: "T", artist: "A", album: "Album X", track_number: 4
  });

  assert.equal(body.album, "Album X");
  assert.equal(body.track_number, 4);
});

test("a refused request carries the bridge's own explanation", async () => {
  const { context } = loadPanel({
    responses: [{ status: 409, body: { error: "Music DLP is working on a job." } }]
  });

  await assert.rejects(
    () => context.api("POST", "/api/metadata", { track_id: 1 }),
    /working on a job/
  );
});

test("an unreachable app gives an explanation instead of Failed to fetch", async () => {
  const { context } = loadPanel({
    responses: [new TypeError("Failed to fetch")]
  });

  await assert.rejects(
    () => context.api("GET", "/api/health"),
    (err) => /could not be reached/.test(err.message) && /is running/.test(err.message)
  );
});

test("a token mismatch says what to do", async () => {
  const { context } = loadPanel({ responses: [{ status: 403, body: {} }] });

  await assert.rejects(
    () => context.api("GET", "/api/health"),
    /token mismatch/
  );
});

test("a plan with no changes does not claim the playlist is up to date when songs are unmatched", () => {
  const { context } = loadPanel();

  const text = context.describePlan({
    to_add: [],
    to_remove: [],
    summary: { unmatched: 3 }
  });

  assert.match(text, /3 song/);
  assert.doesNotMatch(text, /up to date/);
});

test("a plan with removals reports them", () => {
  const { context } = loadPanel();

  const text = context.describePlan({
    to_add: ["a", "b"],
    to_remove: ["c", "d", "e", "f", "g"],
    summary: { unmatched: 1 }
  });

  assert.match(text, /2 to add/);
  assert.match(text, /5 to remove/);
});

test("the panel shows the derived status, not the raw one", () => {
  const { context } = loadPanel();

  assert.equal(context.trackStatus({ status: "converted", display_status: "needs_update" }), "needs_update");
  assert.equal(context.trackStatus({ status: "converted" }), "converted");
});

test("identical tags are called out before a sync", () => {
  const { context } = loadPanel();
  const warning = context.duplicateTagsWarning({
    duplicate_tags: [{ title: "Same Name", artist: "Same Artist", track_ids: [1, 2] }]
  });
  assert.match(warning, /identical title, artist and length/);
  assert.match(warning, /Same Name/);
  assert.equal(context.duplicateTagsWarning({ duplicate_tags: [] }), "");
  assert.equal(context.duplicateTagsWarning({}), "");
});

test("a folder deleted on the desktop stops being selected", () => {
  /* M32: the panel's folder list was a snapshot. When a poll returns a
   * list without the open folder, the panel must move to a real one. */
  const { context } = loadPanel();
  const list = [{ id: 1, title: "A" }, { id: 2, title: "B" }];
  assert.equal(context.activeFolderAfterRefresh(list, 2), 2);
  assert.equal(context.activeFolderAfterRefresh([{ id: 1, title: "A" }], 2), 1);
  assert.equal(context.activeFolderAfterRefresh([], 2), null);
  assert.equal(context.activeFolderAfterRefresh(list, null), 1);
  assert.equal(context.sameFolders(list, JSON.parse(JSON.stringify(list))), true);
  assert.equal(context.sameFolders(list, []), false);
});
