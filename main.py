from __future__ import annotations

import asyncio
import copy
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import decky
from backend.exports import export_path, list_exports, safe_output_name as _safe_output_name
from backend.library import (
    APPINFO_V41_MAGIC,
    _app_names,
    _appinfo_names_from_data,
    _discover,
    _duration_from_mpd,
    _shortcut_names,
)
from backend.media import join_fragments as _join_fragments, stream_files as _stream_files
from backend.qr import _qr_matrix
from backend.transfer import TransferManager, _lan_address


OUTPUT_DIR = Path("/home/deck/Videos/DeckClip")


def _export_path(filename: str) -> Path:
    return export_path(OUTPUT_DIR, filename)


def _list_exports() -> list[dict[str, Any]]:
    return list_exports(OUTPUT_DIR)


class Plugin:
    async def _main(self):
        self.jobs: dict[str, dict[str, Any]] = {}
        self.tasks: set[asyncio.Task] = set()
        self.transfers = TransferManager(_export_path, decky.logger)
        decky.logger.info("DeckClip loaded")

    async def _unload(self):
        await self.transfers._stop_transfer()
        for task in self.tasks:
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)

    async def list_clips(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(_discover)

    async def list_exports(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(_list_exports)

    async def trash_export(self, filename: str) -> dict[str, str]:
        path = await asyncio.to_thread(_export_path, filename)
        gio = shutil.which("gio")
        if gio is None:
            raise RuntimeError("The system Trash service is unavailable. Delete this file in Desktop Mode.")
        process = await asyncio.create_subprocess_exec(
            gio, "trash", "--", str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            detail = stderr.decode(errors="replace").strip()
            raise RuntimeError(detail or "Could not move the export to Trash")
        return {"filename": filename}

    @property
    def transfer(self):
        return self.transfers.transfer

    @transfer.setter
    def transfer(self, value):
        self.transfers.transfer = value

    async def start_transfer(self, filename: str) -> dict[str, Any]:
        return await self.transfers.start_transfer(filename)

    async def get_transfer_status(self) -> dict[str, Any]:
        return await self.transfers.get_transfer_status()

    async def stop_transfer(self) -> dict[str, str]:
        return await self.transfers.stop_transfer()

    async def _handle_transfer_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        return await self.transfers._handle_transfer_client(reader, writer)

    async def start_export(self, items: list[dict[str, str]]) -> dict[str, str]:
        available = {clip["id"]: clip for clip in await self.list_clips()}
        if not items:
            raise ValueError("Select at least one clip")
        requested = []
        for item in items:
            clip = available.get(item.get("id", ""))
            if clip is None:
                raise ValueError("A selected clip is no longer available")
            requested.append((clip, item.get("name")))
        job_id = uuid.uuid4().hex
        self.jobs[job_id] = {
            "state": "queued", "progress": 0.0, "output_dir": str(OUTPUT_DIR),
            "clips": [{"id": clip["id"], "display_name": clip["game_name"], "progress": 0.0, "state": "queued"} for clip, _ in requested],
        }
        task = asyncio.create_task(self._run_export(job_id, requested))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return {"job_id": job_id}

    async def get_export_status(self, job_id: str) -> dict[str, Any]:
        if job_id not in self.jobs:
            raise ValueError("Unknown export job")
        return copy.deepcopy(self.jobs[job_id])

    async def _run_export(self, job_id: str, requested: list[tuple[dict[str, Any], str | None]]):
        job = self.jobs[job_id]
        job["state"] = "running"
        try:
            if shutil.which("ffmpeg") is None:
                raise RuntimeError("FFmpeg was not found. Install the Decky FFmpeg binary dependency before exporting.")
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            for index, (clip, rename) in enumerate(requested):
                await self._export_one(job, index, clip, rename)
            job["progress"] = 100.0
            job["state"] = "complete"
        except asyncio.CancelledError:
            job["state"] = "cancelled"
            raise
        except Exception as error:
            decky.logger.exception("DeckClip export failed")
            job["state"] = "failed"
            job["error"] = str(error)

    async def _export_one(self, job: dict[str, Any], index: int, clip: dict[str, Any], rename: str | None):
        item = job["clips"][index]
        item["state"] = "exporting"
        sessions = sorted(Path(clip["id"]).glob("video/**/session.mpd"), key=lambda p: p.stat().st_mtime)
        if not sessions:
            raise RuntimeError(f"No session manifest found for {clip['game_name']}")
        output = OUTPUT_DIR / _safe_output_name(rename, clip)
        counter = 2
        while output.exists():
            output = output.with_name(f"{output.stem} ({counter}).mp4")
            counter += 1
        # Keep temporary output beside the destination so the final atomic rename
        # cannot cross filesystems. Source recording directories are never written.
        with tempfile.TemporaryDirectory(prefix=".deckclip-", dir=OUTPUT_DIR) as temp_name:
            temp = Path(temp_name)
            parts = []
            for session_index, session in enumerate(sessions):
                part = temp / f"part-{session_index:03d}.mp4"
                await self._remux_session(
                    session,
                    part,
                    lambda percent, base=session_index: self._set_progress(
                        job, index, (base + percent / 100) / len(sessions) * 90
                    ),
                )
                parts.append(part)
            if len(parts) == 1:
                os.replace(parts[0], output)
            else:
                concat = temp / "concat.txt"
                concat.write_text("".join(f"file '{part.as_posix()}'\n" for part in parts))
                await self._run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-map", "0", "-c", "copy", "-movflags", "+faststart", str(output)])
        item.update({"progress": 100.0, "state": "complete", "output": str(output)})
        job["progress"] = sum(entry["progress"] for entry in job["clips"]) / len(job["clips"])

    def _set_progress(self, job: dict[str, Any], index: int, value: float):
        job["clips"][index]["progress"] = min(99.0, value)
        job["progress"] = sum(entry["progress"] for entry in job["clips"]) / len(job["clips"])

    async def _remux_session(self, manifest: Path, output: Path, progress):
        streams = _stream_files(manifest.parent)
        if not streams or streams[0][0] != 0:
            raise RuntimeError(f"No complete video fragment stream found beside {manifest.name}")

        total_bytes = sum(path.stat().st_size for _, files in streams for path in files)
        copied_bytes = 0

        def on_bytes(count: int):
            nonlocal copied_bytes
            copied_bytes += count
            if total_bytes:
                progress(copied_bytes / total_bytes * 70.0)

        assembled: list[Path] = []
        for stream_id, files in streams:
            stream_file = output.parent / f"{output.stem}-stream{stream_id}.mp4"
            await asyncio.to_thread(_join_fragments, files, stream_file, on_bytes)
            assembled.append(stream_file)

        command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
        for stream_file in assembled:
            command.extend(["-i", str(stream_file)])
        command.extend(["-map", "0:v:0"])
        for input_index in range(1, len(assembled)):
            command.extend(["-map", f"{input_index}:a:0?"])
        command.extend([
            "-c", "copy", "-movflags", "+faststart", "-progress", "pipe:1", str(output)
        ])

        duration = _duration_from_mpd(manifest) or 0
        process = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdout is not None
        async for raw in process.stdout:
            line = raw.decode(errors="replace").strip()
            if line.startswith("out_time_ms=") and duration:
                media_percent = int(line.split("=", 1)[1]) / 1_000_000 / duration * 100
                progress(70.0 + min(100.0, media_percent) * 0.3)
        stderr = (await process.stderr.read()).decode(errors="replace") if process.stderr else ""
        if await process.wait() != 0:
            raise RuntimeError(stderr.strip() or f"FFmpeg could not remux {manifest.parent.name}")
        progress(100.0)

    async def _run(self, command: list[str]):
        process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(stderr.decode(errors="replace").strip() or "FFmpeg concat failed")
