# Reading DeckClip

Updated for 0.13.0: read `release-checklist.md` alongside this guide. The historical security review now has a follow-up section distinguishing resolved findings from remaining limitations.

Start with `src/index.tsx`. It registers the plugin and its full-screen help route, then coordinates the library, clip selection, and exported-file screens. The title cog opens the help route; unloading the plugin removes it.

Next read `src/pages/`. Pages receive data and callbacks from the coordinator. A callback such as `onExport` reports a user action; the page itself does not run FFmpeg.

The library refreshes on panel visibility through `useQuickAccessVisible`. `src/api/library.ts` shares overlapping scan requests and sends results to subscribed screens. The coordinator keeps selections and rename fields whose clip IDs still exist. Settings offers a manual rescan through the same service. Reopening a panel that stayed mounted preserves these edits; restarting or remounting the plugin still resets its in-memory edits.

`HelpSettingsPage.tsx` uses Decky's native `SidebarNavigation` for its layout. Each topic has a distinct child route under `/deckclip/help`, following Decky's own settings screen. The parent route accepts those child paths, and Steam handles page selection. Reading the iPhone instructions starts no transfer timer. The settings area's manual rescan does call the backend. The title cog uses the same DialogButton sizing and gear icon as Decky's header.

`src/hooks/useExportJob.ts` and `usePhoneTransfer.ts` start operations and poll progress. `src/api/deckclip.ts` connects them to Python methods in `main.py`. TypeScript types describe expected data; the backend still needs to validate requests.

`main.py` coordinates exports and delegates to `backend/`: `library.py` discovers recordings, `media.py` handles fragments, `exports.py` restricts exported-file access, and `transfer.py` runs temporary LAN sharing.

## UI design rule

Prefer `@decky/ui` controls and Steam's navigation, typography, spacing, and focus behavior. Use custom rendering when needed for content such as QR codes. Verify scrolling, directional navigation, and Back on a real Deck; compilation does not establish controller usability. Future bulk file actions should use native confirmation dialogs and remain limited to exports.

## Walking through the code together

Trace one familiar action at a time: selecting, exporting, then sharing a clip. Follow its page callback into the coordinator or hook, the backend call, and the returned status. This connects each file to behavior you already recognize.

## Beginner's vocabulary

The **frontend** is the interface inside Steam. The **backend** is Python running on the Deck, with access to files and FFmpeg. An **RPC** is a request across that boundary: the interface asks Python to perform an operation and return data.

`.tsx` means TypeScript with interface markup. A **component** describes part of a screen. **Props** are inputs passed to a component; **state** is information remembered between renders. A **hook** packages stateful behavior. A **callback** is a function supplied by another component, such as what to do when Export is pressed.

In Python, `async`/`await` lets an operation pause while other work runs. `create_task` schedules background work. `asyncio.to_thread` moves blocking work to a worker thread. Canceling the awaiting task does not automatically stop that thread or an FFmpeg process.

## Session 1: opening the library

1. Open `plugin.json`: this describes the plugin to Decky, including flags.
2. Open `package.json`: this lists frontend dependencies and development commands.
3. In `src/index.tsx`, find `definePlugin`. Identify the title, content, help route, and unload cleanup.
4. Find `useQuickAccessVisible` and follow the refresh call into `src/api/library.ts`.
5. Follow the backend bridge in `src/api/deckclip.ts` to `list_clips` in `main.py` and recording discovery in `backend/library.py`.
6. Return to the frontend and locate where the returned list becomes state and is passed into `LibraryPage`.

The sequence is: panel becomes visible → scan request → recording discovery → returned data → updated screen.

Exercise: search for `localStorage` and compare it with the selection state. This explains why a game filter can survive restarting the plugin while an unsaved rename may not.

## Session 2: exporting a clip

1. In `src/pages/LibraryPage.tsx`, find the export callback.
2. Follow it into `src/index.tsx`, then `src/hooks/useExportJob.ts` and the API bridge.
3. In Python, read `start_export`, `_run_export`, and `_export_one` in that order.
4. Follow the fragment-handling helpers into `backend/media.py`.
5. Return to the hook and find how status polling updates progress and errors.

**Remuxing** puts existing encoded video and audio into a new container without necessarily re-encoding it. Steam recordings consist of fragments; a clip can span multiple recording sessions too.

Exercise: find `publish_export` in `backend/exports.py` and `-y` in the export coordinator. FFmpeg now writes inside staging, then hard-link creation publishes a complete output without overwriting an existing name. The historical overwrite and partial-output findings motivated this change. Find the regression tests demonstrating collisions and failed concat cleanup.

## Session 3: sending to the phone

Start with the send callback in `src/pages/ExportManagerPage.tsx`. Follow it through the coordinator, `src/hooks/usePhoneTransfer.ts`, and Python into `backend/transfer.py`.

The server validates export names, creates a random access token, and starts temporary HTTP sharing. The QR opens the landing page. The iPhone shortcut receives a manifest URL, downloads that manifest, reads `files`, repeats over each item's `url`, downloads the video, and saves that downloaded content to Photos. Saving the initial URL instead explains the earlier image-of-text result.

The server can observe a download finishing; it cannot prove Photos saved the video. HTTP is unencrypted. Anyone with the live URL who can reach the server may access the shared files. Listening on `0.0.0.0` means all IPv4 interfaces, not a guarantee of home-Wi-Fi-only access.

Exercise: find token generation, expiration, route matching, and `writer.drain`. Trace the write timeout and tracked-client cancellation in `_stop_locked`. These are separate from the expiry timer: setting an expired state alone would not close a stalled connection.

## Session 4: previews and deletion

For previews, start with `src/components/ExportThumbnail.tsx`. Its visibility observer delays fetching until needed. Follow the call into `backend/thumbnails.py`: FFmpeg extracts a small frame and the manager caches results. Read the cache count, process timeout, and byte limit separately; they constrain different resources.

For deletion, follow the export manager's confirmation callback into `main.py`, then `backend/exports.py`. The helper limits deletion to direct regular MP4 children of the export directory. It does not recursively delete folders or target Steam recording paths.

Permanent deletion does not use Trash. The app also does not verify that the original recording still exists before deleting its export.

Exercise: find `dir_fd`, `follow_symlinks=False`, and `unlink`. A descriptor refers to an opened resource; a filename can change underneath you. Read the audit's remaining replacement-race concerns alongside these safeguards.

## Session 5: build and test

Read `package.json`'s scripts, then `scripts/build.mjs` and `rollup.config.js`. The normal build compiles TypeScript in a staging directory and bundles the frontend. Packaging collects runtime files into the Decky ZIP. Steam loads the bundle, not the individual frontend source files you edit.

A **watcher** deliberately stays alive to rebuild after edits. A release build should finish and exit.

From the project directory:

```sh
pnpm run typecheck
pnpm run test:backend
pnpm run build
```

Type checking checks TypeScript consistency. Tests check only their covered cases. Building checks bundle production. None proves security or controller usability. Review dependency and lockfile changes before installing third-party tooling.

## Your repeatable inspection checklist

For one feature at a time, answer:

1. Which user action starts it, and which component receives it?
2. Does it change only interface state or ask Python to act?
3. Where does Python validate the inputs?
4. Which files, processes, or network connections can it touch?
5. What happens on failure, cancellation, repeated clicks, or unload?
6. Which test demonstrates that behavior, and what remains untested?

Use your editor's Find All References to follow functions. Search for a familiar button label and follow its callback. Terminal examples:

```sh
rg -n 'start_export|_export_one' main.py src tests
rg -n 'create_subprocess|os.replace|unlink|start_server' main.py backend
rg -n 'setInterval|localStorage|callable' src
```

Remember: frontend types are not backend validation; a disabled button is not a backend lock; checking a path and opening it later are separate operations; marking a transfer expired does not necessarily close an existing connection.

Keep experiments in temporary fixtures, not real clip folders. Start with one session above per sitting. The goal is to explain what a feature touches, why it may touch it, and what happens when it fails—not memorize every line.
