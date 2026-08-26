from __future__ import annotations

import asyncio
import copy
import datetime as dt
import json
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

import decky


OUTPUT_DIR = Path("/home/deck/Videos/DeckClip")
CLIP_RE = re.compile(r"^clip_(?P<app>\d+|downloaded)_(?P<date>\d{8})_(?P<time>\d{6})")
SAFE_NAME_RE = re.compile(r"[^\w .()\[\]-]+", re.UNICODE)


def _steam_roots() -> list[Path]:
    override = os.environ.get("DECKCLIP_STEAM_ROOT")
    if override:
        return [Path(override)]
    home = Path(getattr(decky, "DECKY_USER_HOME", "/home/deck"))
    candidates = [
        home / ".local/share/Steam",
        home / ".steam/steam",
        home / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
    ]
    return list(dict.fromkeys(path.resolve() for path in candidates if path.exists()))


def _parse_timestamp(name: str, fallback: float) -> dt.datetime:
    match = CLIP_RE.match(name)
    if match:
        try:
            return dt.datetime.strptime(match.group("date") + match.group("time"), "%Y%m%d%H%M%S").astimezone()
        except ValueError:
            pass
    return dt.datetime.fromtimestamp(fallback).astimezone()


def _duration_from_mpd(path: Path) -> float | None:
    try:
        # The MPD duration is an attribute on the opening element. Reading only
        # the header is quicker than parsing a manifest containing many segments.
        header = path.read_text(errors="replace")[:65536]
        attribute = re.search(r'\bmediaPresentationDuration\s*=\s*["\']([^"\']+)["\']', header)
        if not attribute:
            return None
        value = attribute.group(1)
        match = re.fullmatch(r"PT(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?", value)
        if not match:
            return None
        hours, minutes, seconds = (float(part or 0) for part in match.groups())
        return hours * 3600 + minutes * 60 + seconds
    except OSError:
        return None


def _app_names(roots: list[Path]) -> dict[str, str]:
    names: dict[str, str] = {}
    pattern = re.compile(r'"(appid|name)"\s+"([^"]*)"')
    for root in roots:
        for manifest in (root / "steamapps").glob("appmanifest_*.acf"):
            try:
                fields = dict(pattern.findall(manifest.read_text(errors="replace")))
                if fields.get("appid") and fields.get("name"):
                    names[fields["appid"]] = fields["name"]
            except OSError:
                continue
    return names


def _discover(limit: int = 3) -> list[dict[str, Any]]:
    roots = _steam_roots()
    app_names = _app_names(roots)
    found: list[tuple[float, Path]] = []
    for root in roots:
        userdata = root / "userdata"
        if not userdata.is_dir():
            continue
        for clips_dir in userdata.glob("*/gamerecordings/clips"):
            for clip_dir in clips_dir.glob("clip_*"):
                if clip_dir.is_dir():
                    try:
                        found.append((clip_dir.stat().st_mtime, clip_dir.resolve()))
                    except OSError:
                        continue
    results = []
    for modified, clip_dir in sorted(found, reverse=True):
        sessions = sorted(clip_dir.glob("video/**/session.mpd"), key=lambda p: p.stat().st_mtime)
        if not sessions:
            continue
        match = CLIP_RE.match(clip_dir.name)
        app_id = match.group("app") if match else "unknown"
        recorded = _parse_timestamp(clip_dir.name, modified)
        durations = [_duration_from_mpd(session) for session in sessions]
        total = sum(value for value in durations if value is not None) if any(value is not None for value in durations) else None
        results.append({
            "id": str(clip_dir),
            "app_id": app_id,
            "game_name": app_names.get(app_id, f"Steam app {app_id}" if app_id.isdigit() else "Steam clip"),
            "recorded_at": recorded.isoformat(),
            "duration_seconds": total,
            "session_count": len(sessions),
        })
        if len(results) == limit:
            break
    return results


def _safe_output_name(requested: str | None, clip: dict[str, Any]) -> str:
    default = f"{clip['game_name']} - {dt.datetime.fromisoformat(clip['recorded_at']).strftime('%Y-%m-%d %H-%M-%S')}"
    name = SAFE_NAME_RE.sub("_", (requested or default).strip()).strip(" ._")[:160] or "Steam clip"
    if name.lower().endswith(".mp4"):
        name = name[:-4].rstrip(" .")
    return name + ".mp4"


class Plugin:
    async def _main(self):
        self.jobs: dict[str, dict[str, Any]] = {}
        self.tasks: set[asyncio.Task] = set()
        decky.logger.info("DeckClip loaded")

    async def _unload(self):
        for task in self.tasks:
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)

    async def list_clips(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(_discover, 3)

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
                await self._ffmpeg(session, part, lambda percent, base=session_index: self._set_progress(job, index, (base + percent / 100) / len(sessions) * 90))
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

    async def _ffmpeg(self, source: Path, output: Path, progress):
        duration = _duration_from_mpd(source) or 0
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
            "-map", "0:v:0", "-map", "0:a?", "-c", "copy", "-movflags", "+faststart",
            "-progress", "pipe:1", str(output), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdout is not None
        async for raw in process.stdout:
            line = raw.decode(errors="replace").strip()
            if line.startswith("out_time_ms=") and duration:
                progress(min(100.0, int(line.split("=", 1)[1]) / 1_000_000 / duration * 100))
        stderr = (await process.stderr.read()).decode(errors="replace") if process.stderr else ""
        if await process.wait() != 0:
            raise RuntimeError(stderr.strip() or f"FFmpeg could not read {source.name}")
        progress(100.0)

    async def _run(self, command: list[str]):
        process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(stderr.decode(errors="replace").strip() or "FFmpeg concat failed")
