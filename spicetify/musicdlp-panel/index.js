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
    missing: "File missing"
  };

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
    var response = await fetch(BASE + path, {
      method: method,
      headers: {
        Authorization: "Bearer " + TOKEN,
        "Content-Type": "application/json"
      },
      body: body === undefined ? undefined : JSON.stringify(body)
    });
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
    if (status === "converted") return "converted";
    return "";
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

  async function syncFolder(folder) {
    var playlistUri = await ensurePlaylist(folder.title);
    var contents = await platform().PlaylistAPI.getContents(playlistUri);
    var items = (contents && contents.items) || [];
    var playlistUris = items.map(function (item) { return item.uri; });

    var localTracks = await platform().LocalFilesAPI.getTracks();
    var plan = await api("POST", "/api/sync/plan", {
      folder_id: folder.id,
      local_tracks: slimLocalTracks(localTracks),
      playlist_uris: playlistUris
    });

    if (plan.to_add.length) {
      await platform().PlaylistAPI.add(playlistUri, plan.to_add, {});
    }
    if (plan.to_remove.length) {
      var rows = items
        .filter(function (item) { return plan.to_remove.indexOf(item.uri) >= 0; })
        .map(function (item) { return { uid: item.uid || "", uri: item.uri }; });
      if (rows.length) await platform().PlaylistAPI.remove(playlistUri, rows);
    }

    return { playlistUri: playlistUri, plan: plan };
  }

  // ------------------------------------------------------------- components

  function Toast(props) {
    if (!props.toast) return null;
    return h("div", { className: "mdlp-toast " + (props.toast.kind === "error" ? "err" : "ok") },
      props.toast.text);
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
                h("span", { className: "mdlp-status " + statusClass(track.status) },
                  statusLabel(track.status)))
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
        if (updateFile && canUpdate) {
          await api("POST", "/api/jobs", {
            track_ids: [track.id],
            stages: track.status === "synced" ? ["convert", "sync"] : ["convert"],
            force: true
          });
        }
        props.onSaved(updateFile ? "Saved and updating the file." : "Saved.");
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
          await api("POST", "/api/metadata", {
            track_id: chosen[i].track_id,
            title: chosen[i].title,
            artist: chosen[i].artist
          });
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
    var album = albumState[0], setAlbum = albumState[1];
    var start = startState[0], setStart = startState[1];
    var busy = busyState[0], setBusy = busyState[1];

    async function apply() {
      if (!album.trim()) return;
      setBusy(true);
      try {
        for (var i = 0; i < props.tracks.length; i++) {
          await api("POST", "/api/metadata", {
            track_id: props.tracks[i].id,
            title: props.tracks[i].title,
            artist: props.tracks[i].artist,
            album: album.trim(),
            track_number: Number(start) + i
          });
        }
        props.onDone("Numbered " + props.tracks.length + " song(s).");
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
          await api("POST", "/api/metadata", {
            track_id: chosen[i].track_id,
            title: chosen[i].title,
            artist: chosen[i].artist,
            album: chosen[i].album,
            track_number: chosen[i].track_number
          });
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

    function notify(text, kind) {
      setToast({ text: text, kind: kind || "ok" });
    }

    async function refreshFolders() {
      var list = await api("GET", "/api/folders");
      setFolders(list);
      return list;
    }

    async function loadTracks(folderId) {
      var path = folderId ? "/api/tracks?folder=" + folderId : "/api/tracks";
      var list = await api("GET", path);
      setTracks(list);
      setSelected(new Set());
    }

    React.useEffect(function () {
      var alive = true;

      async function first() {
        try {
          var info = await api("GET", "/api/health");
          if (!alive) return;
          setHealth(info);
          setConnection("online");
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
            if (alive) { setHealth(info); setConnection("online"); setReason(""); }
          })
          .catch(function (err) {
            if (alive) {
              setConnection("offline");
              setReason(err && err.message ? err.message : "");
            }
          });
      }, 5000);

      return function () { alive = false; clearInterval(timer); };
    }, []);

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

    // Follow the desktop app's progress while a job runs.
    React.useEffect(function () {
      if (!busy) return;
      var timer = setInterval(function () {
        api("GET", "/api/status").then(function (status) {
          if (status.progress && Object.keys(status.progress).length) {
            setProgress(status.progress);
          }
          if (!status.pipeline_running) {
            loadTracks(activeFolder).catch(function () {});
            refreshFolders().catch(function () {});
            setBusy("");
          }
        }).catch(function () {});
      }, 1000);
      return function () { clearInterval(timer); };
    }, [busy, activeFolder]);

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
      setBusy("Starting " + stages.join(" → ") + "…");
      try {
        await api("POST", "/api/jobs", {
          track_ids: selectedTracks.map(function (t) { return t.id; }),
          stages: stages
        });
        notify("Working on " + selectedTracks.length + " song(s).");
      } catch (err) {
        notify(err.message, "error");
        setBusy("");
      }
    }

    async function doSync() {
      var folder = folders.filter(function (f) { return f.id === activeFolder; })[0];
      if (!folder) {
        notify("Choose a folder first.", "error");
        return;
      }
      setBusy("Syncing “" + folder.title + "” to Spotify…");
      try {
        var result = await syncFolder(folder);
        var summary = result.plan.summary;
        var lines = [
          "Playlist “" + folder.title + "” is up to date.",
          "Added " + summary.add + ", removed " + summary.remove + "."
        ];
        if (summary.unmatched) {
          lines.push(summary.unmatched + " song(s) are not in Spotify's Local Files yet — "
            + "download and convert them first, then sync again.");
        }
        notify(lines.join("\n"));
      } catch (err) {
        notify(err.message, "error");
      } finally {
        setBusy("");
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
        loadTracks(activeFolder).catch(function () {});
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
        var job = await api("POST", "/api/rewrite", {
          track_ids: selectedTracks.map(function (t) { return t.id; })
        });
        var done = await waitForJob(job);
        if (done.status === "error") throw new Error(done.error);
        notify("Files rewritten and the Spotify copies updated.");
      } catch (err) {
        notify(err.message, "error");
      } finally {
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
          "Bridge " + health.version + " · playlists go to “" + health.dlp_folder + "”") : null
      ),

      connection === "offline"
        ? h("div", { className: "mdlp-toast err" },
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

      h(Toast, { toast: toast }),

      dialog && dialog.type === "edit"
        ? h(EditDialog, {
            track: dialog.track,
            onClose: closeDialog,
            onInfo: function (text) { notify(text); },
            onError: function (text) { notify(text, "error"); },
            onSaved: function (text) { notify(text); closeDialog(); loadTracks(activeFolder); }
          })
        : null,

      dialog && dialog.type === "review"
        ? h(ReviewDialog, {
            title: dialog.title,
            intro: dialog.intro,
            rows: dialog.rows,
            onClose: closeDialog,
            onError: function (text) { notify(text, "error"); },
            onDone: function (text) { notify(text); closeDialog(); loadTracks(activeFolder); }
          })
        : null,

      dialog && dialog.type === "album"
        ? h(AlbumDialog, {
            tracks: selectedTracks,
            onClose: closeDialog,
            onError: function (text) { notify(text, "error"); },
            onDone: function (text) { notify(text); closeDialog(); loadTracks(activeFolder); }
          })
        : null,

      dialog && dialog.type === "lookup"
        ? h(LookupDialog, {
            title: dialog.title,
            intro: dialog.intro,
            rows: dialog.rows,
            onClose: closeDialog,
            onError: function (text) { notify(text, "error"); },
            onDone: function (text) { notify(text); closeDialog(); loadTracks(activeFolder); }
          })
        : null
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
