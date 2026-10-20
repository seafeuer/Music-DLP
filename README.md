# Music DLP

A Windows desktop app that downloads audio from **YouTube playlists and videos**,
**X/Twitter posts**, and other links supported by yt-dlp, converts it to tagged
MP3/M4A, and delivers it to **Spotify Local Files**.

Titles are kept exactly as published. You can edit title, artist, album, and
track number per song before converting.

## What it does

- **Three clear steps**, always visible at the top: Download → Convert → Send
  to Spotify. In Easy mode one button does all three; in Pro mode you can run
  them separately.
- **Any link** — YouTube playlists, YouTube videos (watch / youtu.be / shorts),
  X/Twitter posts, and other yt-dlp sites. Paste several at once or drag them in.
- **Real progress** — per-track bytes, speed, and ETA; cancellation that works.
- **Duplicate-aware** — the same video in two playlists is downloaded once.
- **Resume** — interrupted downloads and conversions continue where they stopped.
- **Edit any song** — title, artist, album, track number, and cover picture,
  with a live preview of the file name. "Update the audio file now" rewrites
  the file and refreshes the Spotify copy in one step.
- **Guided setup** — Help → Set up Music DLP installs FFmpeg for you and walks
  through the Spotify steps in plain language, with a "Check again" button.
- **Browser cookies** — use Chrome/Edge/Firefox cookies directly, or a
  `cookies.txt` file, for sign-in checks and protected posts.
- **Import old library** — brings sources, tracks, and output paths over from
  the previous Music-DLP database.

## Requirements

- Windows 10/11
- Python 3.10+ (for running from source)
- **FFmpeg** — needed for conversion and tagging
- Optional: Spotify Desktop, for Local Files delivery

## Install and run

```bash
git clone https://github.com/seafeuer/Music-DLP.git
cd Music-DLP

python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

python main.py
```

Install FFmpeg with `winget install Gyan.FFmpeg`, or place `ffmpeg.exe` next to
the app (or in a `ffmpeg\` subfolder). The first-run wizard can also locate it.

## Using it

1. Paste a link into the bar and press **Add** (or drop links onto the window).
2. Tick the songs you want. New songs are ticked automatically.
3. **Easy mode:** click **Download to Spotify (n)**.
   **Pro mode:** use **Download**, **Convert**, and **Spotify** separately.

The three steps at the top always show where you are. To change a song's
details, highlight it and click **Edit details…** (or double-click the row).

Converted files go to `Music\MusicDLP\MP3` (and `M4A` if enabled). Files sent to
Spotify are copied to `Music\MusicDLP\Spotify` by default.

### Spotify

Spotify has **no public API** for uploading local files or adding them to a
playlist. Music DLP does the part that can be automated:

1. Writes correctly tagged MP3s into a folder.
2. Optionally restarts Spotify so it rescans that folder.

The first time you open the app, **Set up Music DLP** shows four steps with a
green tick for each one. If Spotify still does not know your folder later, an
amber bar appears at the top of the window with a **Show me how** button.

One-time setup in Spotify:

1. Profile → Settings → **Show local files**.
2. **Add a source** → choose the Music DLP Spotify folder.
3. Restart Spotify (the app has a button for this).
4. Your tracks appear under **Your Library → Local Files**.

You add them to a playlist inside Spotify; no external app can do that.

## Modes

| | Easy | Pro |
|---|---|---|
| Primary action | One button: download → convert → Spotify | Three separate buttons |
| Details panel | Hidden | Metadata editing, paths, actions |
| Format column | Hidden | Visible |
| Retry | Automatic on next run | Per track |
| Log | Drawer on error | Drawer on error |

Switch modes from the toolbar at any time; the selection is preserved.

## Settings worth knowing

- **Download → Cookies** — browser cookies are the easiest fix for YouTube
  sign-in checks and protected X posts.
- **Output → Filename template** — `{title}` by default. Also supports
  `{artist}`, `{album}`, `{track}`, `{id}`, `{source}`.
- **Output → Normalize** — two-pass loudness normalization to −14 LUFS.
- **Spotify → When sending** — copy (keep the output file) or move.
- **Advanced** — FFmpeg path, diagnostics, log folder.

## Building a standalone app

```bash
pip install pyinstaller
python build.py --clean
```

Output: `dist/MusicDLP/`. Put `ffmpeg.exe` in `dist/MusicDLP/ffmpeg/`.

## Development

```bash
pip install -r requirements.txt pytest pytest-qt
python -m pytest
```

The test suite covers link parsing, filenames, the database, pipeline
orchestration (with fake services), Spotify copying, legacy import, and an
offscreen Qt smoke test.

```
musicdlp/
  core/       paths, settings, database, models, logging, errors
  services/   links, yt-dlp, FFmpeg, tagging, thumbnails, Spotify,
              library, pipeline, legacy import
  ui/         main window, table model, delegates, widgets, dialogs, theme
```

## Troubleshooting

| Problem | Fix |
|---|---|
| "FFmpeg not found" | Settings → Advanced → Detect, or `winget install Gyan.FFmpeg` |
| YouTube sign-in check | Settings → Download → Cookies → your browser |
| X post fails | It may be protected or contain no video; try browser cookies |
| Tracks not in Spotify | Spotify → Settings → Show local files → add the folder, then restart Spotify |
| Duplicate entries in Spotify | Send only one format (Settings → Spotify → MP3) |

Logs live in `%LOCALAPPDATA%\MusicDLP\logs\musicdlp.log`; the app's **Logs**
drawer shows the same messages and can open the folder.

## License

For personal use.
