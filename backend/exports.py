"""Naming and safe access for files in DeckClip's dedicated output directory."""

import datetime as dt
import re
import os
import stat
from pathlib import Path
from typing import Any

SAFE_NAME_RE = re.compile(r"[^\w .()\[\]-]+", re.UNICODE)


def open_export(path: Path):
    """Open a direct regular child without following a replaced final symlink."""
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("Export is no longer a regular file")
            return os.fdopen(fd, "rb")
        except BaseException:
            os.close(fd)
            raise
    finally:
        os.close(directory)


def file_identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def publish_export(staged: Path, output: Path) -> Path:
    """Atomically publish complete output without overwriting any existing name.

    Staging is on the same filesystem. Hard-link creation either succeeds or
    reports a collision; there is no check-then-replace window.
    """
    with staged.open("rb") as stream:
        if os.fstat(stream.fileno()).st_size == 0:
            raise ValueError("FFmpeg produced an empty export")
        os.fsync(stream.fileno())
    for number in range(1, 10001):
        candidate = output if number == 1 else output.with_name(f"{output.stem} ({number}).mp4")
        try:
            os.link(staged, candidate, follow_symlinks=False)
            return candidate
        except FileExistsError:
            continue
    raise RuntimeError("Too many exports with this name; choose another filename")


def delete_exports(output_dir: Path, filenames: list[str]) -> dict[str, Any]:
    """Delete only explicitly selected regular MP4 children, never recursively."""
    if not isinstance(filenames, list) or not filenames or len(filenames) > 100:
        raise ValueError("Select between 1 and 100 exports")
    for name in filenames:
        export_path(output_dir, name)
    deleted, failed = [], []
    # Pin the directory so a replaced parent path cannot redirect deletion.
    descriptor = os.open(output_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name in dict.fromkeys(filenames):
            try:
                info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError("Export is no longer a regular file")
                os.unlink(name, dir_fd=descriptor)
                deleted.append(name)
            except (OSError, ValueError):
                failed.append(name)
    finally:
        os.close(descriptor)
    return {"deleted": deleted, "failed": failed}


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
