"""Read-only Steam recording discovery and game-name resolution."""

import datetime as dt
import json
import mmap
import os
import re
import struct
from pathlib import Path
from typing import Any

import decky

CLIP_RE = re.compile(r"^clip_(?P<app>\d+|downloaded)_(?P<date>\d{8})_(?P<time>\d{6})")
APPINFO_V41_MAGIC = b"\x29\x44\x56\x07"

def _steam_roots() -> list[Path]:
    override = os.environ.get("DECKCLIP_STEAM_ROOT")
    if override:
        return [Path(override)]
    home = Path(getattr(decky, "DECKY_USER_HOME", "/home/deck"))
    candidates = [
        home / ".local/share/Steam",
        home / ".steam/steam",
        home / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
    ]
    return list(dict.fromkeys(path.resolve() for path in candidates if path.exists()))


def _parse_timestamp(name: str, fallback: float) -> dt.datetime:
    match = CLIP_RE.match(name)
    if match:
        try:
            return dt.datetime.strptime(match.group("date") + match.group("time"), "%Y%m%d%H%M%S").astimezone()
        except ValueError:
            pass
    return dt.datetime.fromtimestamp(fallback).astimezone()


def _duration_from_mpd(path: Path) -> float | None:
    try:
        # The MPD duration is an attribute on the opening element. Reading only
        # the header is quicker than parsing a manifest containing many segments.
        header = path.read_text(errors="replace")[:65536]
        attribute = re.search(r'\bmediaPresentationDuration\s*=\s*["\']([^"\']+)["\']', header)
        if not attribute:
            return None
        value = attribute.group(1)
        match = re.fullmatch(r"PT(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?", value)
        if not match:
            return None
        hours, minutes, seconds = (float(part or 0) for part in match.groups())
        return hours * 3600 + minutes * 60 + seconds
    except OSError:
        return None


def _steamapps_dirs(roots: list[Path]) -> list[Path]:
    """Return primary and configured Steam library steamapps directories."""
    directories: list[Path] = []
    for root in roots:
        primary = root / "steamapps"
        directories.append(primary)
        try:
            text = (primary / "libraryfolders.vdf").read_text(errors="replace")
        except OSError:
            continue
        for encoded_path in re.findall(r'"path"\s+"((?:\\.|[^"\\])*)"', text):
            # VDF escapes backslashes. Linux library paths normally contain none,
            # but decoding them also keeps this fixture-friendly across platforms.
            library_path = encoded_path.replace("\\\\", "\\").replace('\\"', '"')
            directories.append(Path(library_path) / "steamapps")
    return list(dict.fromkeys(path.resolve() for path in directories))


def _read_appinfo_string_table(data, offset: int) -> list[str]:
    if offset < 16 or offset + 4 > len(data):
        raise ValueError("Invalid appinfo.vdf string table offset")
    count = struct.unpack_from("<I", data, offset)[0]
    if count > 1_000_000:
        raise ValueError("appinfo.vdf string table is too large")
    position = offset + 4
    strings: list[str] = []
    for _ in range(count):
        end = data.find(b"\0", position, min(len(data), position + 65537))
        if end < 0:
            raise ValueError("Invalid appinfo.vdf string")
        strings.append(bytes(data[position:end]).decode("utf-8", errors="replace"))
        position = end + 1
    return strings


def _read_appinfo_bvdf(data, position: int, end: int, strings: list[str], depth: int = 0) -> tuple[dict[str, Any], int]:
    if depth > 32:
        raise ValueError("appinfo.vdf nesting is too deep")
    result: dict[str, Any] = {}
    while position < end:
        value_type = data[position]
        position += 1
        if value_type == 8:
            return result, position
        if position + 4 > end:
            raise ValueError("Truncated appinfo.vdf key")
        key_index = struct.unpack_from("<I", data, position)[0]
        position += 4
        if key_index >= len(strings):
            raise ValueError("Invalid appinfo.vdf key index")
        key = strings[key_index]
        if value_type == 0:
            value, position = _read_appinfo_bvdf(data, position, end, strings, depth + 1)
        elif value_type == 1:
            value_end = data.find(b"\0", position, end)
            if value_end < 0 or value_end - position > 1024 * 1024:
                raise ValueError("Invalid appinfo.vdf value")
            value = bytes(data[position:value_end]).decode("utf-8", errors="replace")
            position = value_end + 1
        elif value_type in (2, 3, 4, 6):
            if position + 4 > end:
                raise ValueError("Truncated appinfo.vdf value")
            value = struct.unpack_from("<I", data, position)[0]
            position += 4
        elif value_type in (7, 10):
            if position + 8 > end:
                raise ValueError("Truncated appinfo.vdf value")
            value = struct.unpack_from("<Q", data, position)[0]
            position += 8
        elif value_type == 5:
            value_end = position
            while value_end + 1 < end and data[value_end:value_end + 2] != b"\0\0":
                value_end += 2
            if value_end + 1 >= end or value_end - position > 1024 * 1024:
                raise ValueError("Invalid appinfo.vdf wide string")
            value = bytes(data[position:value_end]).decode("utf-16-le", errors="replace")
            position = value_end + 2
        else:
            raise ValueError(f"Unsupported appinfo.vdf value type {value_type}")
        result[key] = value
    return result, position


def _appinfo_names_from_data(data, wanted_ids: set[int]) -> dict[str, str]:
    if len(data) < 20 or bytes(data[:4]) != APPINFO_V41_MAGIC:
        return {}
    string_offset = struct.unpack_from("<Q", data, 8)[0]
    strings = _read_appinfo_string_table(data, string_offset)
    names: dict[str, str] = {}
    remaining_ids = set(wanted_ids)
    position = 16
    while position + 4 <= string_offset and remaining_ids:
        app_id = struct.unpack_from("<I", data, position)[0]
        position += 4
        if app_id == 0:
            break
        if position + 4 > string_offset:
            break
        size = struct.unpack_from("<I", data, position)[0]
        position += 4
        entry_start = position
        entry_end = entry_start + size
        if size < 60 or entry_end > string_offset:
            break
        if app_id in wanted_ids:
            try:
                raw, _ = _read_appinfo_bvdf(data, entry_start + 60, entry_end, strings)
                appinfo = raw.get("appinfo", {})
                common = appinfo.get("common", {}) if isinstance(appinfo, dict) else {}
                name = common.get("name") if isinstance(common, dict) else None
                if isinstance(name, str) and name.strip():
                    names[str(app_id)] = name.strip()
                    remaining_ids.discard(app_id)
            except (ValueError, struct.error):
                pass
        position = entry_end
    return names


def _appinfo_names(path: Path, wanted_ids: set[int]) -> dict[str, str]:
    if not wanted_ids:
        return {}
    try:
        with path.open("rb") as appinfo_file:
            with mmap.mmap(appinfo_file.fileno(), 0, access=mmap.ACCESS_READ) as data:
                return _appinfo_names_from_data(data, wanted_ids)
    except (OSError, ValueError, struct.error):
        return {}


def _app_names(roots: list[Path], wanted_ids: set[str] | None = None) -> dict[str, str]:
    names: dict[str, str] = {}
    pattern = re.compile(r'"(appid|name)"\s+"([^"]*)"')
    for steamapps in _steamapps_dirs(roots):
        for manifest in steamapps.glob("appmanifest_*.acf"):
            try:
                fields = dict(pattern.findall(manifest.read_text(errors="replace")))
                if fields.get("appid") and fields.get("name"):
                    names[fields["appid"]] = fields["name"]
            except OSError:
                continue
    for root in roots:
        userdata = root / "userdata"
        if not userdata.is_dir():
            continue
        for shortcuts in userdata.glob("*/config/shortcuts.vdf"):
            for app_id, name in _shortcut_names(shortcuts).items():
                names.setdefault(app_id, name)
    numeric_ids = {
        int(app_id) for app_id in (wanted_ids or set())
        if app_id.isdigit() and int(app_id) <= 0xFFFFFFFF and app_id not in names
    }
    for root in roots:
        cached = _appinfo_names(root / "appcache/appinfo.vdf", numeric_ids)
        for app_id, name in cached.items():
            names.setdefault(app_id, name)
        numeric_ids -= {int(app_id) for app_id in cached}
        if not numeric_ids:
            break
    return names


def _read_vdf_string(data: bytes, position: int) -> tuple[str, int]:
    end = data.find(b"\0", position)
    if end < 0 or end - position > 65536:
        raise ValueError("Invalid shortcuts.vdf string")
    return data[position:end].decode("utf-8", errors="replace"), end + 1


def _read_vdf_object(data: bytes, position: int, depth: int = 0) -> tuple[dict[str, Any], int]:
    if depth > 16:
        raise ValueError("shortcuts.vdf nesting is too deep")
    result: dict[str, Any] = {}
    while position < len(data):
        value_type = data[position]
        position += 1
        if value_type == 8:
            return result, position
        key, position = _read_vdf_string(data, position)
        key = key.lower()
        if value_type == 0:
            value, position = _read_vdf_object(data, position, depth + 1)
        elif value_type == 1:
            value, position = _read_vdf_string(data, position)
        elif value_type == 2:
            if position + 4 > len(data):
                raise ValueError("Truncated shortcuts.vdf integer")
            value = struct.unpack_from("<i", data, position)[0]
            position += 4
        elif value_type == 7:
            if position + 8 > len(data):
                raise ValueError("Truncated shortcuts.vdf integer")
            value = struct.unpack_from("<Q", data, position)[0]
            position += 8
        else:
            raise ValueError(f"Unsupported shortcuts.vdf value type {value_type}")
        result[key] = value
    raise ValueError("Unterminated shortcuts.vdf object")


def _shortcut_names(path: Path) -> dict[str, str]:
    """Read non-Steam shortcut IDs and names without changing Steam metadata."""
    try:
        if path.stat().st_size > 16 * 1024 * 1024:
            return {}
        data = path.read_bytes()
        if not data or data[0] != 0:
            return {}
        root_key, position = _read_vdf_string(data, 1)
        root_value, _ = _read_vdf_object(data, position, 1)
        root = {root_key.lower(): root_value}
    except (OSError, ValueError, struct.error):
        return {}
    shortcuts = root.get("shortcuts")
    if not isinstance(shortcuts, dict):
        return {}
    names: dict[str, str] = {}
    for shortcut in shortcuts.values():
        if not isinstance(shortcut, dict):
            continue
        app_id = shortcut.get("appid")
        name = shortcut.get("appname")
        if not isinstance(app_id, int) or not isinstance(name, str) or not name.strip():
            continue
        unsigned_id = app_id & 0xFFFFFFFF
        clean_name = name.strip()
        names[str(unsigned_id)] = clean_name
        names[str((unsigned_id << 32) | 0x02000000)] = clean_name
    return names


def _discover(limit: int | None = None) -> list[dict[str, Any]]:
    roots = _steam_roots()
    found: list[tuple[float, Path]] = []
    for root in roots:
        userdata = root / "userdata"
        if not userdata.is_dir():
            continue
        for clips_dir in userdata.glob("*/gamerecordings/clips"):
            for clip_dir in clips_dir.glob("clip_*"):
                if clip_dir.is_dir():
                    try:
                        found.append((clip_dir.stat().st_mtime, clip_dir.resolve()))
                    except OSError:
                        continue
    wanted_ids = {
        match.group("app")
        for _, clip_dir in found
        if (match := CLIP_RE.match(clip_dir.name)) is not None
    }
    app_names = _app_names(roots, wanted_ids)
    results = []
    for modified, clip_dir in sorted(found, reverse=True):
        sessions = sorted(clip_dir.glob("video/**/session.mpd"), key=lambda p: p.stat().st_mtime)
        if not sessions:
            continue
        match = CLIP_RE.match(clip_dir.name)
        app_id = match.group("app") if match else "unknown"
        recorded = _parse_timestamp(clip_dir.name, modified)
        durations = [_duration_from_mpd(session) for session in sessions]
        total = sum(value for value in durations if value is not None) if any(value is not None for value in durations) else None
        results.append({
            "id": str(clip_dir),
            "app_id": app_id,
            "game_name": app_names.get(app_id, f"Steam app {app_id}" if app_id.isdigit() else "Steam clip"),
            "recorded_at": recorded.isoformat(),
            "duration_seconds": total,
            "session_count": len(sessions),
        })
        if limit is not None and len(results) == limit:
            break
    return results
