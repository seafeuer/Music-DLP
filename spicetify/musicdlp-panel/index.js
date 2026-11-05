/* Music DLP panel for Spotify.
 *
 * Runs inside Spotify's renderer as a route component. Spicetify mounts
 * whatever `render` returns, so this file must export a component rather
 * than touch the DOM itself. Spotify's own APIs handle the playlists;
 * everything that touches the library on disk goes through the Music DLP
 * bridge on localhost. The bridge address and token are injected below by
 * spicetify/install-panel.ps1.
 */

var MUSICDLP_CONFIG = __MUSICDLP_CONFIG__;

/* Everything below is module scope on purpose: the wrapper Spicetify
 * generates around this file does `default: () => render`, so `render`
 * has to be a top-level binding.
 */

  var BASE = "http://127.0.0.1:" + MUSICDLP_CONFIG.port;
  var TOKEN = MUSICDLP_CONFIG.token;
  var DLP_FOLDER = MUSICDLP_CONFIG.dlpFolder || "Music DLP";

  var STATUS_LABELS = {
    new: "Not downloaded",
    queued: "Queued",
    downloading: "Downloading",
    downloaded: "Downloaded",
    converting: "Converting",
    converted: "Converted",
    syncing: "Sending to Spotify",
    synced: "In Spotify",
    error: "Error",
    cancelled: "Cancelled",
    needs_update: "Needs update",
    missing: "File missing",
    sync_error: "Spotify copy failed"
  };

  // Spotify accepts about 100 playlist entries per write. Batch bigger
  // selections instead of sending one oversized call that fails whole.
  var PLAYLIST_CHUNK = 100;
  // Pages of playlist contents to read before giving up.
  var PLAYLIST_PAGE = 100;

  // React is resolved lazily: Spicetify exposes it once its own bundle has
  // loaded, which is before this module is imported, but going through a
  // getter keeps the panel from depending on load order.
  function React_() { return window.Spicetify.React; }
  function h() {
    var react = React_();
    return react.createElement.apply(react, arguments);
  }

  function sleep(ms) {
    return new Promise(function (resolve) { setTimeout(resolve, ms); });
  }

  async function api(method, path, body) {
    // A request that never answers left every button disabled forever.
    var controller = typeof AbortController === "function"
      ? new AbortController() : null;
    var timer = controller
      ? setTimeout(function () { controller.abort(); }, 20000)
      : null;
    var response;
    try {
      response = await fetch(BASE + path, {
        method: method,
        headers: {
          Authorization: "Bearer " + TOKEN,
          "Content-Type": "application/json"
        },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller ? controller.signal : undefined
      });
    } catch (err) {
      // fetch() rejects for a stopped app, a blocked port and a CORS
      // failure alike. Say what to check instead of "Failed to fetch".
      throw new Error(
        "Music DLP could not be reached at " + BASE + ". Make sure the app "
        + "is running, that the panel is switched on in its settings, and "
        + "that Spotify was restarted after it was installed."
      );
    } finally {
      if (timer) clearTimeout(timer);
    }
    var data = null;
    try {
      data = await response.json();
    } catch (err) {
      data = null;
    }
    if (!response.ok) {
      if (response.status === 403) {
        throw new Error("Music DLP refused the request (token mismatch). "
          + "Restart Music DLP, then reload Spotify.");
      }
      throw new Error((data && data.error) || "Request failed (" + response.status + ")");
    }
    return data;
  }

  async function waitForJob(job) {
    var current = job;
    for (var i = 0; i < 1800; i++) {
      if (!current || current.status !== "running") return current;
      await sleep(400);
      current = await api("GET", "/api/jobs/" + current.id);
    }
    throw new Error("That job is taking too long. Check the Music DLP window.");
  }

  function statusLabel(status) {
    return STATUS_LABELS[status] || status;
  }

  function statusClass(status) {
    if (status === "synced") return "synced";
    if (status === "error" || status === "missing") return "error";
    if (status === "needs_update" || status === "sync_error") return "pending";
    if (status === "converted") return "converted";
    return "";
  }

  /* The status to show for a song.
   *
   * The bridge sends display_status, which folds in a deleted file and an
   * unsaved edit. Older bridges sent only status, so fall back to it.
   */
  function trackStatus(track) {
    return track.display_status || track.status;
  }

  /* Which ticked songs a stage can actually act on.
   *
   * The desktop filters this before starting a job; without the same
   * filter the panel used to start jobs that silently did nothing.
   */
  function eligibleFor(stage, tracks) {
    return (tracks || []).filter(function (track) {
      if (stage === "download") return true;
      if (stage === "convert") {
        // A retag works on the existing file; anything else needs the
        // download that is kept for converting.
        return !!(track.has_raw || (track.has_file && !track.file_missing));
      }
      if (stage === "sync") {
        return !!(track.has_file && !track.file_missing);
      }
      return false;
    });
  }

  /* The songs a multi-stage run can act on.
   *
   * The last stage decides: a download run can take anything, convert
   * needs downloaded audio, and sync needs a converted file.
   */
  function eligibleForStages(stages, tracks) {
    var list = tracks || [];
    if (stages.indexOf("download") >= 0) return list;
    if (stages.indexOf("sync") >= 0) return eligibleFor("sync", list);
    if (stages.indexOf("convert") >= 0) return eligibleFor("convert", list);
    return list;
  }

  /* The body a review dialog sends.
   *
   * Translate and AI reviews own a title and an artist. The album and
   * track number belong to other dialogs, so they are not sent: the bridge
   * writes only the fields it receives.
   */
  function reviewMetadataBody(row) {
    return {
      track_id: row.track_id,
      title: row.title,
      artist: row.artist
    };
  }

  function albumMetadataBody(track, album, number) {
    return {
      track_id: track.id,
      title: track.title,
      artist: track.artist,
      album: album,
      track_number: number
    };
  }

  function lookupMetadataBody(row) {
    return {
      track_id: row.track_id,
      title: row.title,
      artist: row.artist,
      album: row.album,
      track_number: row.track_number
    };
  }

  // ------------------------------------------------------------ Spotify side

  function platform() {
    return window.Spicetify.Platform;
  }

  function slimLocalTracks(tracks) {
    return (tracks || []).map(function (track) {
      return {
        uri: track.uri,
        name: track.name,
        artists: (track.artists || []).map(function (artist) { return { name: artist.name }; }),
        album: track.album ? { name: track.album.name } : null,
        duration: track.duration ? { milliseconds: track.duration.milliseconds } : null
      };
    });
  }

  async function findDlpFolder() {
    var root = await platform().RootlistAPI.getContents();
    var items = (root && root.items) || [];
    var found = items.filter(function (item) {
      return item.type === "folder" && item.name === DLP_FOLDER;
    })[0];
    if (found) return found;
    var created = await platform().RootlistAPI.createFolder(DLP_FOLDER, {});
    return { uri: created.uri, items: [], name: DLP_FOLDER };
  }

  /* The playlist that belongs to a Music DLP folder.
   *
   * Only playlists already inside the Music DLP folder are adopted, so a
   * playlist of the user's own that happens to share a name is never
   * touched. Anything else is created and moved in.
   */
  async function ensurePlaylist(folderName) {
    var dlp = await findDlpFolder();
    var existing = (dlp.items || []).filter(function (item) {
      return item.type === "playlist" && item.name === folderName;
    })[0];
    if (existing) return existing.uri;

    var uri = await platform().RootlistAPI.createPlaylist(folderName, {});
    if (typeof uri !== "string") uri = uri && uri.uri;
    await platform().RootlistAPI.move([{ uri: uri }], { after: { uri: dlp.uri } });
    return uri;
  }

  /* Read every entry of a playlist.
   *
   * getContents returns one page. Mapping that page as the whole playlist
   * made out-of-page songs look absent, so a sync could add duplicates and
   * never remove a song beyond the first page.
   */
  async function readPlaylist(playlistUri) {
    var items = [];
    var offset = 0;
    for (var page = 0; page < 100; page++) {
      var contents = await platform().PlaylistAPI.getContents(playlistUri, {
        offset: offset,
        limit: PLAYLIST_PAGE
      });
      var batch = (contents && contents.items) || [];
      items = items.concat(batch);
      if (batch.length < PLAYLIST_PAGE) break;
      offset += batch.length;
    }
    return items;
  }

  /* Read Spotify's Local Files index, and say whether the read is whole.
   *
   * An empty list is what Spotify returns while it is scanning or before
   * it has indexed a folder. The bridge refuses to plan removals on that
   * evidence, and this flag is how it knows.
   */
  async function readLocalFiles() {
    var tracks = await platform().LocalFilesAPI.getTracks();
    var list = tracks || [];
    return { tracks: list, complete: list.length > 0 };
  }

  /* Work out what a sync would do, without changing anything. */
  async function planSync(folder) {
    var playlistUri = await ensurePlaylist(folder.title);
    var items = await readPlaylist(playlistUri);
    var playlistUris = items.map(function (item) { return item.uri; });
    var local = await readLocalFiles();

    var plan = await api("POST", "/api/sync/plan", {
      folder_id: folder.id,
      local_tracks: slimLocalTracks(local.tracks),
      playlist_uris: playlistUris,
      local_files_complete: local.complete
    });
    return { playlistUri: playlistUri, items: items, plan: plan };
  }

  async function addInChunks(playlistUri, uris) {
    for (var i = 0; i < uris.length; i += PLAYLIST_CHUNK) {
      await platform().PlaylistAPI.add(
        playlistUri, uris.slice(i, i + PLAYLIST_CHUNK), {}
      );
    }
  }

  async function removeInChunks(playlistUri, items, uris) {
    var wanted = {};
    uris.forEach(function (uri) { wanted[uri] = true; });
    var rows = items
      .filter(function (item) { return wanted[item.uri]; })
      .map(function (item) { return { uid: item.uid || "", uri: item.uri }; });
    for (var i = 0; i < rows.length; i += PLAYLIST_CHUNK) {
      await platform().PlaylistAPI.remove(
        playlistUri, rows.slice(i, i + PLAYLIST_CHUNK)
      );
    }
  }

  /* Apply a plan the user has approved. Additions are safe; removals are
   * only ever sent after the preview dialog's explicit confirmation.
   */
  async function applySync(playlistUri, plan, items) {
    if (plan.to_add && plan.to_add.length) {
      await addInChunks(playlistUri, plan.to_add);
    }
    if (plan.to_remove && plan.to_remove.length) {
      await removeInChunks(playlistUri, items, plan.to_remove);
    }
    return { playlistUri: playlistUri, plan: plan };
  }

  /* Which folder to show after a fresh folder list arrives.
   *
   * The desktop can delete or rename folders while this panel is open, so
   * a folder that used to be selected may be gone. Fall back to the first
   * folder instead of keeping a stale id whose sync would 404.
   */
  function activeFolderAfterRefresh(list, currentId) {
    var items = list || [];
    if (currentId !== null && currentId !== undefined) {
      for (var i = 0; i < items.length; i++) {
        if (items[i].id === currentId) return currentId;
      }
    }
    return items.length ? items[0].id : null;
  }

  function sameFolders(left, right) {
    try {
      return JSON.stringify(left || []) === JSON.stringify(right || []);
    } catch (err) {
      return false;
    }
  }

  /* Songs that Spotify folds into a single local URI.
   *
   * Spotify keys a local file by title, artist and length, so two different
   * files with the same final tags are one playlist entry. The plan counts
   * both as matched; say so before the sync instead of silently losing one.
   */
  function duplicateTagsWarning(plan) {
    var duplicates = plan && plan.duplicate_tags;
    if (!duplicates || !duplicates.length) return "";
    var names = duplicates.map(function (item) {
      return "“" + item.title + "”"
        + (item.artist ? " by " + item.artist : "");
    });
    return duplicates.length + " pair(s) of songs have identical title, "
      + "artist and length, so Spotify will show only one of each: "
      + names.join("; ") + ". Edit one song's details before syncing.";
  }

  /* What to tell the user about a plan. */
  function describePlan(plan) {
    var summary = plan.summary || {};
    if (!plan.to_add.length && !plan.to_remove.length) {
      var text = "Playlist “up to date”.";
      if (summary.unmatched) {
        text = summary.unmatched + " song(s) are not in Spotify's Local "
          + "Files yet. Download and convert them first, then sync again.";
      }
      return text;
    }
    var lines = [];
    if (plan.to_add.length) lines.push(plan.to_add.length + " to add");
    if (plan.to_remove.length) lines.push(plan.to_remove.length + " to remove");
    if (summary.unmatched) lines.push(summary.unmatched + " unmatched");
    return lines.join(", ") + ".";
  }

  // ------------------------------------------------------------- components

  function Toast(props) {
    if (!props.toast) return null;
    return h("div", {
      className: "mdlp-toast " + (props.toast.kind === "error" ? "err" : "ok"),
      role: "status"
    },
      h("span", null, props.toast.text),
      h("button", {
        className: "mdlp-toast-close",
        "aria-label": "Dismiss",
        onClick: props.onClose
      }, "×")
    );
  }

  function Modal(props) {
    if (!props.title) return null;
    return h("div", { className: "mdlp-overlay", onClick: props.onClose },
      h("div", { className: "mdlp-modal", onClick: function (e) { e.stopPropagation(); } },
        h("h2", null, props.title),
        props.children
      )
    );
  }

  /* A sync is shown before it happens.
   *
   * Removals are destructive: a playlist can contain songs the user added
   * by hand. The confirm button says how many songs will go and is not
   * the default action, so pressing Enter cannot empty a playlist.
   */
  function SyncPreviewDialog(props) {
    var plan = props.plan;
    var summary = plan.summary || {};
    var removes = plan.to_remove.length;
    var adds = plan.to_add.length;
    var busyState = React_().useState(false);
    var busy = busyState[0], setBusy = busyState[1];

    async function apply() {
      setBusy(true);
      try {
        await applySync(props.playlistUri, plan, props.items);
        props.onDone("Playlist “" + props.folderTitle + "” updated: "
          + adds + " added, " + removes + " removed.");
      } catch (err) {
        props.onError(err.message);
      } finally {
        setBusy(false);
      }
    }

    return h(Modal, { title: "Sync “" + props.folderTitle + "”", onClose: props.onClose },
      h("p", { className: "mdlp-hint" }, describePlan(plan)),
      h("ul", { className: "mdlp-preview" },
        h("li", null, adds + " song(s) will be added"),
        h("li", { className: removes ? "warn" : "" },
          removes + " song(s) will be removed from the playlist"),
        h("li", null, (summary.unmatched || 0) + " song(s) have no Local Files match")),
      plan.reason ? h("p", { className: "mdlp-unsure" }, plan.reason) : null,
      duplicateTagsWarning(plan)
        ? h("p", { className: "mdlp-unsure" }, duplicateTagsWarning(plan))
        : null,
      removes
        ? h("p", { className: "mdlp-unsure" },
            "Songs you added to this playlist by hand are removed too. "
            + "Cancel to leave the playlist untouched.")
        : null,
      h("div", { className: "mdlp-actions" },
        h("button", { className: "mdlp-btn", onClick: props.onClose },
          removes ? "Cancel" : "Close"),
        adds || removes
          ? h("button", {
              className: "mdlp-btn " + (removes ? "danger" : "primary"),
              disabled: busy,
              onClick: apply
            }, busy ? "Applying…" : (removes
              ? "Remove " + removes + " and add " + adds
              : "Add " + adds + " song(s)"))
          : null)
    );
  }

  function TrackTable(props) {
    var React = React_();
    var tracks = props.tracks;
    if (!tracks.length) {
      return h("div", { className: "mdlp-table-wrap" },
        h("div", { className: "mdlp-empty" }, "No songs here yet."));
    }
    var allSelected = tracks.length > 0 && tracks.every(function (t) {
      return props.selected.has(t.id);
    });
    return h("div", { className: "mdlp-table-wrap" },
      h("table", { className: "mdlp-table" },
        h("thead", null,
          h("tr", null,
            h("th", { style: { width: "34px" } },
              h("input", {
                type: "checkbox",
                checked: allSelected,
                onChange: function () { props.onSelectAll(!allSelected); }
              })),
            h("th", null, "Title"),
            h("th", null, "Artist"),
            h("th", null, "Album"),
            h("th", null, "Status")
          )
        ),
        h("tbody", null,
          tracks.map(function (track) {
            var selected = props.selected.has(track.id);
            return h("tr", {
              key: track.id,
              className: selected ? "selected" : "",
              onDoubleClick: function () { props.onEdit(track); }
            },
              h("td", null,
                h("input", {
                  type: "checkbox",
                  checked: selected,
                  onChange: function () { props.onToggle(track.id); }
                })),
              h("td", null, h("div", { className: "mdlp-title" }, track.title)),
              h("td", null, h("div", { className: "mdlp-artist" }, track.artist || "—")),
              h("td", null, h("div", { className: "mdlp-artist" }, track.album || "—")),
              h("td", null,
                h("span", { className: "mdlp-status " + statusClass(trackStatus(track)) },
                  statusLabel(trackStatus(track))))
            );
          })
        )
      )
    );
  }

  function EditDialog(props) {
    var React = React_();
    var track = props.track;
    var titleState = React.useState(track.title);
    var artistState = React.useState(track.artist);
    var albumState = React.useState(track.album);
    var numberState = React.useState(track.track_number || "");
    var updateState = React.useState(false);
    var busyState = React.useState("");
    var title = titleState[0], setTitle = titleState[1];
    var artist = artistState[0], setArtist = artistState[1];
    var album = albumState[0], setAlbum = albumState[1];
    var number = numberState[0], setNumber = numberState[1];
    var updateFile = updateState[0], setUpdateFile = updateState[1];
    var busy = busyState[0], setBusy = busyState[1];
    var canUpdate = track.status === "converted" || track.status === "synced";

    async function save() {
      setBusy("Saving…");
      try {
        await api("POST", "/api/metadata", {
          track_id: track.id,
          title: title,
          artist: artist,
          album: album,
          track_number: number === "" ? null : Number(number)
        });
        var started = false;
        if (updateFile && canUpdate) {
          var stages = ["convert"];
          if (!track.has_file && !track.has_raw) {
            stages = ["download", "convert"];
          }
          if (track.status === "synced") stages.push("sync");
          // force rebuilds missing audio; retag rewrites the existing file
          // instead of re-encoding it.
          var result = await api("POST", "/api/jobs", {
            track_ids: [track.id],
            stages: stages,
            force: true,
            retag: true
          });
          if (!result.started) {
            throw new Error(result.reason || "Music DLP could not start the update.");
          }
          started = true;
        }
        props.onSaved(started ? "Saved and updating the file." : "Saved.",
          { started: started });
      } catch (err) {
        props.onError(err.message);
      } finally {
        setBusy("");
      }
    }

    async function cover(action) {
      setBusy("Working on the cover…");
      try {
        var result = await api("POST", "/api/cover", { track_id: track.id, action: action });
        if (action === "lookup") {
          props.onInfo(result.result === "updated"
            ? "Cover replaced."
            : "No better cover was found (" + result.result + ").");
        } else {
          props.onInfo("Using the video picture.");
        }
      } catch (err) {
        props.onError(err.message);
      } finally {
        setBusy("");
      }
    }

    return h(Modal, { title: "Edit details", onClose: props.onClose },
      h("div", { className: "mdlp-field" },
        h("label", null, "Title"),
        h("input", { className: "mdlp-input", value: title,
          onChange: function (e) { setTitle(e.target.value); } })),
      h("div", { className: "mdlp-field" },
        h("label", null, "Artist"),
        h("input", { className: "mdlp-input", value: artist,
          onChange: function (e) { setArtist(e.target.value); } })),
      h("div", { className: "mdlp-field" },
        h("label", null, "Album"),
        h("input", { className: "mdlp-input", value: album,
          onChange: function (e) { setAlbum(e.target.value); } })),
      h("div", { className: "mdlp-field" },
        h("label", null, "Track number"),
        h("input", { className: "mdlp-input", value: number, type: "number",
          onChange: function (e) { setNumber(e.target.value); } })),
      h("div", { className: "mdlp-row" },
        h("button", { className: "mdlp-btn", disabled: !!busy,
          onClick: function () { cover("lookup"); } }, "Find cover art"),
        h("button", { className: "mdlp-btn", disabled: !!busy,
          onClick: function () { cover("video"); } }, "Use video picture")),
      canUpdate ? h("label", { className: "mdlp-check", style: { marginTop: "12px" } },
        h("input", { type: "checkbox", checked: updateFile,
          onChange: function (e) { setUpdateFile(e.target.checked); } }),
        "Update the audio file now") : null,
      h("div", { className: "mdlp-actions" },
        h("button", { className: "mdlp-btn", onClick: props.onClose }, "Cancel"),
        h("button", { className: "mdlp-btn primary", disabled: !!busy, onClick: save },
          busy || "Save"))
    );
  }

  function ReviewDialog(props) {
    var React = React_();
    var rowsState = React.useState(props.rows);
    var busyState = React.useState(false);
    var rows = rowsState[0], setRows = rowsState[1];
    var busy = busyState[0], setBusy = busyState[1];

    function toggle(index) {
      var next = rows.slice();
      next[index] = Object.assign({}, next[index], { checked: !next[index].checked });
      setRows(next);
    }

    function edit(index, key, value) {
      var next = rows.slice();
      var row = Object.assign({}, next[index]);
      row[key] = value;
      next[index] = row;
      setRows(next);
    }

    async function apply() {
      setBusy(true);
      try {
        var chosen = rows.filter(function (row) { return row.checked; });
        for (var i = 0; i < chosen.length; i++) {
          await api("POST", "/api/metadata", reviewMetadataBody(chosen[i]));
        }
        props.onDone("Applied to " + chosen.length + " song(s).");
      } catch (err) {
        props.onError(err.message);
      } finally {
        setBusy(false);
      }
    }

    return h(Modal, { title: props.title, onClose: props.onClose },
      props.intro ? h("p", { className: "mdlp-hint" }, props.intro) : null,
      h("table", { className: "mdlp-review-table" },
        h("thead", null,
          h("tr", null,
            h("th", { style: { width: "30px" } }),
            h("th", null, "Song"),
            h("th", null, "New title"),
            h("th", null, "New artist"))),
        h("tbody", null,
          rows.map(function (row, index) {
            return h("tr", { key: row.track_id },
              h("td", null,
                h("input", { type: "checkbox", checked: !!row.checked,
                  onChange: function () { toggle(index); } })),
              h("td", null,
                h("div", null, row.original_title),
                row.unsure ? h("div", { className: "mdlp-unsure" }, "Unsure") : null,
                row.error ? h("div", { className: "mdlp-unsure" }, row.error) : null),
              h("td", null,
                h("input", { type: "text", value: row.title,
                  onChange: function (e) { edit(index, "title", e.target.value); } })),
              h("td", null,
                h("input", { type: "text", value: row.artist,
                  onChange: function (e) { edit(index, "artist", e.target.value); } })));
          }))),
      h("div", { className: "mdlp-actions" },
        h("button", { className: "mdlp-btn", onClick: props.onClose }, "Cancel"),
        h("button", { className: "mdlp-btn primary", disabled: busy, onClick: apply },
          busy ? "Applying…" : "Apply selected"))
    );
  }

  function AlbumDialog(props) {
    var React = React_();
    var albumState = React.useState("");
    var startState = React.useState(1);
    var busyState = React.useState(false);
    var updateState = React.useState(true);
    var album = albumState[0], setAlbum = albumState[1];
    var start = startState[0], setStart = startState[1];
    var busy = busyState[0], setBusy = busyState[1];
    var updateFile = updateState[0], setUpdateFile = updateState[1];

    async function apply() {
      if (!album.trim()) return;
      setBusy(true);
      try {
        var ids = [];
        for (var i = 0; i < props.tracks.length; i++) {
          await api("POST", "/api/metadata", albumMetadataBody(
            props.tracks[i], album.trim(), Number(start) + i
          ));
          ids.push(props.tracks[i].id);
        }
        var started = false;
        if (updateFile) {
          // Numbering alone does not touch the files. Without this the
          // album and track number stay in the database while Spotify
          // keeps showing the old tags.
          var result = await api("POST", "/api/rewrite", { track_ids: ids });
          started = !!(result && result.started);
        }
        props.onDone(
          "Numbered " + props.tracks.length + " song(s)."
          + (updateFile
            ? (started
              ? " Rewriting their files now."
              : " Tick the songs and press Rewrite files to update them.")
            : ""),
          { started: started }
        );
      } catch (err) {
        props.onError(err.message);
      } finally {
        setBusy(false);
      }
    }

    return h(Modal, { title: "Number songs as an album", onClose: props.onClose },
      h("p", { className: "mdlp-hint" },
        "Every ticked song gets this album name and consecutive track numbers, "
        + "so Spotify shows them as one album."),
      h("div", { className: "mdlp-field" },
        h("label", null, "Album name"),
        h("input", { className: "mdlp-input", value: album,
          onChange: function (e) { setAlbum(e.target.value); } })),
      h("div", { className: "mdlp-field" },
        h("label", null, "First track number"),
        h("input", { className: "mdlp-input", type: "number", value: start,
          onChange: function (e) { setStart(e.target.value); } })),
      h("label", { className: "mdlp-check" },
        h("input", { type: "checkbox", checked: updateFile,
          onChange: function (e) { setUpdateFile(e.target.checked); } }),
        "Rewrite the files so Spotify shows the new tags"),
      h("div", { className: "mdlp-actions" },
        h("button", { className: "mdlp-btn", onClick: props.onClose }, "Cancel"),
        h("button", { className: "mdlp-btn primary", disabled: busy, onClick: apply },
          busy ? "Applying…" : "Apply"))
    );
  }

  function LookupDialog(props) {
    // Album and track number come from MusicBrainz; title and artist stay
    // as they are unless the catalogue disagreed.
    var React = React_();
    var rowsState = React.useState(props.rows);
    var busyState = React.useState(false);
    var rows = rowsState[0], setRows = rowsState[1];
    var busy = busyState[0], setBusy = busyState[1];

    function toggle(index) {
      var next = rows.slice();
      next[index] = Object.assign({}, next[index], { checked: !next[index].checked });
      setRows(next);
    }

    function edit(index, key, value) {
      var next = rows.slice();
      var row = Object.assign({}, next[index]);
      row[key] = value;
      next[index] = row;
      setRows(next);
    }

    async function apply() {
      setBusy(true);
      try {
        var chosen = rows.filter(function (row) { return row.checked; });
        for (var i = 0; i < chosen.length; i++) {
          await api("POST", "/api/metadata", lookupMetadataBody(chosen[i]));
        }
        props.onDone("Applied album details to " + chosen.length + " song(s).");
      } catch (err) {
        props.onError(err.message);
      } finally {
        setBusy(false);
      }
    }

    return h(Modal, { title: props.title, onClose: props.onClose },
      props.intro ? h("p", { className: "mdlp-hint" }, props.intro) : null,
      h("table", { className: "mdlp-review-table" },
        h("thead", null,
          h("tr", null,
            h("th", { style: { width: "30px" } }),
            h("th", null, "Song"),
            h("th", null, "Album"),
            h("th", null, "Track"))),
        h("tbody", null,
          rows.map(function (row, index) {
            return h("tr", { key: row.track_id },
              h("td", null,
                h("input", { type: "checkbox", checked: !!row.checked,
                  onChange: function () { toggle(index); } })),
              h("td", null,
                h("div", null, row.original_title),
                row.error ? h("div", { className: "mdlp-unsure" }, row.error) : null),
              h("td", null,
                h("input", { type: "text", value: row.album || "",
                  onChange: function (e) { edit(index, "album", e.target.value); } })),
              h("td", null,
                h("input", { type: "number", value: row.track_number || "",
                  style: { width: "70px" },
                  onChange: function (e) { edit(index, "track_number",
                    e.target.value === "" ? null : Number(e.target.value)); } })));
          }))),
      h("div", { className: "mdlp-actions" },
        h("button", { className: "mdlp-btn", onClick: props.onClose }, "Cancel"),
        h("button", { className: "mdlp-btn primary", disabled: busy, onClick: apply },
          busy ? "Applying…" : "Apply selected"))
    );
  }

  function Panel() {
    var React = React_();
    var connectionState = React.useState("connecting");
    var healthState = React.useState(null);
    var foldersState = React.useState([]);
    var activeState = React.useState(null);
    var tracksState = React.useState([]);
    var selectedState = React.useState(new Set());
    var busyState = React.useState("");
    var toastState = React.useState(null);
    var progressState = React.useState(null);
    var dialogState = React.useState(null);
    var linkState = React.useState("");
    var loadingState = React.useState(false);
    var reasonState = React.useState("");
    // True only while a pipeline job is running. Bridge jobs (add, link,
    // translate, AI, covers, Drive) are awaited by their own call, so the
    // pipeline poll must not clear their busy state after a second.
    var watchingState = React.useState(false);

    var connection = connectionState[0], setConnection = connectionState[1];
    var health = healthState[0], setHealth = healthState[1];
    var folders = foldersState[0], setFolders = foldersState[1];
    var activeFolder = activeState[0], setActiveFolder = activeState[1];
    var tracks = tracksState[0], setTracks = tracksState[1];
    var selected = selectedState[0], setSelected = selectedState[1];
    var busy = busyState[0], setBusy = busyState[1];
    var toast = toastState[0], setToast = toastState[1];
    var progress = progressState[0], setProgress = progressState[1];
    var dialog = dialogState[0], setDialog = dialogState[1];
    var link = linkState[0], setLink = linkState[1];
    var loading = loadingState[0], setLoading = loadingState[1];
    var reason = reasonState[0], setReason = reasonState[1];
    var watching = watchingState[0], setWatching = watchingState[1];
    // The health poll lives for the life of the panel; a ref is how it
    // knows which folder is open without being torn down on every switch.
    var activeRef = React.useRef(null);
    activeRef.current = activeFolder;

    function notify(text, kind) {
      setToast({ text: text, kind: kind || "ok" });
    }

    async function refreshFolders() {
      var list = await api("GET", "/api/folders");
      setFolders(function (previous) {
        return sameFolders(previous, list) ? previous : list;
      });
      return list;
    }

    /* Follow a changed folder list: switch away from a folder that the
     * desktop deleted or renamed. */
    function adoptFolders(list) {
      var id = activeFolderAfterRefresh(list, activeRef.current);
      if (id === activeRef.current) return;
      setActiveFolder(id);
      activeRef.current = id;
      loadTracks(id).catch(function () {});
    }

    async function loadTracks(folderId) {
      var path = folderId ? "/api/tracks?folder=" + folderId : "/api/tracks";
      var list = await api("GET", path);
      setTracks(list);
      setSelected(new Set());
    }

    React.useEffect(function () {
      var alive = true;
      var wasOffline = false;

      async function first() {
        try {
          var info = await api("GET", "/api/health");
          if (!alive) return;
          setHealth(info);
          setConnection("online");
          setReason("");
          var list = await refreshFolders();
          if (!alive) return;
          if (list.length) {
            setActiveFolder(list[0].id);
            await loadTracks(list[0].id);
          }
        } catch (err) {
          if (alive) {
            setConnection("offline");
            setReason(err && err.message ? err.message : "");
          }
        }
      }

      first();

      var timer = setInterval(function () {
        api("GET", "/api/health")
          .then(function (info) {
            if (!alive) return;
            setHealth(info);
            setConnection("online");
            setReason("");
            if (wasOffline) {
              // The app was restarted while the panel was open: its
              // folders and songs may be different now.
              wasOffline = false;
              refreshFolders().catch(function () {});
              loadTracks(activeRef.current).catch(function () {});
            }
          })
          .catch(function (err) {
            if (!alive) return;
            wasOffline = true;
            setConnection("offline");
            setReason(err && err.message ? err.message : "");
          });
      }, 5000);

      return function () { alive = false; clearInterval(timer); };
    }, []);

    // The folder list used to be a snapshot: a folder added, renamed or
    // deleted in the desktop app stayed invisible here until the panel was
    // reloaded. Poll quietly while the panel is visible, and follow a
    // folder that disappeared.
    React.useEffect(function () {
      function refreshQuietly() {
        refreshFolders().then(adoptFolders).catch(function () {});
      }
      var timer = setInterval(function () {
        if (typeof document !== "undefined" && document.visibilityState
            && document.visibilityState !== "visible") {
          return;
        }
        refreshQuietly();
      }, 20000);
      function onVisible() {
        if (typeof document === "undefined"
            || document.visibilityState === "visible") {
          refreshQuietly();
        }
      }
      if (typeof document !== "undefined" && document.addEventListener) {
        document.addEventListener("visibilitychange", onVisible);
      }
      return function () {
        clearInterval(timer);
        if (typeof document !== "undefined" && document.removeEventListener) {
          document.removeEventListener("visibilitychange", onVisible);
        }
      };
    }, []);

    // A toast left on screen forever hid the panel and stacked errors.
    React.useEffect(function () {
      if (!toast) return;
      var timer = setTimeout(function () { setToast(null); }, 8000);
      return function () { clearTimeout(timer); };
    }, [toast]);

    async function refreshAll() {
      setLoading(true);
      try {
        var list = await refreshFolders();
        var id = activeFolderAfterRefresh(list, activeRef.current);
        if (id !== activeRef.current) {
          setActiveFolder(id);
          activeRef.current = id;
        }
        await loadTracks(id);
      } catch (err) {
        notify(err.message, "error");
      } finally {
        setLoading(false);
      }
    }

    async function chooseFolder(id) {
      setActiveFolder(id);
      setLoading(true);
      try {
        await loadTracks(id);
      } catch (err) {
        notify(err.message, "error");
      } finally {
        setLoading(false);
      }
    }

    // Follow the desktop app's progress while a pipeline job runs. This
    // effect is tied to watching, not to busy: bridge jobs finish inside
    // their own awaited call, and the pipeline flag is false the whole
    // time they run, which used to clear their busy state after a second.
    React.useEffect(function () {
      if (!watching) return;
      var timer = setInterval(function () {
        api("GET", "/api/status").then(function (status) {
          if (status.progress && Object.keys(status.progress).length) {
            setProgress(status.progress);
          }
          if (!status.pipeline_running) {
            loadTracks(activeRef.current).catch(function () {});
            refreshFolders().catch(function () {});
            setBusy("");
            setProgress(null);
            setWatching(false);
          }
        }).catch(function () {});
      }, 1000);
      return function () { clearInterval(timer); };
    }, [watching]);

    var selectedTracks = tracks.filter(function (t) { return selected.has(t.id); });

    function toggle(id) {
      var next = new Set(selected);
      if (next.has(id)) next.delete(id); else next.add(id);
      setSelected(next);
    }

    function selectAll(on) {
      setSelected(on ? new Set(tracks.map(function (t) { return t.id; })) : new Set());
    }

    async function addLink() {
      var url = link.trim();
      if (!url) return;
      setBusy("Adding the link…");
      try {
        var job = await api("POST", "/api/links", { urls: [url] });
        var done = await waitForJob(job);
        if (done.status === "error") throw new Error(done.error);
        setLink("");
        await refreshFolders();
        notify("Added " + done.result.tracks_new + " song(s).");
      } catch (err) {
        notify(err.message, "error");
      } finally {
        setBusy("");
      }
    }

    async function runStages(stages) {
      if (!selectedTracks.length) {
        notify("Tick the songs first.", "error");
        return;
      }
      var chosen = eligibleForStages(stages, selectedTracks);
      var skipped = selectedTracks.length - chosen.length;
      if (!chosen.length) {
        notify(
          "None of the ticked songs can be used for this step. Download "
          + "them first, or tick songs that are already downloaded.",
          "error"
        );
        return;
      }
      setBusy("Starting " + stages.join(" → ") + "…");
      try {
        var result = await api("POST", "/api/jobs", {
          track_ids: chosen.map(function (t) { return t.id; }),
          stages: stages
        });
        if (!result.started) {
          setBusy("");
          notify(result.reason || "Music DLP could not start the job.", "error");
          return;
        }
        setWatching(true);
        notify("Working on " + chosen.length + " song(s)."
          + (skipped ? " " + skipped + " skipped (not downloaded yet)." : ""));
      } catch (err) {
        notify(err.message, "error");
        setBusy("");
      }
    }

    async function doSync() {
      var folderId = activeRef.current;
      setBusy("Checking the playlist…");
      try {
        // The desktop may have renamed or deleted the folder since the
        // panel loaded it; syncing a stale id would plan removals for a
        // folder that no longer exists.
        var list = await refreshFolders();
        var folder = list.filter(function (f) { return f.id === folderId; })[0];
        if (!folder) {
          setBusy("");
          notify("That folder no longer exists. Choose a folder and try again.", "error");
          return;
        }

        var result = await planSync(folder);
        var plan = result.plan;
        setBusy("");

        if (!plan.to_add.length && !plan.to_remove.length) {
          notify(describePlan(plan));
          if (plan.reason && !plan.safe_to_remove) notify(plan.reason, "error");
          return;
        }

        // Nothing is changed until the preview is confirmed. Removals in
        // particular can include songs the user added by hand.
        setDialog({
          type: "sync",
          folderTitle: folder.title,
          playlistUri: result.playlistUri,
          items: result.items,
          plan: plan
        });
      } catch (err) {
        setBusy("");
        notify(err.message, "error");
      }
    }

    async function sendToDrive() {
      if (!selectedTracks.length) return notify("Tick the songs first.", "error");
      setBusy("Sending to Google Drive…");
      try {
        var job = await api("POST", "/api/gdrive", {
          track_ids: selectedTracks.map(function (t) { return t.id; })
        });
        var done = await waitForJob(job);
        if (done.status === "error") throw new Error(done.error);
        notify("Sent " + done.result.sent + " file(s) to "
          + (done.result.destination || "Google Drive") + ".");
      } catch (err) {
        notify(err.message, "error");
      } finally {
        setBusy("");
      }
    }

    async function translate() {
      if (!selectedTracks.length) return notify("Tick the songs first.", "error");
      setBusy("Translating…");
      try {
        var job = await api("POST", "/api/translate", {
          track_ids: selectedTracks.map(function (t) { return t.id; })
        });
        var done = await waitForJob(job);
        if (done.status === "error") throw new Error(done.error);
        if (done.result.error) throw new Error(done.result.error);
        var rows = done.result.translations.map(function (item) {
          var original = tracks.filter(function (t) { return t.id === item.track_id; })[0];
          return {
            track_id: item.track_id,
            original_title: original ? original.title : "",
            title: item.combined_title || item.title,
            artist: item.combined_artist || item.artist,
            checked: !item.already_target && item.confidence >= 0.6,
            unsure: item.confidence < 0.6
          };
        });
        setDialog({ type: "review", title: "Translated titles", rows: rows,
          intro: "Nothing is saved until you press Apply." });
      } catch (err) {
        notify(err.message, "error");
      } finally {
        setBusy("");
      }
    }

    async function fixCovers() {
      if (!selectedTracks.length) return notify("Tick the songs first.", "error");
      setBusy("Fixing covers…");
      try {
        var job = await api("POST", "/api/cover/bulk", {
          track_ids: selectedTracks.map(function (t) { return t.id; })
        });
        var done = await waitForJob(job);
        if (done.status === "error") throw new Error(done.error);
        var counts = done.result.counts || {};
        var parts = Object.keys(counts).map(function (key) { return key + ": " + counts[key]; });
        notify("Covers — " + (parts.join(", ") || "nothing to do")
          + (done.result.errors.length ? "\n" + done.result.errors.join("\n") : ""));
        loadTracks(activeRef.current).catch(function () {});
      } catch (err) {
        notify(err.message, "error");
      } finally {
        setBusy("");
      }
    }

    async function lookUpDetails() {
      if (!selectedTracks.length) return notify("Tick the songs first.", "error");
      setBusy("Asking MusicBrainz…");
      try {
        var job = await api("POST", "/api/lookup", {
          track_ids: selectedTracks.map(function (t) { return t.id; })
        });
        var done = await waitForJob(job);
        if (done.status === "error") throw new Error(done.error);
        var rows = done.result.results.map(function (item) {
          var original = tracks.filter(function (t) { return t.id === item.track_id; })[0];
          return {
            track_id: item.track_id,
            original_title: original ? original.title : "",
            title: original ? original.title : "",
            artist: item.artist || (original ? original.artist : ""),
            album: item.album || "",
            track_number: item.track_number,
            checked: !item.error && !!(item.album || item.track_number),
            unsure: !!item.error,
            error: item.error || ""
          };
        });
        setDialog({ type: "lookup", title: "Album details from MusicBrainz", rows: rows,
          intro: "Nothing is saved until you press Apply." });
      } catch (err) {
        notify(err.message, "error");
      } finally {
        setBusy("");
      }
    }

    async function rewriteFiles() {
      if (!selectedTracks.length) return notify("Tick the songs first.", "error");
      setBusy("Rewriting the files…");
      try {
        var result = await api("POST", "/api/rewrite", {
          track_ids: selectedTracks.map(function (t) { return t.id; })
        });
        if (!result.started) {
          setBusy("");
          notify(result.reason || "Music DLP could not start the rewrite.", "error");
          return;
        }
        setWatching(true);
        notify("Rewriting " + result.tracks + " file(s)…");
      } catch (err) {
        notify(err.message, "error");
        setBusy("");
      }
    }

    async function askAi() {      if (!selectedTracks.length) return notify("Tick the songs first.", "error");
      setBusy("Asking the model…");
      try {
        var job = await api("POST", "/api/ai/names", {
          track_ids: selectedTracks.map(function (t) { return t.id; })
        });
        var done = await waitForJob(job);
        if (done.status === "error") throw new Error(done.error);
        var rows = done.result.suggestions.map(function (item) {
          var original = tracks.filter(function (t) { return t.id === item.track_id; })[0];
          return {
            track_id: item.track_id,
            original_title: original ? original.title : "",
            title: item.title || "",
            artist: item.artist || "",
            checked: !item.error && item.confidence >= 0.6,
            unsure: item.confidence < 0.6,
            error: item.error || ""
          };
        });
        setDialog({ type: "review", title: "AI suggested names", rows: rows,
          intro: "Review before applying. Set up a model in Music DLP → Settings → AI." });
      } catch (err) {
        notify(err.message, "error");
      } finally {
        setBusy("");
      }
    }

    function closeDialog() { setDialog(null); }

    function finish(text, extra) {
      notify(text);
      closeDialog();
      if (extra && extra.started) setWatching(true);
      loadTracks(activeRef.current).catch(function () {});
    }

    var folderList = h("aside", { className: "mdlp-folders" },
      h("div", {
        className: "mdlp-folder " + (activeFolder === null ? "active" : ""),
        onClick: function () { chooseFolder(null); }
      }, h("span", null, "All songs")),
      folders.map(function (folder) {
        return h("div", {
          key: folder.id,
          className: "mdlp-folder " + (activeFolder === folder.id ? "active" : ""),
          onClick: function () { chooseFolder(folder.id); }
        },
          h("span", null, folder.title),
          h("span", { className: "count" }, folder.tracks));
      })
    );

    var percent = progress && progress.total
      ? Math.round((progress.done || 0) / progress.total * 100)
      : 0;

    return h("div", { className: "musicdlp-app" },
      h("div", { className: "mdlp-header" },
        h("h1", null, "Music DLP"),
        h("span", { className: "mdlp-badge " + connection }, connection),
        health ? h("span", { className: "mdlp-hint" },
          "Bridge " + health.version + " · playlists go to “" + health.dlp_folder + "”") : null,
        h("button", {
          className: "mdlp-btn mdlp-refresh",
          onClick: refreshAll,
          disabled: loading,
          title: "Reload folders and songs from Music DLP"
        }, "Refresh")
      ),

      connection === "offline"
        ? h("div", { className: "mdlp-banner err" },
            "Music DLP is not running, or the bridge is switched off. "
            + "Open the Music DLP app on this computer and leave it running."
            + (reason ? "\n" + reason : ""))
        : null,

      h("div", { className: "mdlp-toolbar" },
        h("input", {
          className: "mdlp-input",
          placeholder: "Paste a YouTube or X link…",
          value: link,
          onChange: function (e) { setLink(e.target.value); },
          onKeyDown: function (e) { if (e.key === "Enter") addLink(); }
        }),
        h("button", { className: "mdlp-btn", onClick: addLink, disabled: !!busy }, "Add"),
        h("button", { className: "mdlp-btn", disabled: !!busy,
          onClick: function () { runStages(["download", "convert", "sync"]); } },
          "Download to Spotify"),
        h("button", { className: "mdlp-btn primary", disabled: !!busy, onClick: doSync },
          "Sync playlist"),
        h("button", { className: "mdlp-btn", disabled: !!busy || !selectedTracks.length,
          onClick: function () { setDialog({ type: "album" }); } },
          "Number as album"),
        h("button", { className: "mdlp-btn", disabled: !!busy, onClick: sendToDrive },
          "Send to Drive")
      ),

      h("div", { className: "mdlp-toolbar" },
        h("button", { className: "mdlp-btn", disabled: !!busy,
          onClick: function () { runStages(["download"]); } }, "Download"),
        h("button", { className: "mdlp-btn", disabled: !!busy,
          onClick: function () { runStages(["convert"]); } }, "Convert"),
        h("button", { className: "mdlp-btn", disabled: !!busy,
          onClick: function () { runStages(["sync"]); } }, "Send to Spotify"),
        h("button", { className: "mdlp-btn", disabled: !!busy, onClick: translate },
          "Translate"),
        h("button", { className: "mdlp-btn", disabled: !!busy, onClick: askAi },
          "Ask AI"),
        h("button", { className: "mdlp-btn", disabled: !!busy, onClick: lookUpDetails },
          "Look up details"),
        h("button", { className: "mdlp-btn", disabled: !!busy, onClick: fixCovers },
          "Fix covers"),
        h("button", { className: "mdlp-btn", disabled: !!busy, onClick: rewriteFiles },
          "Rewrite files"),
        selectedTracks.length === 1
          ? h("button", { className: "mdlp-btn", disabled: !!busy,
              onClick: function () { setDialog({ type: "edit", track: selectedTracks[0] }); } },
              "Edit details")
          : null,
        h("span", { className: "mdlp-hint" },
          selectedTracks.length ? selectedTracks.length + " ticked" : "Nothing ticked")
      ),

      h("div", { className: "mdlp-layout" },
        folderList,
        loading
          ? h("div", { className: "mdlp-table-wrap" },
              h("div", { className: "mdlp-empty" }, "Loading…"))
          : h(TrackTable, {
              tracks: tracks,
              selected: selected,
              onToggle: toggle,
              onSelectAll: selectAll,
              onEdit: function (track) { setDialog({ type: "edit", track: track }); }
            })
      ),

      busy ? h("div", { className: "mdlp-progress" },
        h("div", null, busy + (progress && progress.label ? " · " + progress.label : "")),
        progress && progress.total
          ? h("div", { className: "mdlp-bar" }, h("div", { style: { width: percent + "%" } }))
          : null
      ) : null,

      dialog && dialog.type === "edit"
        ? h(EditDialog, {
            track: dialog.track,
            onClose: closeDialog,
            onInfo: function (text) { notify(text); },
            onError: function (text) { notify(text, "error"); },
            onSaved: function (text, extra) { finish(text, extra); }
          })
        : null,

      dialog && dialog.type === "review"
        ? h(ReviewDialog, {
            title: dialog.title,
            intro: dialog.intro,
            rows: dialog.rows,
            onClose: closeDialog,
            onError: function (text) { notify(text, "error"); },
            onDone: function (text, extra) { finish(text, extra); }
          })
        : null,

      dialog && dialog.type === "album"
        ? h(AlbumDialog, {
            tracks: selectedTracks,
            onClose: closeDialog,
            onError: function (text) { notify(text, "error"); },
            onDone: function (text, extra) { finish(text, extra); }
          })
        : null,

      dialog && dialog.type === "lookup"
        ? h(LookupDialog, {
            title: dialog.title,
            intro: dialog.intro,
            rows: dialog.rows,
            onClose: closeDialog,
            onError: function (text) { notify(text, "error"); },
            onDone: function (text, extra) { finish(text, extra); }
          })
        : null,

      dialog && dialog.type === "sync"
        ? h(SyncPreviewDialog, {
            folderTitle: dialog.folderTitle,
            playlistUri: dialog.playlistUri,
            items: dialog.items,
            plan: dialog.plan,
            onClose: closeDialog,
            onError: function (text) { notify(text, "error"); },
            onDone: function (text) { finish(text); }
          })
        : null,

      // Last, so a message is never hidden behind a dialog.
      h(Toast, { toast: toast, onClose: function () { setToast(null); } })
    );
  }

  /* Spicetify binds this module's default export as the route component, so
   * it has to be a component - not a function that mounts into the DOM.
   * Rendering into Spotify's own container fights its React tree and trips
   * the route error boundary.
   */
  var render = function () {
    return h(Panel);
  };
