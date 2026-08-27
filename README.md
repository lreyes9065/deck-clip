# DeckClip

DeckClip is a barebones Decky Loader plugin with a searchable game and clip browser that exports any selection of Steam Game Recording clips to normal MP4 files in:

`/home/deck/Videos/DeckClip/`

Each selected clip can be renamed before export. The UI reports overall and per-clip progress, then shows the destination folder. Existing files are never overwritten, and Steam's recording folders are only read.

DeckClip runs without root privileges. Its export manager lists only direct MP4 files in the dedicated output folder and provides a confirmed **Move to Trash** action; it never manages Steam's source recordings.

An exported clip can also be shared directly to a phone. DeckClip starts a temporary local-only web server, displays a QR code, and stops sharing after ten minutes or when the user presses **Stop sharing**. After a complete download it keeps a 30-second retry window, then closes automatically. No LocalSend plugin, cloud account, or internet upload is required.

## Prototype architecture

- `src/index.tsx` is the small Decky entry point and screen coordinator. `src/pages/` contains the library, clip browser, and export manager screens; `src/components/` contains reusable UI; `src/hooks/` owns export and transfer polling; and typed backend calls, models, persistence, and formatting live under `src/api/`, `src/types/`, and `src/utils/`.
- `main.py` is Decky's compatibility entry point and coordinates export jobs. Self-contained services live under `backend/`: `library.py` performs read-only Steam discovery and name resolution, `exports.py` validates output-file access, `media.py` assembles fragmented streams, `qr.py` generates transfer QR matrices, and `transfer.py` owns the temporary token-protected LAN server and its lifecycle.
- DeckClip explicitly assembles every numbered Steam `.m4s` fragment for each video/audio stream, then FFmpeg remuxes those streams without re-encoding. This avoids FFmpeg stopping after the first three-second DASH fragment. If a clip spans multiple recording sessions, DeckClip concatenates the resulting session parts into one MP4.
- All intermediate and final writes stay under `/home/deck/Videos/DeckClip/`. The source clip paths are never opened for writing, renamed, or removed.
- Phone transfer serves one selected export at a time over the Deck's current LAN address. A cryptographically random URL protects the file, the server accepts only that exact URL, and it automatically expires after ten minutes.

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

This builds, tests, and validates `release/DeckClip-0.8.0.zip`. Its relevant layout is:

```text
DeckClip/
├── backend/
│   ├── __init__.py
│   ├── exports.py
│   ├── library.py
│   ├── media.py
│   ├── qr.py
│   └── transfer.py
├── dist/index.js
├── main.py
├── package.json
├── plugin.json
├── README.md
├── LICENSE
└── THIRD_PARTY_NOTICES.md
```

### Install through Decky

1. Copy `release/DeckClip-0.8.0.zip` to the Deck's Downloads folder. Do not extract it.
2. In Gaming Mode, open the Quick Access menu (`…`) and Decky Loader.
3. Open Decky settings and enable **Developer Mode** if needed.
4. Open the Developer section, choose **Install Plugin from Zip**, and select `DeckClip-0.8.0.zip` from Downloads.
5. Wait for Decky to finish installing, then reload Decky or restart Steam if DeckClip does not immediately appear.

Decky owns its installed plugin directory and makes it read-only; that is expected. Install updates by generating and selecting a newer ZIP rather than editing `/home/deck/homebrew/plugins/DeckClip/` directly.

#### Upgrading from v0.3.1 or earlier

Earlier prototypes ran their backend as root and may have created a root-owned output folder. Before using v0.4.0, switch to Desktop Mode and run this one-time ownership repair in Konsole:

```bash
sudo chown -R deck:deck /home/deck/Videos/DeckClip
```

The command is intentionally limited to DeckClip's output folder. New exports are created as the normal `deck` user.

### Test checklist

1. Confirm the three games with the newest recordings appear on the first page. Select another game from **Choose from all games**, confirm it replaces the recent three and appears as the selected value, then clear the filter.
2. Open a game, All Clips, and Unknown / Unmatched from the game selector; confirm clips are newest-first, initially unchecked, and loaded 25 at a time.
3. Filter clips by game name, localized date/time, or displayed duration.
4. Toggle one, several, or all desired clips. Add a filename to at least one selected clip.
5. Start export and watch both overall and per-clip progress.
6. Confirm the completion message points to `/home/deck/Videos/DeckClip/`.
7. Open each MP4 from Dolphin or a media player and check video, game audio, and any extra audio track you recorded.
8. Export the same names again and confirm DeckClip creates `name (2).mp4` rather than overwriting the first file.
9. Open **Manage exported clips**, move one MP4 to Trash, and confirm Steam's original clip remains available.
10. For another exported clip, choose **Send to phone**. Put the Deck and iPhone on the same trusted Wi-Fi network, scan the QR code, and use **Download clip** to save the MP4 to Files. iOS does not let this local webpage select Photos as the download destination.
11. Confirm DeckClip reports the completed download, then press **Stop sharing**. Also confirm an uncompleted share expires after ten minutes.

Backend-only discovery tests can be run on any machine with Python 3.9+:

```bash
pnpm run test:backend
```

For safe fixture testing, set `DECKCLIP_STEAM_ROOT` to a fake Steam directory before loading the backend. This override is intended for development only.

## Known prototype limitations

- FFmpeg must be present on the Deck; a store-ready package should bundle a known-compatible binary.
- Game titles are resolved from `appmanifest_*.acf` across configured libraries, Steam's local v41 `appinfo.vdf` cache, and `shortcuts.vdf` for current non-Steam shortcuts. Deleted shortcuts without remaining local metadata appear under Unknown / Unmatched as `Steam app <id>`.
- Session-copy and concat assume Steam kept compatible codecs/settings across a multi-session clip. A resolution or codec change may require a future fallback re-encode.
- Export jobs are in memory and do not survive a Decky restart.
- Moving exports to Trash requires SteamOS's `gio` utility. If it is unavailable, DeckClip leaves the file untouched and directs the user to Desktop Mode.
- Phone transfer uses ordinary HTTP on the local network because the Deck cannot issue a browser-trusted certificate for its LAN address. Use it only on a trusted home network; the random link is temporary but network traffic is not encrypted.
- The phone and Deck must be able to reach each other directly. Guest Wi-Fi, client isolation, a VPN, or a firewall can prevent the QR link from opening.
