# DeckClip 0.13.0: release-hardening checklist

This is a pre-1.0 test build. Use expendable exported copies for destructive tests; never remove Steam source recordings as part of this checklist.

## Automated checks

Run `pnpm run release` and `git diff --check`. The backend suite covers remuxed multi-fragment duration, original-library environment restoration, no-overwrite publication, failed concat cleanup, file replacement rejection, malformed ranges, concurrent sharing/export starts, stalled-client shutdown, subprocess cancellation, bounded stderr, and private persistent history.

The ZIP verifier requires the runtime modules and rejects unexpected files, duplicate names, traversal, and symlink entries. These checks do not prove dependency provenance.

## Steam Deck acceptance (still required)

1. Install the ZIP. Browse known and unknown games. Verify dropdown, focus, help tabs, and controller Back. The only Back to games button belongs below Export.
2. Export one clip, then multiple clips, including a clip spanning recording sessions. Verify duration, audio tracks, dimensions, and playback. No re-encoding is intended.
3. Export the same name three times. Expect stable `(2)` and `(3)` suffixes; existing files must remain unchanged.
4. Leave a game with selected clips, then reopen it: selections must be cleared. A running job must continue independently.
5. Check lazy previews. Select exports, cancel deletion, then permanently delete only disposable test exports. Confirm Steam originals remain.
6. Share a batch to iPhone. Test browser downloads and the Shortcut separately. A completed download is not a Photos acknowledgement.
7. Start a download, stop sharing, and verify the connection closes. Test expiry and interrupted Wi-Fi. Retry with a fresh QR. Confirm controller Stop remains usable after a status error.
8. Restart Decky after a finished export and inspect Recent statuses. Restart during a disposable export; verify the status records cancellation/interruption and no partial top-level MP4 is offered. An abrupt process kill can leave hidden staging directories; do not delete them blindly while another export is active.
9. Exercise low-space failure in an isolated test environment, not by filling a user's normal disk. Verify helpful errors, no overwritten videos, and no incomplete published MP4. Saving the status file itself may fail when the disk is full.

## Remaining gates / limitations

- Obtain a successful dependency advisory audit before 1.0. The local `pnpm audit --json` attempt returned no result and was interrupted; dependencies are not certified clear.
- Test on real SteamOS and multiple iPhones/Android browsers. Host tests do not establish controller usability or Linux media compatibility.
- HTTP remains unencrypted and binds all IPv4 interfaces. Use trusted networks; possession of the live token plus network reachability grants access.
- File identity checks reject replacement before serving. A process with the same user's write access can still modify an already-open file in place. This is not a sandbox against a malicious local account.
- Permanent deletion targets selected direct filenames. External local renames can change which direct child occupies a selected name; no cross-process transaction is promised.
- System FFmpeg is not bundled or independently audited. Libraries/parser vulnerabilities and codec changes remain external compatibility concerns.
- Export count is limited to 100, one active batch; transfer selection remains 20. No silent reduction of existing transfer limits.
- Downloadable/redacted diagnostic bundles and a multi-user beta remain future work; current history is readable in Settings and stored privately under Decky's plugin log directory.
- The existing plugin debug flag is retained pending Decky release-policy review; no root flag is enabled.

## Understanding the new boundaries

Read `backend/exports.py` for no-overwrite publication and safe file opens; `backend/processes.py` for process ownership and concurrent output draining; `backend/media.py` for cancellation-aware worker cleanup; `backend/transfer.py` for session locking, client limits, and shutdown; and `backend/history.py` for bounded persistent diagnostics.

The older security review is a historical baseline, not a claim that every finding remains unfixed. Its follow-up section records what changed in this build.
