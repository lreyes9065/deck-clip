from __future__ import annotations

import asyncio
import copy
import os
import shutil
import sys
import tempfile
import threading
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import decky

# Decky loads main.py directly rather than importing the plugin directory as a
# Python package. Resolve bundled modules from this read-only installed folder
# explicitly, independent of Decky's process working directory.
PLUGIN_DIR = Path(__file__).resolve().parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from backend.exports import delete_exports, export_path, list_exports, publish_export, safe_output_name as _safe_output_name
from backend.history import load_history, save_history
from backend.library import _discover, _duration_from_mpd, sorted_sessions
from backend.media import IncompleteRecording, feed_fragments, stream_files as _stream_files, validate_recording
from backend.paths import user_home
from backend.process_env import find_ffmpeg
from backend import processes
from backend.processes import run_ffmpeg
from backend.thumbnails import THUMBNAIL_BUSY, Thumbnails
from backend.transfer import TransferManager


OUTPUT_DIR = user_home() / "Videos" / "DeckClip"
# Decky sends SIGTERM, then SIGKILL 5 seconds later: _unload must finish well
# inside that, including stopping FFmpeg and the transfer server.
UNLOAD_BUDGET = 2.5
STAGING_PREFIX = ".deckclip-"


def _remove_staging(output_dir: Path) -> int:
    """Delete leftover export staging folders (partial MP4s) in the output folder.

    Only direct children named .deckclip-* that are real directories, never
    symlinks, and never anything else in the folder.
    """
    removed = 0
    try:
        entries = list(os.scandir(output_dir))
    except OSError:
        return 0
    for entry in entries:
        if not entry.name.startswith(STAGING_PREFIX):
            continue
        try:
            if entry.is_dir(follow_symlinks=False):
                shutil.rmtree(entry.path)
                removed += 1
        except OSError:
            pass
    return removed


def _log(level: str, message: str, *args) -> None:
    """Log through Decky's logger; logging must never break a request."""
    method = getattr(getattr(decky, "logger", None), level, None)
    if method is None:
        return
    try:
        method(message, *args)
    except Exception:
        pass


def _export_path(filename: str) -> Path:
    return export_path(OUTPUT_DIR, filename)


def _list_exports() -> list[dict[str, Any]]:
    return list_exports(OUTPUT_DIR)


class Plugin:
    async def _main(self):
        self.operation_lock = asyncio.Lock()
        self.closing = False
        history_dir = getattr(decky, "DECKY_PLUGIN_LOG_DIR", None)
        self.history_dir = Path(history_dir) if history_dir else None
        self.jobs: dict[str, dict[str, Any]] = load_history(self.history_dir)
        # Decky may key persistent directories by the new manifest name. Read
        # the former plugin's bounded status file without changing old data.
        if not self.jobs and self.history_dir is not None and self.history_dir.name == "Decky ClipPort":
            self.jobs = load_history(self.history_dir.parent / "DeckClip")
        self.tasks: set[asyncio.Task] = set()
        self.transfers = TransferManager(_export_path, decky.logger)
        self.thumbnails = Thumbnails(_export_path)
        self.preview_clips = set()
        # A previous run killed mid-export can leave partial output behind.
        if await asyncio.to_thread(_remove_staging, OUTPUT_DIR):
            _log("info", "ClipPort removed unfinished export files from a previous run")
        decky.logger.info("ClipPort loaded")

    async def _unload(self):
        """Stop everything within UNLOAD_BUDGET seconds, before Decky's SIGKILL.

        Never waits on operation_lock: a request holding it must not delay
        stopping FFmpeg. Order: signal every child at once, cancel tasks and
        stop sharing in parallel with a shared deadline, then SIGKILL whatever
        is left and remove partial output.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + UNLOAD_BUDGET
        self.closing = True
        processes.terminate_all()
        tasks = list(getattr(self, "tasks", ()))
        for task in tasks:
            task.cancel()
        work = [*tasks]
        if getattr(self, "thumbnails", None) is not None:
            work.append(asyncio.ensure_future(self.thumbnails.close()))
        if getattr(self, "transfers", None) is not None:
            work.append(asyncio.ensure_future(self.transfers.shutdown()))
        if work:
            # Leave time after this for SIGKILL, reaping and cleanup.
            done, pending = await asyncio.wait(work, timeout=max(0.1, deadline - loop.time() - 0.8))
            for future in done:
                if not future.cancelled() and future.exception() is not None:
                    _log("warning", "ClipPort unload step failed: %s", future.exception())
            if pending:
                _log("warning", "ClipPort: %d unload step(s) still running at the deadline", len(pending))
        processes.kill_all()
        await processes.reap_all(max(0.0, deadline - loop.time() - 0.3))
        for job in getattr(self, "jobs", {}).values():
            if job.get("state") in ("queued", "running"):
                job["state"] = "cancelled"
                job["error"] = "Export cancelled while the plugin was unloading."
        if getattr(self, "jobs", None):
            self._save_history()
        if any(not task.done() for task in tasks):
            # A task is still finishing; let it remove its own staging folder.
            return
        _remove_staging(OUTPUT_DIR)

    async def list_clips(self) -> list[dict[str, Any]]:
        try:
            clips = await asyncio.to_thread(_discover)
        except Exception:
            _log("exception", "ClipPort could not list Steam clips")
            raise
        self.preview_clips = {clip["id"] for clip in clips}
        return clips

    async def get_clip_thumbnail(self, clip_id: str):
        # Accept only IDs issued by the current library scan, never arbitrary paths.
        if not isinstance(clip_id, str) or clip_id not in self.preview_clips:
            raise ValueError("Clip is not in the current library")
        return self._thumbnail_result(await self.thumbnails.get(clip_id, source_path=Path(clip_id)))

    @staticmethod
    def _thumbnail_result(value) -> dict[str, Any]:
        # "busy" means the preview queue was full: the panel retries later.
        if value is THUMBNAIL_BUSY:
            return {"status": "busy"}
        return {"status": "ready", "image": value}

    async def list_exports(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(_list_exports)

    async def delete_exports(self, filenames: list[str]):
        async with self.operation_lock:
            if self.closing:
                raise ValueError("ClipPort is shutting down")
            return await self._delete_exports(filenames)

    async def _delete_exports(self, filenames):
        if any(job["state"] in ("queued", "running") for job in self.jobs.values()):
            raise ValueError("Wait for the current export to finish")
        if self.transfers.transfer is not None:
            raise ValueError("Stop sharing before deleting exports")
        result = await asyncio.to_thread(delete_exports, OUTPUT_DIR, filenames)
        self.thumbnails.cache.clear()
        return result

    async def get_export_thumbnail(self, filename: str):
        return self._thumbnail_result(await self.thumbnails.get(filename))

    async def start_transfer(self, filenames: list[str] | str) -> dict[str, Any]:
        async with self.operation_lock:
            if self.closing:
                raise ValueError("ClipPort is shutting down")
            return await self.transfers.start_transfer(filenames)

    async def get_transfer_status(self) -> dict[str, Any]:
        return await self.transfers.get_transfer_status()

    async def stop_transfer(self) -> dict[str, str]:
        return await self.transfers.stop_transfer()

    async def start_export(self, items: list[dict[str, str]]) -> dict[str, str]:
        async with self.operation_lock:
            if self.closing:
                raise ValueError("ClipPort is shutting down")
            if any(job["state"] in ("queued", "running") for job in self.jobs.values()):
                raise ValueError("Wait for the current export to finish")
            return await self._start_export(items)

    async def _start_export(self, items):
        if not isinstance(items, list) or not 1 <= len(items) <= 100:
            raise ValueError("Select between 1 and 100 clips")
        seen = set()
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise ValueError("Invalid clip selection")
            if item["id"] in seen:
                raise ValueError("A clip was selected more than once")
            seen.add(item["id"])
            name = item.get("name")
            if name is not None and (not isinstance(name, str) or len(name) > 512):
                raise ValueError("Invalid export name (maximum 512 characters)")
        available = {clip["id"]: clip for clip in await self.list_clips()}
        requested = []
        for item in items:
            clip = available.get(item.get("id", ""))
            if clip is None:
                raise ValueError("A selected clip is no longer available")
            if clip.get("saving"):
                raise ValueError(f"{clip['game_name']} is still being saved by Steam. Try again in a few seconds.")
            requested.append((clip, item.get("name")))
        job_id = uuid.uuid4().hex
        # Keep bounded job state, allowing recent frontend polling to finish.
        while len(self.jobs) >= 20:
            self.jobs.pop(next(iter(self.jobs)))
        self.jobs[job_id] = {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "state": "queued", "progress": 0.0, "output_dir": str(OUTPUT_DIR),
            "clips": [{"id": clip["id"], "display_name": clip["game_name"], "progress": 0.0, "state": "queued"} for clip, _ in requested],
        }
        self._save_history()
        task = asyncio.create_task(self._run_export(job_id, requested))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return {"job_id": job_id}

    async def get_export_status(self, job_id: str) -> dict[str, Any]:
        if job_id not in self.jobs:
            raise ValueError("Unknown export job")
        return copy.deepcopy(self.jobs[job_id])

    async def get_active_export(self) -> dict[str, Any] | None:
        """The queued or running job, so a remounted panel can resume polling it."""
        for job_id, job in reversed(list(self.jobs.items())):
            if job["state"] in ("queued", "running"):
                return {"job_id": job_id, "job": copy.deepcopy(job)}
        return None

    async def get_recent_statuses(self) -> list[dict[str, Any]]:
        # No transfer URLs/tokens or source contents are retained.
        return [dict(copy.deepcopy(job), id=job_id)
                for job_id, job in list(self.jobs.items())[-5:][::-1]]

    def _save_history(self):
        try:
            save_history(getattr(self, "history_dir", None), self.jobs)
        except (OSError, ValueError):
            # Diagnostics storage failure must not turn a successful export into
            # a failed one (especially when the disk is full).
            decky.logger.exception("Could not persist ClipPort status history")

    async def _run_export(self, job_id: str, requested: list[tuple[dict[str, Any], str | None]]):
        job = self.jobs[job_id]
        job["state"] = "running"
        try:
            if find_ffmpeg() is None:
                raise RuntimeError(
                    "FFmpeg was not found. ClipPort looks for ~/.local/bin/ffmpeg and then ffmpeg on the "
                    "system PATH. See the FFmpeg section of the ClipPort README for setup steps.")
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            for index, (clip, rename) in enumerate(requested):
                await self._export_one(job, index, clip, rename)
            job["progress"] = 100.0
            job["state"] = "complete"
        except asyncio.CancelledError:
            job["state"] = "cancelled"
            job["error"] = "Export cancelled while the plugin was unloading."
            for item in job["clips"]:
                if item["state"] in ("queued", "exporting"):
                    item["state"] = "cancelled"
            raise
        except Exception as error:
            decky.logger.exception("ClipPort export failed")
            job["state"] = "failed"
            job["error"] = str(error) or type(error).__name__
            job["details"] = traceback.format_exc()
            for item in job["clips"]:
                if item["state"] == "exporting":
                    item.update(state="failed", error=job["error"])
                elif item["state"] == "queued":
                    item["state"] = "not started"
            try:
                job["free_bytes"] = shutil.disk_usage(OUTPUT_DIR).free
            except OSError:
                pass
        finally:
            job["finished_at"] = datetime.now(timezone.utc).isoformat()
            self._save_history()

    async def _export_one(self, job: dict[str, Any], index: int, clip: dict[str, Any], rename: str | None):
        item = job["clips"][index]
        item["state"] = "exporting"
        item["stage"] = "Finding recording sessions"
        sessions = sorted_sessions(Path(clip["id"]))
        if not sessions:
            raise RuntimeError(f"No session manifest found for {clip['game_name']}")
        item["stage"] = "Checking the recording is complete"
        for session in sessions:
            def warn(message: str, session=session):
                _log("warning", "ClipPort: %s: %s (exporting anyway)", session.parent, message)
            try:
                validate_recording(session.parent, session, warn)
            except IncompleteRecording as error:
                raise RuntimeError(
                    f"{clip['game_name']}: recording looks incomplete ({error}). "
                    "If Steam is still saving it, wait a few seconds and try again.") from error
        output = OUTPUT_DIR / _safe_output_name(rename, clip)
        # Keep staging beside output for atomic, no-overwrite hard-link publication.
        # Source recording directories are never written.
        with tempfile.TemporaryDirectory(prefix=".deckclip-", dir=OUTPUT_DIR) as temp_name:
            temp = Path(temp_name)
            parts = []
            for session_index, session in enumerate(sessions):
                item["stage"] = f"Assembling/remuxing session {session_index + 1} of {len(sessions)}"
                part = temp / f"part-{session_index:03d}.mp4"
                await self._remux_session(
                    session,
                    part,
                    lambda percent, base=session_index: self._set_progress(
                        job, index, (base + percent / 100) / len(sessions) * 90
                    ),
                    lambda stage, number=session_index + 1: item.update(
                        stage=f"Session {number}/{len(sessions)}: {stage}"),
                )
                parts.append(part)
            if len(parts) == 1:
                item["stage"] = "Saving MP4"
                staged = parts[0]
            else:
                item["stage"] = "Combining recording sessions"
                concat = temp / "concat.txt"
                concat.write_text("".join(f"file '{part.as_posix()}'\n" for part in parts))
                staged = temp / "complete.mp4"
                await self._run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-protocol_whitelist", "file,pipe", "-i", str(concat), "-map", "0", "-c", "copy", "-movflags", "+faststart", str(staged)])
                # Free the per-session parts now rather than when staging is removed.
                for part in parts:
                    part.unlink(missing_ok=True)
                concat.unlink(missing_ok=True)
            output = publish_export(staged, output)
        item.update({"progress": 100.0, "state": "complete", "stage": "Saved", "output": str(output)})
        job["progress"] = sum(entry["progress"] for entry in job["clips"]) / len(job["clips"])

    def _set_progress(self, job: dict[str, Any], index: int, value: float):
        job["clips"][index]["progress"] = min(99.0, value)
        job["progress"] = sum(entry["progress"] for entry in job["clips"]) / len(job["clips"])

    async def _remux_session(self, manifest: Path, output: Path, progress, stage=lambda value: None):
        stage("Reading fragment list")
        streams = _stream_files(manifest.parent)
        if not streams or streams[0][0] != 0:
            raise RuntimeError(f"No complete video fragment stream found beside {manifest.name}")

        total_bytes = sum(path.stat().st_size for _, files in streams for path in files)
        duration = _duration_from_mpd(manifest) or 0
        copied_bytes = 0
        media_percent = 0.0

        def update():
            byte_percent = copied_bytes / total_bytes * 100 if total_bytes else 0.0
            # Fragments are fed and remuxed at the same time; report the slower.
            progress(min(byte_percent, media_percent) if duration else byte_percent)

        def on_bytes(count: int):
            nonlocal copied_bytes
            copied_bytes += count
            update()

        def report(line: str):
            nonlocal media_percent
            if line.startswith("out_time_ms=") and duration:
                try:
                    media_percent = min(100.0, int(line.split("=", 1)[1]) / 1_000_000 / duration * 100)
                except ValueError:
                    return
                update()

        stage(f"Remuxing {len(streams)} stream(s) with FFmpeg")
        await self._remux_streams(streams, output, on_bytes, report)
        progress(100.0)

    async def _remux_streams(self, streams, output: Path, on_bytes, on_progress):
        """Pipe each stream's fragments straight into FFmpeg.

        Nothing but the output MP4 is written, so a session needs about 1x its
        size in free space (previously the joined stream copies made it 2x).
        """
        loop = asyncio.get_running_loop()
        stop = threading.Event()
        pipes: list[tuple[int, int]] = []
        open_fds: set[int] = set()
        feeders: list[asyncio.Future] = []
        results: list[Any] = []

        def report(count: int):
            if not stop.is_set():
                loop.call_soon_threadsafe(on_bytes, count)

        def started():
            # Only FFmpeg may hold the read ends, or a dead FFmpeg would leave
            # feeders blocked instead of seeing a broken pipe.
            for read_fd, write_fd in pipes:
                os.close(read_fd)
                open_fds.discard(read_fd)
            for (_, write_fd), (_, files) in zip(pipes, streams):
                open_fds.discard(write_fd)  # feed_fragments now owns it
                feeders.append(asyncio.ensure_future(asyncio.to_thread(feed_fragments, files, write_fd, report, stop)))

        try:
            for _ in streams:
                read_fd, write_fd = os.pipe()
                pipes.append((read_fd, write_fd))
                open_fds.update((read_fd, write_fd))
            command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
            for read_fd, _ in pipes:
                command.extend(["-protocol_whitelist", "file,pipe", "-i", f"pipe:{read_fd}"])
            command.extend(["-map", "0:v:0"])
            for input_index in range(1, len(pipes)):
                command.extend(["-map", f"{input_index}:a:0?"])
            command.extend(["-c", "copy", "-movflags", "+faststart", "-progress", "pipe:1", str(output)])
            try:
                await run_ffmpeg(command, on_progress, pass_fds=[read_fd for read_fd, _ in pipes], on_started=started)
            except BaseException:
                stop.set()
                raise
        finally:
            for fd in open_fds:
                os.close(fd)
            if feeders:
                results = await asyncio.gather(*feeders, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise RuntimeError(f"Could not read recording fragments: {result}") from result

    async def _run(self, command: list[str]):
        await run_ffmpeg(command)
