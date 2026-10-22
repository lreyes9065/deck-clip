# Decky ClipPort

Your Steam clips, ready to go. Export full-length clips and scan a QR code to download them to your phone over local Wi-Fi.

Formerly DeckClip; see `CHANGELOG.md` for the rename and other release notes. For storage compatibility, exports remain in `/home/deck/Videos/DeckClip/` and the installed ZIP folder remains `DeckClip/`.

ClipPort is a barebones Decky Loader plugin with a searchable game and clip browser that exports any selection of Steam Game Recording clips to normal MP4 files in:

`/home/deck/Videos/DeckClip/`

Each selected clip can be renamed before export. The UI reports overall and per-clip progress, then shows the destination folder. Existing files are never overwritten, and Steam's recording folders are only read.

ClipPort runs without root privileges. Its export manager lists only direct MP4 files in the dedicated output folder and offers confirmed permanent deletion, individually or for selected exports. Steam source recordings are never deleted. Existing system Trash contents are left alone.

Source recordings and exported videos have small static previews generated lazily by FFmpeg. Up to 100 previews share an in-memory cache and a single generation queue. Source previews use the initialization fragment and up to three initial video chunks, capped at 16 MB, in an automatically removed temporary file outside Steam's folders. No source files are modified and no preview images accumulate on disk. Unsupported, oversized, or unavailable recordings display a placeholder; browsing and exporting remain available. The preview is an early frame, not a precise trimmed-clip poster image.

One or more exported clips can also be shared directly to a phone through one QR code. ClipPort starts a temporary LAN web server, displays a single QR code for the selected batch, and stops sharing after ten minutes or when the user presses **Stop sharing**. After every selected file has downloaded, it keeps a 30-second retry window and then closes automatically. No LocalSend plugin, cloud account, or internet upload is required.

The phone page includes an optional **Save all to Photos** action for an explicitly configured iPhone Shortcut, plus individual browser downloads as the universal fallback. A cog beside the ClipPort title opens a permanent, full-screen **Help & Settings** page, letting users complete the guided Shortcut setup before starting the ten-minute transfer timer. See `docs/iphone-shortcut.md` for setup and security details.

## Architecture

- `src/index.tsx` is the small Decky entry point and screen coordinator. `src/pages/` contains the library, clip browser, and export manager screens; `src/components/` contains reusable UI; `src/hooks/` owns export and transfer polling; and typed backend calls, models, persistence, and formatting live under `src/api/`, `src/types/`, and `src/utils/`.
- `main.py` is Decky's entry point and coordinates export jobs. Services under `backend/` handle library discovery, safe export-file access/publication, fragment assembly, QR/LAN transfers, thumbnails, subprocess lifetimes, and persistent status history. `process_env.py` isolates system FFmpeg from Decky's bundled library environment.
- ClipPort feeds every numbered Steam `.m4s` fragment of each video/audio stream, in order, through a pipe straight into FFmpeg, which remuxes them without re-encoding. This avoids FFmpeg stopping after the first three-second DASH fragment, and no joined copies of the streams are written to disk. If a clip spans multiple recording sessions, ClipPort remuxes each session to a temporary part and concatenates the parts into one MP4, deleting the parts straight afterwards.
- Before exporting, ClipPort checks each session is complete: segment numbers must be contiguous, audio and video segment counts must agree (within one segment, since audio frames do not line up exactly with segment boundaries), and the counts must match the session manifest where it lists them. Clips Steam wrote to in the last ten seconds are shown as still saving and cannot be selected until the next rescan.
- Free space needed during export is about 1x the clip size for a single-session clip and about 2x for a multi-session clip (the parts plus the combined file).
- All intermediate and final video writes stay under `/home/deck/Videos/DeckClip/`. Completed output is published atomically without replacing existing names. The latest five export statuses are stored separately in Decky's plugin log directory as a private `recent-exports.json` file. Source clip paths are never opened for writing, renamed, or removed.
- Phone transfer serves at most 20 direct exports. Opened file identities must match the selected files; replacements are rejected. A random session URL protects the manifest and numbered file links. The server has an eight-client limit, write timeouts, and closes accepted connections when stopped or expired. It binds all IPv4 interfaces: reachability depends on routing, VPNs, and firewalls, not just Wi-Fi membership.

## Steam Deck test setup

### Prerequisites

1. Install Decky Loader and enable Developer Mode in Decky's settings.
2. Make sure ClipPort can find FFmpeg. See [FFmpeg](#ffmpeg) below.
3. Make at least one clip using Steam Game Recording.

### Build on another computer (recommended)

Building requires Node.js 18 or newer and pnpm 9.

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

This builds, tests, and validates `release/Decky-ClipPort-0.15.1.zip`. Its relevant layout is:

```text
DeckClip/
├── backend/
│   ├── __init__.py
│   ├── exports.py
│   ├── history.py
│   ├── library.py
│   ├── media.py
│   ├── process_env.py
│   ├── processes.py
│   ├── qr.py
│   ├── source_preview.py
│   ├── thumbnails.py
│   └── transfer.py
├── dist/index.js
├── docs/
│   ├── code-walkthrough.md
│   ├── iphone-shortcut.md
│   ├── release-checklist.md
│   └── security-review-2026-09-12.md
├── LICENSE
├── main.py
├── package.json
├── plugin.json
├── README.md
└── THIRD_PARTY_NOTICES.md
```

`scripts/package-decky.sh` copies exactly these files and `scripts/verify-decky-zip.py` rejects an archive with anything missing or extra.

### Install through Decky

1. Copy `release/Decky-ClipPort-0.15.1.zip` to the Deck's Downloads folder. Do not extract it.
2. In Gaming Mode, open the Quick Access menu (`…`) and Decky Loader.
3. Open Decky settings and enable **Developer Mode** if needed.
4. Open the Developer section, choose **Install Plugin from Zip**, and select `Decky-ClipPort-0.15.1.zip` from Downloads.
5. Wait for Decky to finish installing, then reload Decky or restart Steam if ClipPort does not immediately appear.

Decky owns its installed plugin directory and makes it read-only; that is expected. Install updates by generating and selecting a newer ZIP rather than editing `/home/deck/homebrew/plugins/DeckClip/` directly.

### FFmpeg

ClipPort runs FFmpeg for exports and previews. It looks in this order:

1. `~/.local/bin/ffmpeg` (that is, `/home/deck/.local/bin/ffmpeg`), if it exists and is executable.
2. `ffmpeg` on the system `PATH`.

SteamOS does not guarantee an `ffmpeg` command, and its system partition is read-only, so check in Desktop Mode with `ffmpeg -version` in Konsole. If it is missing, put a static Linux x86_64 FFmpeg build at `~/.local/bin/ffmpeg` (for example from a static-build provider such as johnvansickle.com/ffmpeg or BtbN/FFmpeg-Builds), then run `chmod +x ~/.local/bin/ffmpeg` and check `~/.local/bin/ffmpeg -version`. That folder is in your home directory, so it survives SteamOS updates. ClipPort does not download or bundle FFmpeg itself.

### Test checklist

1. Confirm the three games with the newest recordings appear on the first page. Select another game from the dropdown, confirm it replaces the recent three and appears as the selected value, then clear the filter.
2. Open a game and Unknown / Unmatched from the game selector; confirm clips are newest-first, initially unchecked, and loaded 25 at a time. Back to games appears only below Export; leaving a game clears its selection.
3. Filter clips by game name, localized date/time, or displayed duration.
4. Toggle one, several, or all desired clips. Add a filename to at least one selected clip.
5. Start export and watch both overall and per-clip progress.
6. Confirm the completion message points to `/home/deck/Videos/DeckClip/`.
7. Open each MP4 from Dolphin or a media player and check video, game audio, and any extra audio track you recorded.
8. Export the same names again and confirm ClipPort creates `name (2).mp4` rather than overwriting the first file.
9. Open **Manage exported clips**, check previews, then select expendable test exports. Cancel deletion first and verify they remain. Confirm permanent deletion and verify only those exports disappear; Steam originals must remain untouched.
10. In **Manage exported clips**, leave the exports unchecked, select several, and choose **Send selected clips**. Put the Deck and iPhone on the same trusted Wi-Fi network and scan the one QR code. With the documented Shortcut installed, test **Save all to Photos**; otherwise verify each individual download link works.
11. Confirm ClipPort reports progress for the selected batch and does not mark it complete until every clip finishes. Then press **Stop sharing**. Also confirm an uncompleted share expires after ten minutes.

Backend tests target POSIX systems with Python 3.10+. Media integration tests also require FFmpeg:

```bash
pnpm run test:backend
```

For safe fixture testing, set `DECKCLIP_STEAM_ROOT` to a fake Steam directory before loading the backend. This override is intended for development only.

## Known prototype limitations

- FFmpeg must be present on the Deck (see [FFmpeg](#ffmpeg)); ClipPort does not bundle it. A store release could bundle a pinned static build through Decky's `remote_binary` packaging instead.
- Game titles are resolved from `appmanifest_*.acf` across configured libraries, Steam's local `appinfo.vdf` cache (format version 29, magic `0x07564429`), and `shortcuts.vdf` for current non-Steam shortcuts. Deleted shortcuts without remaining local metadata appear under Unknown / Unmatched as `Steam app <id>`.
- Session-copy and concat assume Steam kept compatible codecs/settings across a multi-session clip. A resolution or codec change may require a future fallback re-encode.
- A running export keeps going while the Quick Access menu is closed, and the panel picks its progress back up when reopened. Exports cannot resume across a plugin or Deck restart. The latest five status records survive; unfinished records are marked interrupted. Persistence failure (for example, a full disk) is logged and does not invalidate an otherwise successful export.
- Deletion is permanent and limited to selected exports. Stop sharing and wait for exports to finish before deleting. Originals might have been deleted separately, so check before confirming.
- Phone transfer uses ordinary HTTP on the local network because the Deck cannot issue a browser-trusted certificate for its LAN address. Use it only on a trusted home network; the random link is temporary but network traffic is not encrypted.
- The phone and Deck must be able to reach each other directly. Guest Wi-Fi, client isolation, a VPN, or a firewall can prevent the QR link from opening.

## Diagnostics

Settings → Recent statuses shows the last five export batches across restarts. Error text is not visually ellipsized; very large FFmpeg output retains its final 128 KiB with an explicit notice. Records may contain local paths and game names. No transfer tokens or environment dumps are added to this history. Review screenshots before sharing them.

See `docs/release-checklist.md` for on-Deck acceptance tests and remaining release gates. See `docs/code-walkthrough.md` for a beginner-friendly source tour. This hardening pass is not a security certification.
