# Changelog

Earlier releases were not recorded here. This file starts with the audit
release.

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
