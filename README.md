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
- **Selection across links** — tick songs in one link, switch to another, and
  keep going. One **Download to Spotify (n)** sends everything you ticked.
  **All songs** at the top of the library shows the whole collection, and
  Ctrl+Shift+A selects all of it.
- **Library folders** — press **Folder** to create one, then drag songs (or a
  whole link) onto it. Dragging a song out of its own entry moves it and
  removes that entry; dragging it out of a playlist files just that song.
- **Guided setup** — Help → Set up Music DLP installs FFmpeg for you and walks
  through the Spotify steps in plain language, with a "Check again" button.
- **Smart titles** — turns `DRAKE - AT THE GATES FT LIL UZI VERT (CDQ)` into
  the song *At The Gates* by *Drake, Lil Uzi Vert*. Applied automatically only
  when the app is confident; anything uncertain appears under
  **Suggested titles** for you to accept. It learns artist names from your own
  edits, so titles with no separator get easier over time.
- **AI naming (optional)** — press **Ask AI** to have a model name the songs
  the rules could not work out. Works with Ollama on your computer, Ollama
  cloud with an API key, or any compatible API. Nothing is applied without
  your review.
- **Chatbot export/import** — no key or model needed: export the songs that
  need names to a text file, paste it into any chatbot, then import the reply.
- **Real album art** — looks the song up in free music catalogues and embeds
  the actual cover instead of the video still. Falls back to the video image.
- **Dark theme** — the default, with a one-click switch to light.
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

**Easiest:** download the `.exe` from the
[latest release](https://github.com/seafeuer/Music-DLP/releases/latest) and run
it. Python and the app's libraries are inside it. Put `ffmpeg.exe` in the
`ffmpeg\` folder next to it, or install FFmpeg with
`winget install Gyan.FFmpeg`.

**From source:**

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

Ticks are kept per song, not per link: tick songs in one link, open another,
tick more, and the main button counts all of them. **All songs** at the top of
the library lists everything in one view.

Re-adding or refreshing a link updates the existing entry rather than creating
a second section. Right-click a link and choose **Move to folder…** to group
links under a heading in the library.

The three steps at the top always show where you are. To change a song's
details, highlight it and click **Edit details…** (or double-click the row).

### Smart titles

Titles are cleaned when a link is added:

| Upload title | Song | Artist |
|---|---|---|
| `DRAKE - AT THE GATES FT LIL UZI VERT (OG) (CDQ QUALITY)` | At The Gates | Drake, Lil Uzi Vert |
| `Yoru ni Kakeru` on a `- Topic` channel | Yoru ni Kakeru | YOASOBI |
| `Plastic Love (Official Music Video) [HD]` | Plastic Love | — |

Rules:

- Only applied when the app is confident (80% or better).
- Songs whose details you have edited are never changed.
- Anything uncertain is listed under **Suggested titles**, where you tick what
  to accept. Nothing is renamed until you do.
- Artist names you confirm are remembered, so later uploads with no separator
  (`... MONDAY MORNING YOUNG THUG`) can be split.
- Turn it off in Settings → Output → Smart titles.

### AI naming

Two ways, both optional and both review-first.

**With a model (Settings → AI).** Give it an address and a key, and it works
out the rest. **Detect automatically** is the default: the first time a
server is used, Music DLP tries the three common APIs and remembers which one
answers. Any of these work:

| Server | Address | Key |
|---|---|---|
| Ollama on this computer | `http://localhost:11434` | none |
| Ollama cloud | `https://ollama.com` | required |
| Any [OI]-compatible API | the address from its docs | required |
| Any Anthropic-style API | the address from its docs | required |

The provider list is only a hint for the first guess. Picking the wrong one
is not a problem — a proxy that serves only `/v1/messages` still works when
[OI]-compatible was chosen, and the other way round.

Addresses are forgiving: `https://api.example.com`, `…/v1`, or the whole
`…/v1/chat/completions` all work. Keys are sent as a bearer token, as
`x-api-key`, and as `api-key`, so most servers accept them without further
setup. For Ollama on this computer, install Ollama and pull a model first
(`ollama pull llama3.2`).

Press **Refresh list** to see the models the server offers, and **Test
connection** to check the key — it names the API it found. Then use
**AI → Name songs with AI…** or the **Ask AI** button in Suggested titles.
The model fills in the suggestion columns; you still press Apply.

Small local models name songs badly. A 0.5B model will usually return the
upload title unchanged; use something in the 7B class or larger, or a hosted
model, for useful suggestions.

**Your own instructions.** Settings → AI → **Extra AI instructions** adds
anything you type to the built-in prompt, for both naming and translating.
It is added rather than swapped in, so the answer keeps the shape Music DLP
needs to read it. For example:

```
Always put the Vocaloid singer in the artist field.
Keep (Original) only for Vocaloid songs that have many covers.
```

### Where the AI gets its title

The AI is given the **uploader's own title**, not the name the built-in
rules worked out. Those rules are a good guess but they are wrong often
enough to matter — a Japanese uploader writes "Title - Vocalist", and the
rules read that backwards, so `オーバーライド - 重音テトSV[吉田夜世]` became
`重音テトsv[吉田夜世]`. The library name is still shown to the model, marked
as a guess, so it can see what the app currently thinks without being led by
it. When the two disagree, the upload title wins.

### How brackets are handled

Text in brackets is read as a qualifier, not as part of the song name:

| Upload title | Becomes |
|---|---|
| `Senbonzakura (Original)` by Hatsune Miku | *Senbonzakura (Original)* — Hatsune Miku |
| `Plastic Love (Original)` by Mariya Takeuchi | *Plastic Love* — the label is dropped |
| `Stay (Cover by Jasmine Thompson)` | *Stay (Cover)* — Jasmine Thompson |
| `Lemon [Instrumental]` | *Lemon (Instrumental)* |
| `夜に駆ける (feat. 初音ミク)` | *夜に駆ける* — YOASOBI, 初音ミク |

- A performer named in brackets is an artist credit, including a Vocaloid or
  UTAU singer.
- `(Original)` is kept only for Vocaloid, UTAU, and similar songs with many
  covers, where it is what tells this recording apart. Everywhere else it is
  dropped.
- Cover, instrumental, remix, live, and acoustic markers are kept, because
  they name a different recording.
- Upload noise — "official video", a year, a channel name — is dropped.

**Without a model.** **AI → Export songs for a chatbot…** writes a text file
containing the songs that need names. Paste the whole file into any chatbot,
then use **AI → Import a chatbot's reply…** and paste the answer back. The
names land in Suggested titles for review.

AI never overwrites a song you have edited, and never applies a name by
itself.

### Translating titles

**AI → Translate ticked songs…** (Ctrl+T) or **Translate every song in this
link…** turns names into another language — Japanese, Hebrew, Korean, Chinese,
Spanish, French, and any other language you type. This is separate from
naming: it takes the name a song already has and gives you the same song in
the language you read.

Three ways to write it, because Japanese, Hebrew, and similar languages have
two useful forms:

| Choice | Result |
|---|---|
| In the language's own script | 夜に駆ける |
| In Latin letters | Yoru ni Kakeru |
| Both | 夜に駆ける (Yoru ni Kakeru) |

Artist names are translated too unless you untick the box. Songs already in
the target language are left alone. Everything is shown for review and can be
edited by hand; nothing is saved until Apply. The language and style are
remembered for next time. **Edit details → Translate title and artist…** does
the same for one song.

### Album details without AI

- **Edit → Look up details online…** asks MusicBrainz, a free public
  catalogue, for the album each song belongs to and its track number. No
  account, no model. Review the results, then Apply.
- **Edit → Number ticked songs as an album…** gives every ticked song one
  album name and consecutive track numbers, so Spotify shows them as one
  album instead of loose singles.
- **Edit → Update files for ticked songs** (Ctrl+Shift+R) rewrites every file
  whose details changed, and sends it to Spotify again if it was there.

### Finding and fixing

- **Search all songs** (Ctrl+F, or the box in the top bar) searches the whole
  library, whichever link is open. Clearing the box returns to the link you
  came from.
- **Edit → Find duplicate songs…** groups songs that look like the same
  recording added twice, keeps the copy that is furthest along, and lets you
  remove the rest.
- **Ctrl+Z** undoes the last rename, translation, move into a folder, folder
  creation, or removal that kept its files.
- **F2** edits the highlighted song.

### Cover art

With **Settings → AI → Look up real album art** on, converting a song looks it
up in the iTunes catalogue and MusicBrainz and embeds the real cover. If
nothing is found, the video thumbnail is used as before. **Edit details → Find
cover art** does the same lookup for one song.

Covers are stored at 1400 px square, from the largest artwork the catalogue
has (iTunes serves up to 3000 px; Deezer covers up to 1000 px). YouTube
answers a missing large thumbnail with a 120x90 grey square; that is rejected
and the next size down is used instead, so a missing still never becomes a
grey cover.

**The cover is only used when it is the same song.** A match has to have the
same title, with the artist as the *main* credit — not a feature — and must
not be a live, karaoke, cover, or remix version the upload does not claim to
be. Without that rule an unreleased track picks up a stranger's artwork:
"Monday Morning" by Young Thug was given the cover of a Juicy J song he is
only featured on. When no catalogue entry really is the song, the video's own
picture is used and enlarged to the cover size with a sharpening pass, which
is the honest choice for something that has no official cover.

Converted files go to `Music\MusicDLP\MP3` (and `M4A` if enabled). Files sent to
Spotify are copied to `Music\MusicDLP\Spotify` by default.

### Listening on a phone (Google Drive)

Google Drive's desktop app syncs a folder, and the Drive app on iOS and
Android shows what is in it. **Edit → Send ticked songs to Google Drive**
copies the converted files into that folder, and the Drive app uploads them.

- Music DLP finds the synced Drive automatically (`G:\My Drive`, or
  `Google Drive` in your user folder). **Settings → Spotify → Find it** shows
  which folder it chose.
- By default songs go into a `MusicDLP` subfolder, so the rest of the Drive is
  untouched.
- Sending twice is harmless: a file already there is skipped.
- Nothing is shared or made public. This is a file copy, not a Google account
  login, and needs no API key.

The first upload is done by the Drive app, so it needs a moment and a working
internet connection; the songs appear on the phone after it finishes.

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
  `{artist}`, `{album}`, `{track}`, `{id}`, `{media_id}`, `{source}`, and a
  number format such as `{track:02d}`.
- **Output → Normalize** — two-pass loudness normalization to −14 LUFS.
- **Output → Delete the original download after converting** — off by
  default. Keeping the download means a song can be converted again without
  downloading it a second time.
- **Spotify → When sending** — copy (keep the output file) or move.
- **Spotify → Google Drive** — where songs go for phone listening. Leave it
  empty to use the Drive folder found on this computer.
- **Advanced** — FFmpeg path, diagnostics, log folder, and **Update yt-dlp**.
  Updating is the first thing to try when a site stops working.

A folder can have its own file name pattern: right-click a folder in the
library → **File name pattern…**. Songs filed in that folder use it instead
of the global one, so an album can be numbered `{track:02d} - {title}` while
the rest of the library stays `{title}`.

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
              library, pipeline, legacy import, AI, translation,
              artwork, catalogue lookup
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
| A site suddenly stops working | Settings → Advanced → **Update yt-dlp** |
| AI says "not set up" | Settings → AI → pick a provider and model, then Test connection |
| Translation came out wrong | Untick that row and edit it by hand; the model is only a first draft |
| Song shows in Discord but with no artist or cover | Discord stopped reporting Spotify **local files** in its status; this is not something the app can change. See the note below. |

### Discord status and local files

Discord shows what Spotify is playing, but it only does so for songs in
Spotify's own catalogue. A song that only exists as a local file is left out
of the status, or shows with the title but no artist and no cover. This is a
change on Discord's side, not something in the tags Music DLP writes, and
nothing in the app can turn it back on.

The workarounds are outside the app:

- If the song is in Spotify's catalogue, play that version and Discord will
  show it.
- A separate rich-presence tool that reads Windows' media controls can report
  any player, local files included. [Music Presence](https://github.com/ungive/discord-music-presence)
  is one.

Logs live in `%LOCALAPPDATA%\MusicDLP\logs\musicdlp.log`; the app's **Logs**
drawer shows the same messages and can open the folder.

## License

For personal use.
