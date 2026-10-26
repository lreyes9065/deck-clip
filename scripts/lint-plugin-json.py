#!/usr/bin/env python3
"""Lint plugin.json (and package.json) before a release.

Fails on flags that must not ship: "debug" (Decky hot-reloads and keeps
extra logging), "root" unless --allow-root is passed on purpose, and the
"_root" typo, which Decky ignores. Also checks the fields Decky and the
plugin store read.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
KNOWN_FLAGS = {"root", "debug"}


def lint(plugin: dict, package: dict, *, allow_root: bool = False) -> list[str]:
    problems = []
    flags = plugin.get("flags", [])
    if not isinstance(flags, list) or not all(isinstance(flag, str) for flag in flags):
        problems.append("plugin.json flags must be a list of strings")
        flags = []
    if "debug" in flags:
        problems.append('plugin.json has the "debug" flag; remove it before release')
    if "root" in flags and not allow_root:
        problems.append('plugin.json has the "root" flag; ClipPort runs as the deck user '
                        "(pass --allow-root only if root is really needed)")
    if "_root" in flags:
        problems.append('plugin.json has "_root", which Decky ignores; use "root" or nothing')
    for flag in flags:
        if flag not in KNOWN_FLAGS | {"_root"}:
            problems.append(f"plugin.json has unknown flag {flag!r}")
    for key in ("name", "author"):
        if not isinstance(plugin.get(key), str) or not plugin[key].strip():
            problems.append(f"plugin.json needs a non-empty {key!r}")
    if plugin.get("api_version") != 1:
        problems.append("plugin.json api_version must be 1 (the @decky/api callable API)")
    publish = plugin.get("publish")
    if not isinstance(publish, dict) or not publish.get("description") or not isinstance(publish.get("tags"), list):
        problems.append("plugin.json publish needs a description and a tags list")
    version = package.get("version")
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", version):
        problems.append("package.json version must be semver, e.g. 1.2.3")
    if not package.get("license"):
        problems.append("package.json needs a license")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--allow-root", action="store_true")
    parser.add_argument("--plugin", default=str(PROJECT / "plugin.json"))
    parser.add_argument("--package", default=str(PROJECT / "package.json"))
    args = parser.parse_args(argv)
    problems = lint(json.loads(Path(args.plugin).read_text()), json.loads(Path(args.package).read_text()),
                    allow_root=args.allow_root)
    if not (PROJECT / "LICENSE").is_file():
        problems.append("LICENSE is missing")
    for problem in problems:
        print(f"plugin.json lint: {problem}", file=sys.stderr)
    if not problems:
        print("plugin.json lint: ok")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
