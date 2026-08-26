# DeckClip

DeckClip is a barebones Decky Loader plugin that finds the three newest Steam Game Recording clips and exports any selection of them to normal MP4 files in:

`/home/deck/Videos/DeckClip/`

Each selected clip can be renamed before export. The UI reports overall and per-clip progress, then shows the destination folder. Existing files are never overwritten, and Steam's recording folders are only read.

## Prototype architecture

- `src/index.tsx` is the Decky quick-access UI. It lists clips, tracks selection/renames, starts a job, and polls lightweight status updates.
- `main.py` is the Decky Python backend. It searches common native and Flatpak Steam roots, resolves game names from local app manifests, and locates every nested `session.mpd` in each clip.
- DeckClip explicitly assembles every numbered Steam `.m4s` fragment for each video/audio stream, then FFmpeg remuxes those streams without re-encoding. This avoids FFmpeg stopping after the first three-second DASH fragment. If a clip spans multiple recording sessions, DeckClip concatenates the resulting session parts into one MP4.
- All intermediate and final writes stay under `/home/deck/Videos/DeckClip/`. The source clip paths are never opened for writing, renamed, or removed.

## Steam Deck test setup

### Prerequisites

1. Install Decky Loader and enable Developer Mode in Decky's settings.
2. Confirm FFmpeg is available in Desktop Mode by running `ffmpeg -version` in Konsole. SteamOS normally provides it; if the command is missing, install or bundle a Deck-compatible FFmpeg before testing exports.
3. Make at least one clip using Steam Game Recording.

### Build on another computer (recommended)

The current Decky template requires Node.js 16.14 or newer and pnpm 9.

```bash
pnpm install
pnpm run typecheck
pnpm run build
```

The build creates `dist/index.js`.

### Create the installable ZIP

Do not use GitHub's automatic **Source code** ZIP and do not ZIP the repository root directly. Decky requires a release archive containing one top-level `DeckClip/` directory.

```bash
pnpm run release
```

This builds, tests, and validates `release/DeckClip-0.1.1.zip`. Its relevant layout is:

```text
DeckClip/
├── dist/index.js
├── main.py
├── package.json
├── plugin.json
├── README.md
└── LICENSE
```

### Install through Decky

1. Copy `release/DeckClip-0.1.1.zip` to the Deck's Downloads folder. Do not extract it.
2. In Gaming Mode, open the Quick Access menu (`…`) and Decky Loader.
3. Open Decky settings and enable **Developer Mode** if needed.
4. Open the Developer section, choose **Install Plugin from Zip**, and select `DeckClip-0.1.1.zip` from Downloads.
5. Wait for Decky to finish installing, then reload Decky or restart Steam if DeckClip does not immediately appear.

Decky owns its installed plugin directory and makes it read-only; that is expected. Install updates by generating and selecting a newer ZIP rather than editing `/home/deck/homebrew/plugins/DeckClip/` directly.

### Test checklist

1. Confirm the newest three valid clip folders are shown in newest-first order.
2. Toggle one, several, or all clips. Add a filename to at least one selected clip.
3. Start export and watch both overall and per-clip progress.
4. Confirm the completion message points to `/home/deck/Videos/DeckClip/`.
5. Open each MP4 from Dolphin or a media player and check video, game audio, and any extra audio track you recorded.
6. Export the same names again and confirm DeckClip creates `name (2).mp4` rather than overwriting the first file.

Backend-only discovery tests can be run on any machine with Python 3.9+:

```bash
pnpm run test:backend
```

For safe fixture testing, set `DECKCLIP_STEAM_ROOT` to a fake Steam directory before loading the backend. This override is intended for development only.

## Known prototype limitations

- FFmpeg must be present on the Deck; a store-ready package should bundle a known-compatible binary.
- Game titles are resolved from `appmanifest_*.acf` across the primary library and every library listed in `libraryfolders.vdf`; clips for games that are no longer installed display as `Steam app <id>`.
- Session-copy and concat assume Steam kept compatible codecs/settings across a multi-session clip. A resolution or codec change may require a future fallback re-encode.
- Export jobs are in memory and do not survive a Decky restart.
