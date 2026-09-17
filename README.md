# Decky ClipPort

Your Steam clips, ready to go. Export full-length clips and scan a QR code to download them to your phone over local Wi-Fi.

Formerly DeckClip. Version 0.14.0 changes the displayed name to Decky ClipPort (ClipPort in the panel). For compatibility, exports remain in `/home/deck/Videos/DeckClip/`, the installed ZIP folder remains `DeckClip/`, and the iPhone Shortcut must still be named **DeckClip Save to Photos**. Internal routes and saved game filters retain their existing keys. No videos are moved or deleted by the rename. Older development notes below retain the former name.

The export-manager page now survives panel remounts during deletion confirmation and refreshes after deletion, including when the final export is removed. Return to the library using Back to games.

DeckClip is a barebones Decky Loader plugin with a searchable game and clip browser that exports any selection of Steam Game Recording clips to normal MP4 files in:

`/home/deck/Videos/DeckClip/`

Each selected clip can be renamed before export. The UI reports overall and per-clip progress, then shows the destination folder. Existing files are never overwritten, and Steam's recording folders are only read.

DeckClip runs without root privileges. Its export manager lists only direct MP4 files in the dedicated output folder and offers confirmed permanent deletion, individually or for selected exports. Steam source recordings are never deleted. Existing system Trash contents are left alone.

Exported videos have small static previews generated lazily by FFmpeg. Up to 100 previews are cached in memory; no thumbnail files accumulate on disk. Unsupported videos display a placeholder. Source-recording previews are not included yet.

One or more exported clips can also be shared directly to a phone through one QR code. DeckClip starts a temporary LAN web server, displays a single QR code for the selected batch, and stops sharing after ten minutes or when the user presses **Stop sharing**. After every selected file has downloaded, it keeps a 30-second retry window and then closes automatically. No LocalSend plugin, cloud account, or internet upload is required.

The phone page includes an optional **Save all to Photos** action for an explicitly configured iPhone Shortcut, plus individual browser downloads as the universal fallback. A cog beside the DeckClip title opens a permanent, full-screen **Help & Settings** page, letting users complete the guided Shortcut setup before starting the ten-minute transfer timer. See `docs/iphone-shortcut.md` for setup and security details.

## Architecture

- `src/index.tsx` is the small Decky entry point and screen coordinator. `src/pages/` contains the library, clip browser, and export manager screens; `src/components/` contains reusable UI; `src/hooks/` owns export and transfer polling; and typed backend calls, models, persistence, and formatting live under `src/api/`, `src/types/`, and `src/utils/`.
- `main.py` is Decky's entry point and coordinates export jobs. Services under `backend/` handle library discovery, safe export-file access/publication, fragment assembly, QR/LAN transfers, thumbnails, subprocess lifetimes, and persistent status history. `process_env.py` isolates system FFmpeg from Decky's bundled library environment.
- DeckClip explicitly assembles every numbered Steam `.m4s` fragment for each video/audio stream, then FFmpeg remuxes those streams without re-encoding. This avoids FFmpeg stopping after the first three-second DASH fragment. If a clip spans multiple recording sessions, DeckClip concatenates the resulting session parts into one MP4.
- All intermediate and final video writes stay under `/home/deck/Videos/DeckClip/`. Completed output is published atomically without replacing existing names. The latest five export statuses are stored separately in Decky's plugin log directory as a private `recent-exports.json` file. Source clip paths are never opened for writing, renamed, or removed.
- Phone transfer serves at most 20 direct exports. Opened file identities must match the selected files; replacements are rejected. A random session URL protects the manifest and numbered file links. The server has an eight-client limit, write timeouts, and closes accepted connections when stopped or expired. It binds all IPv4 interfaces: reachability depends on routing, VPNs, and firewalls, not just Wi-Fi membership.

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

Release builds compile TypeScript once into a temporary staging folder, then run Rollup with Decky's bundling defaults. This avoids the TypeScript Rollup plugin leaving file watchers open after a one-time build. Compiler errors stop the build, source maps point back to TypeScript, and staging is removed after completion or failure. `pnpm run watch` retains the standard Decky watch workflow for development.

### Create the installable ZIP

Do not use GitHub's automatic **Source code** ZIP and do not ZIP the repository root directly. Decky requires a release archive containing one top-level `DeckClip/` directory.

```bash
pnpm run release
```

This builds, tests, and validates `release/Decky-ClipPort-0.14.0.zip`. Its relevant layout is:

```text
DeckClip/
├── backend/
│   ├── __init__.py
│   ├── exports.py
│   ├── library.py
│   ├── media.py
│   ├── history.py
│   ├── processes.py
│   ├── process_env.py
│   ├── thumbnails.py
│   ├── qr.py
│   └── transfer.py
├── dist/index.js
├── docs/iphone-shortcut.md
├── main.py
├── package.json
├── plugin.json
├── README.md
├── LICENSE
└── THIRD_PARTY_NOTICES.md
```

### Install through Decky

1. Copy `release/Decky-ClipPort-0.14.0.zip` to the Deck's Downloads folder. Do not extract it.
2. In Gaming Mode, open the Quick Access menu (`…`) and Decky Loader.
3. Open Decky settings and enable **Developer Mode** if needed.
4. Open the Developer section, choose **Install Plugin from Zip**, and select `Decky-ClipPort-0.14.0.zip` from Downloads.
5. Wait for Decky to finish installing, then reload Decky or restart Steam if DeckClip does not immediately appear.

Decky owns its installed plugin directory and makes it read-only; that is expected. Install updates by generating and selecting a newer ZIP rather than editing `/home/deck/homebrew/plugins/DeckClip/` directly.

#### Upgrading from v0.3.1 or earlier

Earlier prototypes ran their backend as root and may have created a root-owned output folder. Before using v0.4.0, switch to Desktop Mode and run this one-time ownership repair in Konsole:

```bash
sudo chown -R deck:deck /home/deck/Videos/DeckClip
```

The command is intentionally limited to DeckClip's output folder. New exports are created as the normal `deck` user.

### Test checklist

1. Confirm the three games with the newest recordings appear on the first page. Select another game from the dropdown, confirm it replaces the recent three and appears as the selected value, then clear the filter.
2. Open a game and Unknown / Unmatched from the game selector; confirm clips are newest-first, initially unchecked, and loaded 25 at a time. Back to games appears only below Export; leaving a game clears its selection.
3. Filter clips by game name, localized date/time, or displayed duration.
4. Toggle one, several, or all desired clips. Add a filename to at least one selected clip.
5. Start export and watch both overall and per-clip progress.
6. Confirm the completion message points to `/home/deck/Videos/DeckClip/`.
7. Open each MP4 from Dolphin or a media player and check video, game audio, and any extra audio track you recorded.
8. Export the same names again and confirm DeckClip creates `name (2).mp4` rather than overwriting the first file.
9. Open **Manage exported clips**, check previews, then select expendable test exports. Cancel deletion first and verify they remain. Confirm permanent deletion and verify only those exports disappear; Steam originals must remain untouched.
10. In **Manage exported clips**, leave the exports unchecked, select several, and choose **Send selected clips**. Put the Deck and iPhone on the same trusted Wi-Fi network and scan the one QR code. With the documented Shortcut installed, test **Save all to Photos**; otherwise verify each individual download link works.
11. Confirm DeckClip reports progress for the selected batch and does not mark it complete until every clip finishes. Then press **Stop sharing**. Also confirm an uncompleted share expires after ten minutes.

Backend tests target POSIX systems with Python 3.10+. Media integration tests also require FFmpeg:

```bash
pnpm run test:backend
```

For safe fixture testing, set `DECKCLIP_STEAM_ROOT` to a fake Steam directory before loading the backend. This override is intended for development only.

## Known prototype limitations

- FFmpeg must be present on the Deck; a store-ready package should bundle a known-compatible binary.
- Game titles are resolved from `appmanifest_*.acf` across configured libraries, Steam's local v41 `appinfo.vdf` cache, and `shortcuts.vdf` for current non-Steam shortcuts. Deleted shortcuts without remaining local metadata appear under Unknown / Unmatched as `Steam app <id>`.
- Session-copy and concat assume Steam kept compatible codecs/settings across a multi-session clip. A resolution or codec change may require a future fallback re-encode.
- Exports cannot resume across a restart. The latest five status records survive; unfinished records are marked interrupted. Persistence failure (for example, a full disk) is logged and does not invalidate an otherwise successful export.
- Deletion is permanent and limited to selected exports. Stop sharing and wait for exports to finish before deleting. Originals might have been deleted separately, so check before confirming.
- Phone transfer uses ordinary HTTP on the local network because the Deck cannot issue a browser-trusted certificate for its LAN address. Use it only on a trusted home network; the random link is temporary but network traffic is not encrypted.
- The phone and Deck must be able to reach each other directly. Guest Wi-Fi, client isolation, a VPN, or a firewall can prevent the QR link from opening.

## Release hardening and diagnostics

Version 0.13.0 adds staged/no-overwrite publication, serialized export requests, coordinated delete/share starts, owned FFmpeg cancellation, bounded concurrent stderr reading, bounded previews, and transfer connection cleanup. Existing stream-copy behavior is unchanged; no video resizing or re-encoding was added.

Settings → Recent statuses shows the last five export batches across restarts. Error text is not visually ellipsized; very large FFmpeg output retains its final 128 KiB with an explicit notice. Records may contain local paths and game names. No transfer tokens or environment dumps are added to this history. Review screenshots before sharing them.

See `docs/release-checklist.md` for on-Deck acceptance tests and remaining release gates. See `docs/code-walkthrough.md` for a beginner-friendly source tour. This hardening pass is not a security certification.
