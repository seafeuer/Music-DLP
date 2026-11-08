# Changelog

Earlier releases were not recorded here. This file starts with the audit
release.

## 2.12.0 — 2026-11-08

The desktop workflow and delivery finish from §4 of the 2026-11-05 audit.
Panel work stays in the desktop where it makes sense; the panel follows the
desktop library live.

### Desktop

- **Bulk edit.** Edit → Edit details for ticked songs… sets one album, one
  artist, a run of track numbers, or a title find/replace (plain text or a
  regular expression) for the whole selection, with a preview and one-step
  undo. “Write the new details into the files now” rewrites tags and names
  without re-encoding the audio.
- **Browse by artist and album.** View → Browse by artist and album… groups
  the whole library, shows cover thumbnails, and ticks an artist, an album,
  or highlighted covers for the next job.
- **Saved filters.** View → Saved filters keeps the current status filter
  and search text under a name, and restores both with one click.
- **Spotify-aware editing.** Right-click a song for Open in Spotify and Copy
  Spotify link. A song that has not been sent opens a Spotify search instead
  of a local link that cannot resolve.
- **Sync a folder from the desktop.** Edit → Sync this folder to its Spotify
  playlist… asks the panel to show its normal preview, so removals still
  need confirmation inside Spotify. The desktop reports the result.
- **Diagnostics.** Settings → Advanced → Run diagnostics… checks FFmpeg,
  yt-dlp, cookies, both folders, Spotify's Local Files registration, the
  bridge, the installed panel, AI configuration and free space, then copies
  one report. The AI connection is tested on request.
- **Reset settings.** Puts every preference back to its default, keeps the
  bridge token so the Spotify panel keeps working, and never touches the
  library or files.

### Panel

- **Live library updates.** The bridge reports a library version; the panel
  refreshes folders and songs when the desktop changes them instead of
  waiting for its own timer.
- **Desktop-requested syncs** arrive as a request, use the same preview as
  the panel's Sync button, and acknowledge the outcome to the desktop.
- **Playlist writes retry** with a growing pause before a sync is reported
  as failed, and count how many chunks were sent.

### Reliability

- **File edits are transactional.** Retagging, cover replacement and
  rewrite-tags write into a temporary copy in the same folder, flush it, and
  only then replace the original. A failed write leaves the song as it was.
- **Spotify folder copies retry** briefly when Windows or a scanner holds a
  file, instead of failing the song for the whole batch.
- **The installer finds Spicetify** on PATH, in the usual install folders,
  and in unpacked releases, with no hard-coded path from one computer.
  `-SkipApply` registers the panel without closing Spotify.

### Delivery

- **Release pipeline.** `python release.py --version X.Y.Z` updates the
  version in every file and the changelog, runs both test suites, builds the
  app, smoke-starts the packaged exe on an isolated home, and writes
  `SHA256SUMS.txt`.
- **Performance budget.** A test seeds 1000 songs and pins load, search and
  model memory.

## 2.11.0 — 2026-11-08

Additions from §4 of the 2026-11-05 audit.

### Panel

- **Find duplicates** shows the same groups as the desktop, keeps the copy
  that is furthest along, and removes only the ticked copies. Only songs the
  duplicate grouping found can be removed, and their files stay on disk
  unless the delete box is ticked.
- **Reconnect files** points songs whose audio moved at a copy in a folder,
  matched by file name.
- **Ask a chatbot** copies the prompt for the songs that need names and reads
  the reply back into the review dialog, including the album.
- **Check files** reports two songs sharing one file and files changed
  outside Music DLP.
- **Folders** creates, renames and deletes library folders without leaving
  Spotify. Deleting a folder keeps its songs, as on the desktop.
- **Cancel** stops a pipeline job from the progress line.

### Desktop

- **An interrupted batch is offered again.** The running batch is recorded
  when the pipeline starts and cleared when it ends, so a crash, a power cut
  or a forced shutdown no longer loses the rest of the work.
- **Closing during a job can keep it running in the tray**, with Show and
  Quit in the tray menu. Quitting clears the resume marker.
- **Backups.** Settings → Advanced keeps dated copies of the library and
  settings: one a day, one before a schema upgrade, and one on demand.
  Restore is applied at the next start and keeps the previous library.
- **Edit → Check song files…** compares each output with the fingerprint
  Music DLP recorded when it wrote the file.
- **Edit → Retry everything that failed** runs every failed song again in
  one job, choosing the download, conversion or send each batch needs.

### Tests

- A bridge contract test reads the panel source, fails on an undeclared
  route, and validates every response the panel reads.
- Six screenshot baselines cover the main window at three sizes in both
  themes.

## 2.10.0 — 2026-11-05

Data safety and correctness fixes from the 2026-11-05 audit.

### Playlist sync

- **Sync playlist cannot wipe a playlist on incomplete evidence.** Removals
  are planned only when Spotify's Local Files list is complete, the folder
  contains songs, and at least one song matched. An unknown or deleted folder
  returns an error instead of an empty plan.
- The panel previews every sync (adds, removals, unmatched) and removals need
  an explicit confirmation. Adds and removals are chunked, and the playlist is
  read page by page.
- Two different songs with identical title/artist/length are named in the
  preview, because Spotify can only show one of them.
- The panel's folder list is refreshed while it is open, and the panel moves
  off a folder that was deleted on the desktop.

### Editing and delivery

- Metadata edits are partial updates: a panel dialog no longer clears album
  or track number it does not show.
- A stale Spotify copy is deleted only after its replacement was delivered.
- Applying a cover no longer cancels a pending metadata rewrite, and relinking
  restores every missing format.
- A failed Spotify copy now reads “Spotify copy needs sending” instead of
  “Error”, and is cleared by the next successful delivery.

### Desktop

- Bridge mutations are refused while the pipeline is running, with a clear
  message instead of a corrupted file or a lost flag.
- Search, the link box, initial focus, sorting, selection semantics, the log
  drawer, and the About colours behave as documented.
- A folder that cannot be opened now says so instead of looking like a dead
  button.
- **Settings → Spotify** has the panel switch and local port the README
  promised.
- Every labelled field in Settings, Edit details, and Translate is paired with
  its label for screen readers.

### Reliability

- Config files that cannot be parsed are kept as `config.json.bad-<timestamp>`
  instead of being overwritten with defaults.
- Finished bridge jobs are forgotten after an hour.
- Raw downloads follow the output folder when it is repaired, collisions are
  reported, and duplicate clustering no longer groups unrelated songs.

### Packaging and docs

- `README.md` and `INSTALL.txt` corrected against the actual UI, with a
  **Known limitations** section.
- `dist/` rebuilt for 2.10.0.

## 2.9.0 and earlier

Not recorded.
