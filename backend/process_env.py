"""Environment and lookup for FFmpeg launched from Decky's bundled Python runtime."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def find_ffmpeg() -> str | None:
    """Return the FFmpeg executable ClipPort should run, or None.

    SteamOS does not guarantee an ``ffmpeg`` command, and its system image is
    read-only. A user-installed static build in ``~/.local/bin/ffmpeg`` is
    preferred (Decky's process PATH does not usually include that folder),
    then whatever ``ffmpeg`` is on PATH. Always an absolute path when found.
    """
    home = os.environ.get("DECKY_USER_HOME") or str(Path.home())
    candidate = Path(home) / ".local" / "bin" / "ffmpeg"
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    return shutil.which("ffmpeg")


def system_tool_env() -> dict[str, str]:
    """Undo PyInstaller's library override for a child, never the parent.

    System FFmpeg must use system libraries rather than Decky's _MEI bundle.
    PyInstaller preserves any pre-existing search path in LD_LIBRARY_PATH_ORIG.
    With no nonempty original, omit the override and use the loader defaults.
    """
    env = os.environ.copy()
    original = env.get("LD_LIBRARY_PATH_ORIG")
    if original:
        env["LD_LIBRARY_PATH"] = original
    else:
        env.pop("LD_LIBRARY_PATH", None)
    return env
