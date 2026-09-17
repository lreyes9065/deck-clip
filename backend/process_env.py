"""Environment for system tools launched from Decky's bundled Python runtime."""

import os


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
