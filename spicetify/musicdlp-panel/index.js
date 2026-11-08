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
  function reviewMetadataBody(row, includeAlbum) {
    var body = {
      track_id: row.track_id,
      title: row.title,
      artist: row.artist
    };
    // A chatbot reply also carries an album, but the translate and AI
    // reviews do not. An empty album is left out: sending "" would erase
    // one that is already known.
    if (includeAlbum && typeof row.album === "string" && row.album.trim()) {
      body.album = row.album.trim();
    }
    return body;
  }

  /* The copies the duplicate dialog ticks by default: every copy except
   * the one the bridge says to keep. */
  function duplicateRemovalIds(groups) {
    var ids = [];
    (groups || []).forEach(function (group) {
      var keep = group.keep;
      (group.tracks || []).forEach(function (track) {
        if (track.id !== keep) ids.push(track.id);
      });
    });
    return ids;
  }

  function duplicateSummary(groups) {
    var list = groups || [];
    if (!list.length) return "No duplicate songs found.";
    var copies = 0;
    list.forEach(function (group) {
      copies += Math.max(0, (group.tracks || []).length - 1);
    });
    return copies + " duplicate cop" + (copies === 1 ? "y" : "ies")
      + " in " + list.length + " group" + (list.length === 1 ? "" : "s") + ".";
  }

  /* The rows the review dialog shows after a chatbot reply is read.
   * Songs the library no longer has stay visible, unchecked, with the
   * reason on their row instead of failing the whole reply. */
  function chatbotRows(results) {
    return (results || []).map(function (item) {
      return {
        track_id: item.track_id,
        original_title: item.original_title || "",
        title: item.title || "",
        artist: item.artist || "",
        album: item.album || "",
        checked: !!item.checked,
        unsure: (item.confidence || 0) < 0.6,
        error: item.error || ""
      };
    });
  }

  /* What a file check found, in one sentence. */
  function integritySummary(report) {
    var checked = (report && report.checked) || 0;
    var shared = ((report && report.shared) || []).length;
    var changed = ((report && report.changed) || []).length;
    if (!shared && !changed) {
      return "Every checked file matches what Music DLP wrote ("
        + checked + " file" + (checked === 1 ? "" : "s") + ").";
    }
    var parts = [];
    if (shared) {
      parts.push(shared + " group" + (shared === 1 ? "" : "s")
        + " of songs share one file");
    }
    if (changed) {
      parts.push(changed + " file" + (changed === 1 ? "" : "s")
        + " changed outside Music DLP");
    }
    return parts.join("; ") + ".";
  }

  function integrityNames(report) {
    var lines = [];
    ((report && report.shared) || []).forEach(function (group) {
      var titles = (group.tracks || []).map(function (track) {
        return track.title;
      });
      lines.push("Sharing one file: " + titles.join(" · "));
    });
    ((report && report.changed) || []).forEach(function (item) {
      lines.push("Changed: " + item.title);
    });
    return lines;
  }

  /* Why a folder name cannot be used, or "" when it can.
   Mirrors the desktop: names ignore case, and a rename may keep its own
   name. */
  function folderNameError(name, folders, exceptId) {
    var title = (name || "").trim();
    if (!title) return "A folder needs a name.";
    var clash = (folders || []).filter(function (folder) {
      return folder.id !== exceptId
        && String(folder.title).toLowerCase() === title.toLowerCase();
    })[0];
    if (clash) return "A folder called “" + clash.title + "” already exists.";
    return "";
  }

  function copyText(text) {
    if (typeof navigator !== "undefined" && navigator.clipboard
        && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    // Older Spotify renderers have no clipboard API. A hidden textarea and
    // execCommand still work there.
    try {
      var area = document.createElement("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      var copied = document.execCommand("copy");
      document.body.removeChild(area);
      if (copied) return Promise.resolve();
    } catch (err) {
      // fall through to the explanation below
    }
    return Promise.reject(new Error(
      "This Spotify build does not allow copying. Select the text and copy "
      + "it by hand."
    ));
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
          await api("POST", "/api/metadata",
            reviewMetadataBody(chosen[i], props.includeAlbum));
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

  /* The same duplicate groups the desktop dialog shows. The copy the
   * bridge marks to keep is not ticked; the rest are. Removal needs a
   * second, explicit press, and the audio files stay unless asked for. */
  function DuplicatesDialog(props) {
    var React = React_();
    var groups = props.groups || [];
    var selectedState = React.useState(new Set(duplicateRemovalIds(groups)));
    var deleteState = React.useState(false);
    var busyState = React.useState(false);
    var selected = selectedState[0], setSelected = selectedState[1];
    var deleteFiles = deleteState[0], setDeleteFiles = deleteState[1];
    var busy = busyState[0], setBusy = busyState[1];

    function toggle(id) {
      var next = new Set(selected);
      if (next.has(id)) next.delete(id); else next.add(id);
      setSelected(next);
    }

    async function remove() {
      var ids = Array.from(selected);
      if (!ids.length) return;
      setBusy(true);
      try {
        var result = await api("POST", "/api/duplicates/remove", {
          track_ids: ids,
          delete_files: deleteFiles
        });
        props.onDone(
          "Removed " + result.removed + " duplicate cop"
          + (result.removed === 1 ? "y" : "ies") + "."
          + (result.files_deleted ? " Their files were deleted." : "")
        );
      } catch (err) {
        props.onError(err.message);
      } finally {
        setBusy(false);
      }
    }

    return h(Modal, { title: "Duplicate songs", onClose: props.onClose },
      h("p", { className: "mdlp-hint" },
        duplicateSummary(groups)
        + " The best copy is kept by default; tick any other copy to remove. "
        + "Nothing is removed until you press Remove."),
      h("table", { className: "mdlp-review-table" },
        h("thead", null,
          h("tr", null,
            h("th", { style: { width: "30px" } }),
            h("th", null, "Song"),
            h("th", null, "Status"),
            h("th", null, "Album"))),
        h("tbody", null,
          groups.reduce(function (all, group, groupIndex) {
            var rows = all.concat([
              h("tr", { key: "g" + groupIndex },
                h("td", { colSpan: 4 },
                  h("strong", null,
                    (group.tracks[0] && group.tracks[0].title) || "Song"),
                  " — " + group.tracks.length + " copies"))
            ]);
            (group.tracks || []).forEach(function (track) {
              var kept = track.id === group.keep;
              if (kept) return; // shown as the group heading's kept copy
              rows.push(h("tr", { key: track.id },
                h("td", null,
                  h("input", {
                    type: "checkbox",
                    checked: selected.has(track.id),
                    onChange: function () { toggle(track.id); }
                  })),
                h("td", null,
                  h("div", null, track.title),
                  h("div", { className: "mdlp-unsure" },
                    (track.artist || "—")
                    + (track.album ? " · " + track.album : ""))),
                h("td", null,
                  h("span", { className: "mdlp-status " + statusClass(trackStatus(track)) },
                    statusLabel(trackStatus(track)))),
                h("td", null, track.album || "—")));
            });
            var best = (group.tracks || []).filter(function (track) {
              return track.id === group.keep;
            })[0];
            if (best) {
              rows.push(h("tr", { key: "keep" + groupIndex },
                h("td", null, "★"),
                h("td", null,
                  h("div", null, best.title),
                  h("div", { className: "mdlp-hint" },
                    "Kept — the copy that is furthest along")),
                h("td", null,
                  h("span", { className: "mdlp-status " + statusClass(trackStatus(best)) },
                    statusLabel(trackStatus(best)))),
                h("td", null, best.album || "—")));
            }
            return rows;
          }, []))),
      h("label", { className: "mdlp-check" },
        h("input", { type: "checkbox", checked: deleteFiles,
          onChange: function (e) { setDeleteFiles(e.target.checked); } }),
        "Also delete their audio files (this cannot be undone)"),
      h("div", { className: "mdlp-actions" },
        h("button", { className: "mdlp-btn", onClick: props.onClose }, "Cancel"),
        h("button", {
          className: "mdlp-btn danger",
          disabled: busy || !selected.size,
          onClick: remove
        }, busy ? "Removing…" : "Remove " + selected.size + " cop"
          + (selected.size === 1 ? "y" : "ies")))
    );
  }

  /* Point songs whose file is missing at a copy in a folder. The panel has
   * no folder picker, so the path is typed; the bridge checks it exists. */
  function ReconnectDialog(props) {
    var React = React_();
    var folderState = React.useState(props.startFolder || "");
    var busyState = React.useState(false);
    var folder = folderState[0], setFolder = folderState[1];
    var busy = busyState[0], setBusy = busyState[1];

    async function reconnect() {
      if (!folder.trim()) return;
      setBusy(true);
      try {
        var result = await api("POST", "/api/reconnect", {
          folder: folder.trim()
        });
        var relinked = result.relinked || 0;
        var missing = result.missing || 0;
        var message;
        if (relinked) {
          message = "Reconnected " + relinked + " song"
            + (relinked === 1 ? "" : "s") + " to the files in that folder.";
          if (missing) message += " " + missing + " still have no file.";
        } else {
          message = "No matching files were found in that folder.";
        }
        props.onDone(message);
      } catch (err) {
        props.onError(err.message);
      } finally {
        setBusy(false);
      }
    }

    return h(Modal, { title: "Reconnect missing files", onClose: props.onClose },
      h("p", { className: "mdlp-hint" },
        "Songs whose audio was moved to another folder keep a path that no "
        + "longer exists. Choose the folder the files are in now and Music "
        + "DLP will match them by file name. Nothing is downloaded."),
      h("div", { className: "mdlp-field" },
        h("label", null, "Folder with the songs"),
        h("input", { className: "mdlp-input", value: folder,
          placeholder: "C:\\Users\\you\\MusicDLP",
          onChange: function (e) { setFolder(e.target.value); },
          onKeyDown: function (e) { if (e.key === "Enter") reconnect(); } })),
      h("div", { className: "mdlp-actions" },
        h("button", { className: "mdlp-btn", onClick: props.onClose }, "Cancel"),
        h("button", { className: "mdlp-btn primary", disabled: busy || !folder.trim(),
          onClick: reconnect }, busy ? "Reconnecting…" : "Reconnect"))
    );
  }

  /* The no-setup path to AI naming: copy a prompt into any chatbot, paste
   * the answer back. Nothing is saved until the review dialog is applied. */
  function ChatbotDialog(props) {
    var React = React_();
    var replyState = React.useState("");
    var busyState = React.useState(false);
    var reply = replyState[0], setReply = replyState[1];
    var busy = busyState[0], setBusy = busyState[1];

    async function copyPrompt() {
      try {
        await copyText(props.prompt);
        props.onInfo("Copied. Paste it into any chatbot, then paste its reply below.");
      } catch (err) {
        props.onError(err.message);
      }
    }

    async function readReply() {
      if (!reply.trim()) return;
      setBusy(true);
      try {
        var result = await api("POST", "/api/import/names", {
          reply: reply
        });
        props.onImported(result.results || []);
      } catch (err) {
        props.onError(err.message);
      } finally {
        setBusy(false);
      }
    }

    return h(Modal, { title: "Ask a chatbot for names", onClose: props.onClose },
      props.count
        ? h("p", { className: "mdlp-hint" },
            props.count + " song" + (props.count === 1 ? " needs" : "s need")
            + " a name. Copy the list, ask any chatbot, then paste its reply "
            + "below. Nothing is saved until you review it.")
        : h("p", { className: "mdlp-hint" },
            "Every song already has a confident name. You can still paste a "
            + "chatbot reply below."),
      props.count ? h("textarea", {
          className: "mdlp-input mdlp-prompt",
          readOnly: true,
          rows: 8,
          value: props.prompt,
          "aria-label": "The list to give the chatbot"
        }) : null,
      props.count
        ? h("div", { className: "mdlp-row" },
            h("button", { className: "mdlp-btn", onClick: copyPrompt },
              "Copy the list"))
        : null,
      h("div", { className: "mdlp-field" },
        h("label", null, "The chatbot's reply"),
        h("textarea", { className: "mdlp-input mdlp-prompt", rows: 8,
          value: reply, placeholder: "Paste the reply here…",
          onChange: function (e) { setReply(e.target.value); } })),
      h("div", { className: "mdlp-actions" },
        h("button", { className: "mdlp-btn", onClick: props.onClose }, "Cancel"),
        h("button", { className: "mdlp-btn primary", disabled: busy || !reply.trim(),
          onClick: readReply }, busy ? "Reading…" : "Read the reply"))
    );
  }

  /* The result of the file check. Read-only: the fix is an explicit
   "Update files" or a look in the desktop app, never a quiet rewrite. */
  function IntegrityDialog(props) {
    var report = props.report || {};
    var shared = report.shared || [];
    var changed = report.changed || [];
    var lines = integrityNames(report);

    return h(Modal, { title: "Check song files", onClose: props.onClose },
      h("p", { className: "mdlp-hint" }, integritySummary(report)),
      lines.length
        ? h("ul", { className: "mdlp-preview" },
            lines.map(function (line, index) {
              return h("li", { key: index }, line);
            }))
        : null,
      shared.length
        ? h("p", { className: "mdlp-unsure" },
            "Two songs that point at one file usually have the same name, "
            + "album and track number. Give one of them different details, "
            + "then press Update files.")
        : null,
      changed.length
        ? h("p", { className: "mdlp-unsure" },
            "A changed file was edited or replaced outside Music DLP. Press "
            + "Update files to write the song's details into it again.")
        : null,
      h("div", { className: "mdlp-actions" },
        h("button", { className: "mdlp-btn primary", onClick: props.onClose },
          "Close"))
    );
  }

  /* Create, rename and delete library folders without leaving Spotify.
   Deleting keeps the songs: only the folder and its filing are removed,
   the same as the desktop. */
  function FolderDialog(props) {
    var React = React_();
    var folders = props.folders || [];
    var nameState = React.useState("");
    var busyState = React.useState("");
    var editState = React.useState(null);      // { id, text }
    var confirmState = React.useState(null);   // id
    var name = nameState[0], setName = nameState[1];
    var busy = busyState[0], setBusy = busyState[1];
    var editing = editState[0], setEditing = editState[1];
    var confirming = confirmState[0], setConfirming = confirmState[1];

    async function create() {
      var problem = folderNameError(name, folders);
      if (problem) return props.onError(problem);
      setBusy("create");
      try {
        var result = await api("POST", "/api/folders", { title: name.trim() });
        setName("");
        props.onInfo(result.created
          ? "Created “" + result.folder.title + "”."
          : "“" + result.folder.title + "” already exists.");
        props.onChanged();
      } catch (err) {
        props.onError(err.message);
      } finally {
        setBusy("");
      }
    }

    async function rename() {
      if (!editing) return;
      var problem = folderNameError(editing.text, folders, editing.id);
      if (problem) return props.onError(problem);
      setBusy("rename");
      try {
        var result = await api("POST", "/api/folders/rename", {
          folder_id: editing.id,
          title: editing.text.trim()
        });
        setEditing(null);
        props.onInfo("Renamed the folder to “" + result.folder.title + "”.");
        props.onChanged();
      } catch (err) {
        props.onError(err.message);
      } finally {
        setBusy("");
      }
    }

    async function remove(id, title) {
      setBusy("delete");
      try {
        await api("POST", "/api/folders/delete", { folder_id: id });
        setConfirming(null);
        props.onInfo("Deleted “" + title + "”. Its songs are still in the library.");
        props.onChanged();
      } catch (err) {
        props.onError(err.message);
      } finally {
        setBusy("");
      }
    }

    return h(Modal, { title: "Folders", onClose: props.onClose },
      h("p", { className: "mdlp-hint" },
        "Folders group links in the Music DLP library. Deleting a folder "
        + "keeps its songs."),
      h("div", { className: "mdlp-row", style: { marginBottom: "14px" } },
        h("input", {
          className: "mdlp-input",
          placeholder: "Name for a new folder",
          value: name,
          onChange: function (e) { setName(e.target.value); },
          onKeyDown: function (e) { if (e.key === "Enter") create(); }
        }),
        h("button", {
          className: "mdlp-btn primary",
          disabled: !!busy || !name.trim(),
          onClick: create
        }, busy === "create" ? "Creating…" : "Create")),
      h("table", { className: "mdlp-review-table" },
        h("thead", null,
          h("tr", null,
            h("th", null, "Folder"),
            h("th", { style: { width: "70px" } }, "Songs"),
            h("th", { style: { width: "150px" } }, ""))),
        h("tbody", null,
          folders.map(function (folder) {
            if (editing && editing.id === folder.id) {
              return h("tr", { key: folder.id },
                h("td", null,
                  h("input", {
                    className: "mdlp-input",
                    value: editing.text,
                    onChange: function (e) {
                      setEditing({ id: folder.id, text: e.target.value });
                    },
                    onKeyDown: function (e) {
                      if (e.key === "Enter") rename();
                    }
                  })),
                h("td", null, folder.tracks),
                h("td", null,
                  h("button", {
                    className: "mdlp-btn primary",
                    disabled: !!busy,
                    onClick: rename
                  }, "Save"),
                  " ",
                  h("button", {
                    className: "mdlp-btn",
                    onClick: function () { setEditing(null); }
                  }, "Cancel")));
            }
            if (confirming === folder.id) {
              return h("tr", { key: folder.id },
                h("td", { colSpan: 2 },
                  "Delete “" + folder.title + "”? Its songs stay in the library."),
                h("td", null,
                  h("button", {
                    className: "mdlp-btn danger",
                    disabled: !!busy,
                    onClick: function () { remove(folder.id, folder.title); }
                  }, "Delete"),
                  " ",
                  h("button", {
                    className: "mdlp-btn",
                    onClick: function () { setConfirming(null); }
                  }, "Cancel")));
            }
            return h("tr", { key: folder.id },
              h("td", null, folder.title),
              h("td", null, folder.tracks),
              h("td", null,
                h("button", {
                  className: "mdlp-btn",
                  disabled: !!busy,
                  onClick: function () {
                    setConfirming(null);
                    setEditing({ id: folder.id, text: folder.title });
                  }
                }, "Rename"),
                " ",
                h("button", {
                  className: "mdlp-btn",
                  disabled: !!busy,
                  onClick: function () {
                    setEditing(null);
                    setConfirming(folder.id);
                  }
                }, "Delete")));
          }))),
      h("div", { className: "mdlp-actions" },
        h("button", { className: "mdlp-btn primary", onClick: props.onClose },
          "Close"))
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
      // Keep the route itself a literal: the contract test reads these.
      var path = "/api/tracks" + (folderId ? "?folder=" + folderId : "");
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

    async function cancelJob() {
      try {
        await api("POST", "/api/cancel", {});
        notify("Cancelling… this stops after the current song.");
      } catch (err) {
        notify(err.message, "error");
      }
    }

    function manageFolders() {
      setDialog({ type: "folders" });
    }

    async function checkFiles() {
      setBusy("Checking the song files…");
      try {
        var report = await api("GET", "/api/integrity");
        setDialog({ type: "integrity", report: report });
      } catch (err) {
        notify(err.message, "error");
      } finally {
        setBusy("");
      }
    }

    async function findDuplicates() {
      setBusy("Looking for duplicate songs…");
      try {
        var data = await api("GET", "/api/duplicates");
        var groups = (data && data.groups) || [];
        if (!groups.length) {
          notify("No duplicate songs found — every name appears once.");
          return;
        }
        setDialog({ type: "duplicates", groups: groups });
      } catch (err) {
        notify(err.message, "error");
      } finally {
        setBusy("");
      }
    }

    function reconnectFiles() {
      setDialog({
        type: "reconnect",
        startFolder: (health && health.output_dir) || ""
      });
    }

    async function exportForChatbot() {
      setBusy("Preparing the list…");
      try {
        var data = await api("GET", "/api/export/songs");
        setDialog({
          type: "chatbot",
          prompt: data.prompt || "",
          count: data.count || 0
        });
      } catch (err) {
        notify(err.message, "error");
      } finally {
        setBusy("");
      }
    }

    function importedFromChatbot(results) {
      var rows = chatbotRows(results);
      if (!rows.length) {
        notify("No usable song names were found in that reply.", "error");
        return;
      }
      setDialog({
        type: "review",
        title: "Names from your chatbot",
        rows: rows,
        includeAlbum: true,
        intro: "Review the names before applying. Songs the library no "
          + "longer has are unchecked."
      });
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
        h("button", { className: "mdlp-btn", disabled: !!busy, onClick: manageFolders },
          "Folders"),
        h("button", { className: "mdlp-btn", disabled: !!busy, onClick: findDuplicates },
          "Find duplicates"),
        h("button", { className: "mdlp-btn", disabled: !!busy, onClick: checkFiles },
          "Check files"),
        h("button", { className: "mdlp-btn", disabled: !!busy, onClick: reconnectFiles },
          "Reconnect files"),
        h("button", { className: "mdlp-btn", disabled: !!busy, onClick: exportForChatbot },
          "Ask a chatbot"),
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
        h("div", { className: "mdlp-progress-row" },
          h("span", null,
            busy + (progress && progress.label ? " · " + progress.label : "")),
          // Only a pipeline job can be stopped from here; a bridge job is
          // the request the panel is already waiting on.
          watching
            ? h("button", { className: "mdlp-btn", onClick: cancelJob },
                "Cancel")
            : null),
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
            includeAlbum: !!dialog.includeAlbum,
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

      dialog && dialog.type === "duplicates"
        ? h(DuplicatesDialog, {
            groups: dialog.groups,
            onClose: closeDialog,
            onError: function (text) { notify(text, "error"); },
            onDone: function (text) { finish(text); }
          })
        : null,

      dialog && dialog.type === "folders"
        ? h(FolderDialog, {
            folders: folders,
            onClose: closeDialog,
            onInfo: function (text) { notify(text); },
            onError: function (text) { notify(text, "error"); },
            onChanged: function () {
              refreshFolders().then(adoptFolders).catch(function () {});
            }
          })
        : null,

      dialog && dialog.type === "integrity"
        ? h(IntegrityDialog, {
            report: dialog.report,
            onClose: closeDialog
          })
        : null,

      dialog && dialog.type === "reconnect"
        ? h(ReconnectDialog, {
            startFolder: dialog.startFolder,
            onClose: closeDialog,
            onError: function (text) { notify(text, "error"); },
            onDone: function (text) { finish(text); }
          })
        : null,

      dialog && dialog.type === "chatbot"
        ? h(ChatbotDialog, {
            prompt: dialog.prompt,
            count: dialog.count,
            onClose: closeDialog,
            onInfo: function (text) { notify(text); },
            onError: function (text) { notify(text, "error"); },
            onImported: importedFromChatbot
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
