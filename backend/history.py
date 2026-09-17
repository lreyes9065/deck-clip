"""Small, private, atomic status history. Never stores transfer URLs/tokens."""
import json
import os
import tempfile
from pathlib import Path

MAX_HISTORY_BYTES = 4 * 1024 * 1024


def load_history(directory: Path | None):
    if directory is None:
        return {}
    try:
        with (directory / "recent-exports.json").open("rb") as stream:
            raw = stream.read(MAX_HISTORY_BYTES + 1)
        if len(raw) > MAX_HISTORY_BYTES:
            return {}
        entries = json.loads(raw)
        if not isinstance(entries, list) or len(entries) > 5:
            return {}
        result = {}
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
                return {}
            if not all(isinstance(entry.get(key), str) for key in ("state", "started_at", "output_dir")):
                return {}
            if not isinstance(entry.get("progress"), (int, float)) or not isinstance(entry.get("clips"), list):
                return {}
            if any(key in entry and not isinstance(entry[key], str) for key in ("error", "details", "finished_at")):
                return {}
            if "free_bytes" in entry and not isinstance(entry["free_bytes"], (int, float)):
                return {}
            if len(entry["clips"]) > 100 or any(not isinstance(clip, dict) or not all(isinstance(clip.get(key), str) for key in ("id", "display_name", "state")) or not isinstance(clip.get("progress"), (int, float)) for clip in entry["clips"]):
                return {}
            if any(any(key in clip and not isinstance(clip[key], str) for key in ("stage", "output", "error")) for clip in entry["clips"]):
                return {}
            job_id = entry.pop("id")
            if entry["state"] in ("queued", "running"):
                entry.update(state="interrupted", error="DeckClip restarted before this export finished. Check your exports before retrying.")
                for clip in entry["clips"]:
                    if clip["state"] in ("queued", "exporting"):
                        clip["state"] = "interrupted"
            result[job_id] = entry
        return result
    except (OSError, ValueError, TypeError):
        return {}


def save_history(directory: Path | None, jobs):
    if directory is None:
        return
    # Only export-job data enters here. No environment dumps or network state.
    entries = [dict(job, id=job_id) for job_id, job in list(jobs.items())[-5:]]
    payload = json.dumps(entries, ensure_ascii=False).encode("utf-8")
    if len(payload) > MAX_HISTORY_BYTES:
        raise ValueError("Status history exceeds its safety limit")
    directory.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".recent-exports-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, directory / "recent-exports.json")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
