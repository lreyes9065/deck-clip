"""Where the Deck user's files live. Resolving this never raises."""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_HOME = Path("/home/deck")


def _candidates():
    # 1. The variable Decky sets for every plugin process.
    yield os.environ.get("DECKY_USER_HOME")
    # 2. Decky's helper module (it mirrors the variable when it is set).
    try:
        import decky
    except ImportError:
        decky = None
    yield getattr(decky, "DECKY_USER_HOME", None)
    user = getattr(decky, "DECKY_USER", None) or os.environ.get("DECKY_USER")
    if isinstance(user, str) and user:
        try:
            import pwd
            yield pwd.getpwnam(user).pw_dir
        except (ImportError, KeyError, OSError):
            pass
    # 3. This process's own home, unless it is root's.
    yield os.environ.get("HOME")
    # 4. SteamOS's only user.
    yield DEFAULT_HOME


def _usable(value) -> Path | None:
    if not isinstance(value, (str, os.PathLike)):
        return None
    text = os.fspath(value).strip()
    if not text:
        return None
    path = Path(text)
    # Empty, relative, "/" and "/root" never hold the Deck user's Steam data.
    if not path.is_absolute() or path == Path(path.anchor) or path == Path("/root"):
        return None
    return path


def _is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def user_home() -> Path:
    """The Deck user's home: the first usable candidate that exists.

    Order: ``DECKY_USER_HOME`` from the environment, ``decky.DECKY_USER_HOME``,
    the home of ``DECKY_USER``, ``$HOME`` (never ``/root``), then ``/home/deck``.
    When none exists yet, the first usable candidate is returned.
    """
    usable = []
    for candidate in _candidates():
        path = _usable(candidate)
        if path is not None and path not in usable:
            usable.append(path)
    for path in usable:
        if _is_dir(path):
            return path
    return usable[0] if usable else DEFAULT_HOME
