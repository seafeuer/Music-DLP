# Music DLP

A Windows desktop app that downloads audio from **YouTube playlists/videos**, **X/Twitter posts**, and other sites supported by yt-dlp, then converts it to Spotify-compatible MP3 and archival M4A files with embedded album art.

Titles are kept exactly as they appear on the source site. There is no AI metadata cleaning or renaming step.

## Features

- **Multiple link types** - YouTube playlists, YouTube videos, X/Twitter posts, and other yt-dlp-supported links
- **Paste several links at once** - one per line
- **Single links and playlists** - each link appears in the library; single items open as a one-song collection
- **Original titles** - files are named from the source title; the channel/uploader is embedded as the artist tag
- **Dual output** - MP3 (320kbps, for Spotify) + M4A (320kbps AAC, for archival)
- **Album art** - embeds 300x300 thumbnails into every file
- **Resume support** - interrupted downloads and conversions pick up where they left off
- **Bot-avoidance** - cookie support and randomized delays between downloads
- **Modern GUI** - CustomTkinter with light/dark mode

## Requirements

- Python 3.10+
- **FFmpeg** installed and in PATH ([download](https://ffmpeg.org/download.html)), or placed next to the app / in a `ffmpeg` subfolder
- Optional: **browser cookies** exported to a `cookies.txt` file (needed for some age-restricted or protected YouTube/X posts)

## Quick Start (From source)

```bash
git clone https://github.com/seafeuer/Music-DLP.git
cd Music-DLP

pip install -r requirements.txt

python main.py
```

## How It Works

```
1. Paste link(s)  - YouTube playlist/video, X/Twitter post, or another yt-dlp link
2. Extract        - Happens automatically when you press Add
3. Download       - Select songs, then click "1. Download"
4. Convert        - Click "2. Convert" to produce MP3 + M4A
```

Output goes to `downloads/final/mp3/` and `downloads/final/m4a/`.

### X/Twitter notes

- X/Twitter serves video, not a separate audio stream. Music DLP downloads the video and FFmpeg extracts the audio.
- Posts with several videos are stored as a multi-song collection.
- Protected or age-restricted posts may need a cookies file (Settings > Download > Cookies File).

## Importing to Spotify

1. Open the Spotify desktop app > Settings > Local Files
2. Enable "Show Local Files"
3. Add the `downloads/final/mp3/` folder as a source
4. Your songs appear under Local Files with album art

**Note:** Use MP3 files for Spotify. Spotify doesn't display album art on M4A local files.

## Building the exe

```bash
pip install pyinstaller
python build_exe.py --clean
```

Output: `dist/MusicDLP/`

## Tech Stack

| Component | Technology |
|-----------|-----------|
| GUI | CustomTkinter |
| Database | SQLite |
| Downloader | yt-dlp |
| Audio conversion | FFmpeg |
| Metadata | mutagen |
| Thumbnails | Pillow |
| Packaging | PyInstaller |

## License

For personal use.
