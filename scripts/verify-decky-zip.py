#!/usr/bin/env python3
"""Validate the shape and safety of a DeckClip release archive."""

from __future__ import annotations

import json
import stat
import sys
import zipfile
from pathlib import PurePosixPath


REQUIRED = {
    "DeckClip/package.json",
    "DeckClip/plugin.json",
    "DeckClip/main.py",
    "DeckClip/backend/__init__.py",
    "DeckClip/backend/exports.py",
    "DeckClip/backend/library.py",
    "DeckClip/backend/media.py",
    "DeckClip/backend/qr.py",
    "DeckClip/backend/transfer.py",
    "DeckClip/backend/thumbnails.py",
    "DeckClip/backend/process_env.py",
    "DeckClip/backend/processes.py",
    "DeckClip/backend/history.py",
    "DeckClip/dist/index.js",
    "DeckClip/docs/iphone-shortcut.md",
    "DeckClip/docs/release-checklist.md",
    "DeckClip/docs/code-walkthrough.md",
    "DeckClip/docs/security-review-2026-09-12.md",
    "DeckClip/LICENSE",
}


def verify(path: str) -> None:
    with zipfile.ZipFile(path) as archive:
        files = {name for name in archive.namelist() if not name.endswith("/")}
        missing = REQUIRED - files
        if missing:
            raise SystemExit(f"Invalid Decky ZIP; missing: {', '.join(sorted(missing))}")
        unexpected = files - REQUIRED - {"DeckClip/README.md", "DeckClip/THIRD_PARTY_NOTICES.md"}
        if unexpected:
            raise SystemExit(f"Unexpected files in release ZIP: {', '.join(sorted(unexpected))}")
        if len(files) != len([entry for entry in archive.infolist() if not entry.is_dir()]):
            raise SystemExit("Duplicate files in release ZIP")

        for name in files:
            if stat.S_ISLNK(archive.getinfo(name).external_attr >> 16):
                raise SystemExit(f"Symlink in release ZIP: {name}")
            member = PurePosixPath(name)
            if member.is_absolute() or ".." in member.parts:
                raise SystemExit(f"Unsafe archive path: {name}")
            if not member.parts or member.parts[0] != "DeckClip":
                raise SystemExit(f"File outside DeckClip folder: {name}")

        plugin = json.loads(archive.read("DeckClip/plugin.json"))
        package = json.loads(archive.read("DeckClip/package.json"))
        if plugin.get("name") != "Decky ClipPort":
            raise SystemExit("plugin.json name must be Decky ClipPort")
        if not package.get("version"):
            raise SystemExit("package.json must contain a version")

    print(f"Verified Decky archive: {path}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(f"Usage: {sys.argv[0]} ARCHIVE.zip")
    verify(sys.argv[1])
