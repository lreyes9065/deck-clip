# Changelog

Release notes moved out of the README. Newest first.

## Unreleased

- Plugin unload is fast and leaves nothing behind. Decky sends SIGTERM and then SIGKILL 5 seconds later; `_unload` now finishes in about 1 second (2.5-second budget). FFmpeg runs in its own process group, so every child is signalled at once, given a short grace period, then killed together with anything it started. Unload no longer waits for the operation lock, the transfer server aborts stalled phone connections instead of flushing to them, partial exports are removed, and leftover `.deckclip-*` staging folders from a killed run are cleaned up on the next load.
- Every backend call from the panel has a timeout (15 s for listing clips and exports, 5 s for status checks, longer for previews and starting an export). A timed-out or failed library scan is cleared, so reopening the menu or Rescan asks the backend again instead of waiting on the dead call. Export and transfer status polling never overlaps, backs off and retries on errors, shows "Status delayed" while retrying, and stops with a clear message if the backend stays unresponsive.
- CI covers Decky's Python versions (3.10, 3.11 for Decky v3.0 to v3.2.10, 3.14 for Decky v3.3.0) and checks every backend import, including imports inside functions, against the modules Decky's bundled Python ships, using committed lists for v3.2.10 and v3.3.0 (`tests/decky_bundled_modules-*.txt`). It also imports `main.py` under the matching Python with only those modules allowed, lints `plugin.json` (no `debug` or `root` flag), and lists the release ZIP. The ZIP verifier now also lints the packaged `plugin.json` and compiles every packaged Python file.
- Fixed: 0.15.1 builds never loaded their backend on the Deck, so the panel sat on "Looking for clips…" with an empty game list. The manifest check imported `xml.etree`, which Decky's bundled Python does not ship, so `main.py` failed at import. Manifests are now read without it, and a test checks every backend import against the modules Decky ships.
- Clip listing never hides a clip because of its contents. Only a folder with no `session.mpd` is left out, as in 0.15.0. Unreadable clips are logged and skipped without failing the list. The panel says when the backend has not answered after 15 seconds.
- Exports are blocked only by real damage: no video stream, or a gap in a stream's segments. Manifest disagreements and uneven audio/video counts, which real Steam clips can have, are logged as warnings and the export goes ahead.
- The Deck user's home is resolved in this order: `DECKY_USER_HOME`, Decky's helpers (`decky.DECKY_USER_HOME`, then the home of `DECKY_USER`), `$HOME` (never `/root`), and finally `/home/deck`.

- Committed the missing `backend/source_preview.py` (and its tests); `main.py` failed to import without it.
- Exports pipe fragments straight into FFmpeg instead of staging joined stream copies, cutting temporary disk use from up to about 3x the clip size to about 1x (2x for multi-session clips).
- Exports refuse incomplete recordings (missing segments or no video). Clips Steam is still writing are marked as saving.
- Library scans skip clips that vanish mid-scan, and order sessions by their timestamped folder names.
- Busy previews are retried by the panel with backoff instead of staying blank.
- The panel resumes showing a running export's progress after the Quick Access menu is reopened.
- Export filenames are capped at 200 UTF-8 bytes (previously 160 characters, which could exceed the 255-byte limit for CJK titles).
- The output folder follows the Deck user's home (see above) instead of a hardcoded `/home/deck`.
- FFmpeg is found in `~/.local/bin/ffmpeg` or on `PATH`; the error message and README now say so accurately.
- Removed the `debug` flag from `plugin.json`. Added GitHub Actions CI.

## 0.15.1

Formerly DeckClip. The plugin is now Decky ClipPort (ClipPort in the panel). Starting with 0.15.1, rename your existing iPhone Shortcut to **ClipPort Save to Photos**; its actions are unchanged. For storage compatibility, exports remain in `/home/deck/Videos/DeckClip/` and the installed ZIP folder remains `DeckClip/`. Internal routes, saved game filters, and legacy-history lookup retain their existing keys. No videos are moved or deleted. Historical review documents and old release archives retain their original names.

The export-manager page now survives panel remounts during deletion confirmation and refreshes after deletion, including when the final export is removed. Return to the library using Back to games.

## 0.13.0

Version 0.13.0 adds staged/no-overwrite publication, serialized export requests, coordinated delete/share starts, owned FFmpeg cancellation, bounded concurrent stderr reading, bounded previews, and transfer connection cleanup. Existing stream-copy behavior is unchanged; no video resizing or re-encoding was added.

## 0.4.0

### Upgrading from v0.3.1 or earlier

Earlier prototypes ran their backend as root and may have created a root-owned output folder. Before using v0.4.0, switch to Desktop Mode and run this one-time ownership repair in Konsole:

```bash
sudo chown -R deck:deck /home/deck/Videos/DeckClip
```

The command is intentionally limited to ClipPort's output folder. New exports are created as the normal `deck` user.
