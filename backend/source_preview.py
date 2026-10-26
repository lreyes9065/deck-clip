"""Bounded preview input assembled read-only from a known Steam clip."""
import os
import stat
import tempfile
from pathlib import Path

from .media import stream_files

MAX_PREVIEW_INPUT = 16 * 1024 * 1024


def open_source_preview(clip: Path):
    # A preview needs only the initial video fragments, not audio or the full clip.
    sessions = sorted(clip.glob("video/**/session.mpd"), key=lambda path: path.stat().st_mtime)
    if not sessions:
        raise ValueError("No recording session")
    streams = stream_files(sessions[0].parent)
    if not streams or streams[0][0] != 0:
        raise ValueError("No video fragments")
    files = streams[0][1][:4]  # init plus at most three chunks
    output = tempfile.TemporaryFile()
    root = None
    try:
        root = os.open(clip, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        remaining = MAX_PREVIEW_INPUT
        for path in files:
            parts = path.relative_to(clip).parts
            directory = os.dup(root)
            try:
                for part in parts[:-1]:
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                    os.close(directory)
                    directory = child
                fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                with os.fdopen(fd, "rb") as source:
                    if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                        raise ValueError("Not a regular recording fragment")
                    data = source.read(remaining + 1)
                    if len(data) > remaining:
                        raise ValueError("Preview input exceeds limit")
                    output.write(data)
                    remaining -= len(data)
            finally:
                os.close(directory)
        output.seek(0)
        return output
    except BaseException:
        output.close()
        raise
    finally:
        if root is not None:
            os.close(root)
