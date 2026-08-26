#!/usr/bin/env python3
"""Validate the shape and safety of a DeckClip release archive."""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import PurePosixPath


REQUIRED = {
    "DeckClip/package.json",
    "DeckClip/plugin.json",
    "DeckClip/main.py",
    "DeckClip/dist/index.js",
    "DeckClip/LICENSE",
}


def verify(path: str) -> None:
    with zipfile.ZipFile(path) as archive:
        files = {name for name in archive.namelist() if not name.endswith("/")}
        missing = REQUIRED - files
        if missing:
            raise SystemExit(f"Invalid Decky ZIP; missing: {', '.join(sorted(missing))}")

        for name in files:
            member = PurePosixPath(name)
            if member.is_absolute() or ".." in member.parts:
                raise SystemExit(f"Unsafe archive path: {name}")
            if not member.parts or member.parts[0] != "DeckClip":
                raise SystemExit(f"File outside DeckClip folder: {name}")

        plugin = json.loads(archive.read("DeckClip/plugin.json"))
        package = json.loads(archive.read("DeckClip/package.json"))
        if plugin.get("name") != "DeckClip":
            raise SystemExit("plugin.json name must be DeckClip")
        if not package.get("version"):
            raise SystemExit("package.json must contain a version")

    print(f"Verified Decky archive: {path}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(f"Usage: {sys.argv[0]} ARCHIVE.zip")
    verify(sys.argv[1])
