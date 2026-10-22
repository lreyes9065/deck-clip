# Changelog

Release notes moved out of the README. Newest first.

## Unreleased

- Committed the missing `backend/source_preview.py` (and its tests); `main.py` failed to import without it.
- Exports pipe fragments straight into FFmpeg instead of staging joined stream copies, cutting temporary disk use from up to about 3x the clip size to about 1x (2x for multi-session clips).
- Exports refuse incomplete recordings (missing segments, mismatched audio/video counts, or disagreement with the manifest). Clips Steam is still writing are marked as saving.
- Library scans skip clips that vanish mid-scan, and order sessions by their timestamped folder names.
- Busy previews are retried by the panel with backoff instead of staying blank.
- The panel resumes showing a running export's progress after the Quick Access menu is reopened.
- Export filenames are capped at 200 UTF-8 bytes (previously 160 characters, which could exceed the 255-byte limit for CJK titles).
- The output folder follows Decky's `DECKY_USER_HOME` instead of a hardcoded `/home/deck`.
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
