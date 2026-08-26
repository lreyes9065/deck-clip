# DeckClip

DeckClip is a barebones Decky Loader plugin that finds the three newest Steam Game Recording clips and exports any selection of them to normal MP4 files in:

`/home/deck/Videos/DeckClip/`

Each selected clip can be renamed before export. The UI reports overall and per-clip progress, then shows the destination folder. Existing files are never overwritten, and Steam's recording folders are only read.

## Prototype architecture

- `src/index.tsx` is the Decky quick-access UI. It lists clips, tracks selection/renames, starts a job, and polls lightweight status updates.
- `main.py` is the Decky Python backend. It searches common native and Flatpak Steam roots, resolves game names from local app manifests, and locates every nested `session.mpd` in each clip.
- FFmpeg remuxes the DASH audio/video streams with stream copy, so there is no quality loss or full re-encode. If a clip spans multiple recording sessions, DeckClip remuxes each session and concatenates the resulting parts into one MP4.
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

### Copy to the Deck

Copy the whole project folder, including `dist/index.js`, `main.py`, `plugin.json`, `package.json`, and `LICENSE`, into:

```text
/home/deck/homebrew/plugins/DeckClip/
```

For example, from a checkout already on the Deck:

```bash
mkdir -p /home/deck/homebrew/plugins/DeckClip
cp -a dist main.py plugin.json package.json LICENSE /home/deck/homebrew/plugins/DeckClip/
```

Restart Decky Loader from its developer settings (or reboot), open the `…` quick-access menu, choose Decky, then DeckClip.

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
- Game titles are resolved from the primary Steam library's `appmanifest_*.acf`; clips for games no longer installed there display as `Steam app <id>`.
- Session-copy and concat assume Steam kept compatible codecs/settings across a multi-session clip. A resolution or codec change may require a future fallback re-encode.
- Export jobs are in memory and do not survive a Decky restart.
