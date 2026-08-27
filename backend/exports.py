"""Naming and safe access for files in DeckClip's dedicated output directory."""

import datetime as dt
import re
from pathlib import Path
from typing import Any

SAFE_NAME_RE = re.compile(r"[^\w .()\[\]-]+", re.UNICODE)


def safe_output_name(requested: str | None, clip: dict[str, Any]) -> str:
    default = f"{clip['game_name']} - {dt.datetime.fromisoformat(clip['recorded_at']).strftime('%Y-%m-%d %H-%M-%S')}"
    name = SAFE_NAME_RE.sub("_", (requested or default).strip()).strip(" ._")[:160] or "Steam clip"
    if name.lower().endswith(".mp4"):
        name = name[:-4].rstrip(" .")
    return name + ".mp4"


def export_path(output_dir: Path, filename: str) -> Path:
    """Resolve one direct MP4 child without accepting traversal or links."""
    if not isinstance(filename, str) or not filename or Path(filename).name != filename:
        raise ValueError("Invalid exported filename")
    if not filename.lower().endswith(".mp4"):
        raise ValueError("Only exported MP4 files can be managed")
    candidate = output_dir / filename
    try:
        if candidate.is_symlink() or not candidate.is_file():
            raise ValueError("Exported file no longer exists")
        if candidate.resolve().parent != output_dir.resolve():
            raise ValueError("Exported file is outside the DeckClip folder")
    except OSError as error:
        raise ValueError("Could not inspect exported file") from error
    return candidate


def list_exports(output_dir: Path) -> list[dict[str, Any]]:
    if not output_dir.is_dir():
        return []
    try:
        candidates = list(output_dir.iterdir())
    except OSError:
        return []
    exports = []
    for candidate in candidates:
        try:
            path = export_path(output_dir, candidate.name)
            stat = path.stat()
        except (OSError, ValueError):
            continue
        exports.append({
            "filename": path.name,
            "size_bytes": stat.st_size,
            "modified_at": dt.datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(),
        })
    return sorted(exports, key=lambda item: item["modified_at"], reverse=True)
