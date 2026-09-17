# DeckClip sanity review — 2026-09-12

Scope: current working-tree frontend, Python services, build/package scripts and existing tests. This is a source review and focused local validation, not a penetration test or a security certification. Runtime code was not changed. No real recordings or exports were used by the reproduction checks.

## Follow-up: 0.13.0 hardening

The findings below describe the September 12 baseline. The subsequent 0.13.0 pass adds atomic no-overwrite publication and staged concat output; export/mutation locks; transfer lifecycle locks, tracked clients, write deadlines and client caps; descriptor-based transfer/thumbnail reads with file-identity checks; cancellation-owned subprocesses and fragment workers; concurrent bounded stderr draining; input/job/preview bounds; sequential guarded polling; persistent five-batch history; and removal of unreachable UI/test-compatibility code. New failure-focused regression tests cover these changes.

Remaining limitations include in-place changes by local writers, cross-process deletion-name races, abrupt-kill staging cleanup, dependency/FFmpeg advisory coverage, some Steam metadata input limits, and live Deck/controller/network acceptance testing. See `release-checklist.md`. Do not interpret this update as a security certification or a declaration that every baseline concern has been eliminated.

## Findings to address before 1.0

### 1. Export publication can overwrite data or expose incomplete output (high priority, reproduced)

Location: `main.py`, `_export_one` and `start_export`.

The destination existence check occurs before conversion. The single-session branch later calls `os.replace`, which overwrites a destination created in the meantime. The multi-session branch passes the final destination directly to FFmpeg with `-y`, allowing overwrite and leaving partial output at a publicly listed filename on failure. The backend does not serialize export jobs; quick repeated requests or another local application can expose this race. UI disabling is not a backend lock.

Temporary fixtures reproduced both behaviors: an existing file created during the mock conversion was replaced, and an intentionally failed concat left `partial.mp4` in `list_exports`.

Fix: serialize conflicting operations; assemble every result under staging; publish only a complete file with an atomic no-overwrite operation and retry naming on collision. Fix suffix generation too: repeated collisions currently accumulate suffixes such as `name (2) (3).mp4`.

### 2. Transfer shutdown does not close accepted clients (high priority, source-confirmed)

Location: `backend/transfer.py`, `_stop_transfer`, `_handle_transfer_client`, `_send_http`.

Stopping closes the listening server, but accepted writers/tasks are not tracked. A handler may be suspended in `writer.drain()` without a timeout. Stopping or expiring a session changes shared state, but cannot interrupt that wait or immediately close the file/socket. Header reads have a timeout; download writes do not. There is also no active-client cap, so many LAN connections can consume descriptors and tasks while sharing is enabled. These are resource-exhaustion and lifecycle risks, not evidence of unauthenticated file download.

Fix: cap clients, add write/idle deadlines, track and close accepted connections on stop/expiry/unload, and wait for handlers to terminate. Test with a receiver that stops reading.

### 3. Selected files are authorized by mutable names (medium priority, local-write prerequisite)

Location: `backend/exports.py`, `export_path`; `backend/transfer.py`, file request; `backend/thumbnails.py`, `get`.

Path validation rejects a symlink when inspected, but the pathname is reopened later. A local process able to modify the folder can replace a file between validation and opening. Transfer sessions retain filenames, not pinned file identities: replacing an authorized export under the same name changes what that session can serve. Symlink replacement could redirect a later read to another file accessible to the Deck user. A remote requester alone cannot create this condition.

Fix: open relative to a pinned directory descriptor with no-follow semantics; validate the opened descriptor and stream it. Decide whether a session pins files or rejects changed inode/metadata. Use equivalent safe descriptor handling for thumbnail inputs. Deletion uses a pinned directory and no-follow stat, which is better, but still targets a mutable filename and can delete a replacement direct child; a symlink swapped immediately before unlink would be unlinked, not its target followed.

### 4. Concurrent session starts can lose ownership of a server (medium priority, source-confirmed)

Location: `backend/transfer.py`, `start_transfer`; `main.py`, deletion/export guards.

Two starts can interleave across awaits, both stop the old session, then both create a server; one overwrites `self.transfer` and its expiry task reference. The earlier listener may be left untracked and use the current shared session state. Delete-versus-start guards are also checks separated from later asynchronous actions, not a shared critical section.

Fix: a lifecycle lock for transfer start/stop and coordinated mutation guards. Disable duplicate frontend requests too, but enforce this in Python. Test overlapping start/stop/delete calls using controlled task barriers.

### 5. Export cancellation and stderr handling need work (medium priority)

Location: `main.py`, `_remux_session`, `_run`, `_unload`; `backend/media.py`, `join_fragments`.

Export subprocesses are not killed and awaited in cancellation cleanup. Cancelling the parent job can leave FFmpeg alive after plugin unload. Remux drains stdout before stderr; sufficiently large error output can fill stderr and block the process. Cancelling an `asyncio.to_thread` await does not stop its underlying copy worker.

Fix: own subprocess lifetimes in try/finally, drain pipes concurrently, terminate then await on cancellation, and make copying cancellation-aware. Do not remove staging while workers may still use it.

### 6. Resource limits and malformed input coverage are incomplete (medium/low priority)

- `main.py`: no export batch-size or concurrent-job bound; completed jobs remain in memory indefinitely. Validate item types, rename types, duplicates and count before work.
- `backend/library.py`, `_duration_from_mpd`: `read_text()[:65536]` reads the entire file before truncating; the comment claiming a bounded header read is inaccurate. Use a bounded read. Other text metadata reads also need limits appropriate to Steam files.
- `backend/thumbnails.py`: the cache is bounded and work serialized, but the byte limit is checked after `communicate()` has buffered output. Pending callers are not bounded. Media decoding still trusts the installed FFmpeg parser; this review did not audit that binary or demonstrate a decoder exploit.
- `backend/transfer.py`: extremely long numeric file IDs/ranges can raise conversion errors and produce logged exceptions rather than a clean rejection. Header size limits reduce, but do not eliminate, this case.

## Leftovers and correctness cleanup

- `ALL_CLIPS` branches remain in the frontend, but the current selector does not offer that option. Restore an intentional entry or remove the unreachable mode.
- `main.py` re-exports several library/QR helpers mainly because older tests import them through `main`; the `transfer` property and `_handle_transfer_client` wrapper serve similar test compatibility. Move tests toward direct service imports before removal. `_lan_address` and `time` are not needed by production coordinator logic.
- Transfer status includes overlapping `downloads`, `completed_files`, `filename`, `filenames`, and a stored `path` that is not used for opening. Simplify after checking callers. String input compatibility for transfers is deliberate, not automatically dead code.
- Polling hooks use asynchronous intervals without preventing overlap or guarding late responses. A late transfer status can restore a stopped QR; `stop()` clears UI state even if stopping the server fails. Report failure accurately and ignore outdated responses.
- `docs/iphone-shortcut.md` says closure occurs after a complete download; batches require every file to complete. `README.md` still references an All Clips option, an old game-selector label, and Python 3.9 despite syntax requiring 3.10+. The architecture tree omits `thumbnails.py`. The walkthrough says help makes no backend requests even though its manual rescan does.
- `plugin.json` retains the debug flag. Review release flags against the target Decky version; this alone is not evidence of a public debug endpoint.
- `scripts/build.mjs` cleans staging on normal success/failure, but has no explicit signal-forwarding cleanup for interruption. Document or handle that lifecycle.
- Package verification checks names/structure, not provenance, unexpected secrets or symlink attributes. The packaging script currently copies an explicit source allowlist, which reduces accidental inclusion.

## Existing protections worth retaining

No credential collection, telemetry, cloud upload, dynamic eval, or shell-interpolated runtime commands were found in the inspected application code. FFmpeg is called with argument arrays. Transfer tokens use `secrets.token_urlsafe(32)`. The server accepts a small set of read-only routes; filenames are escaped in HTML and encoded in download headers. Deletion accepts direct regular MP4 children and does not recursively remove directories. Steam data is opened for reading by discovery/assembly helpers. None of these protections removes the race/lifecycle issues above.

HTTP is an explicit limitation: tokens and videos are not encrypted. Binding to `0.0.0.0` means all IPv4 interfaces, not a guarantee of home-Wi-Fi-only access. Reachability depends on the Deck's routing, VPN and firewall. A token protects requests but does not encrypt traffic.

## Validation and limits

- TypeScript check: passed.
- Existing backend suite: 15 tests passed.
- Temporary export fixtures: reproduced overwrite and partial-output publication.
- Dependency audit: attempted `pnpm audit --json`; it returned no result and was interrupted. Dependency advisory coverage is unresolved, not clean. Run it with registry access before release; review both shipped and build dependencies.
- No live hostile-network test, Steam Deck focus test, FFmpeg binary audit, or exhaustive dependency source inspection performed.

Recommended order: safe output publication; session/client lifecycle; safe file opening; cancellation and request bounds; polling/compatibility cleanup; fresh dependency audit and an on-Deck regression pass.
