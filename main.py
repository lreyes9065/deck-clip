from __future__ import annotations

import asyncio
import copy
import datetime as dt
import html
import json
import mmap
import os
import re
import secrets
import shutil
import socket
import struct
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

import decky


OUTPUT_DIR = Path("/home/deck/Videos/DeckClip")
CLIP_RE = re.compile(r"^clip_(?P<app>\d+|downloaded)_(?P<date>\d{8})_(?P<time>\d{6})")
SAFE_NAME_RE = re.compile(r"[^\w .()\[\]-]+", re.UNICODE)
INIT_STREAM_RE = re.compile(r"^init-stream(?P<id>\d+)\.m4s$")
APPINFO_V41_MAGIC = b"\x29\x44\x56\x07"
TRANSFER_TTL_SECONDS = 10 * 60


def _qr_gf_multiply(left: int, right: int) -> int:
    result = 0
    for bit in range(7, -1, -1):
        result = (result << 1) ^ ((result >> 7) * 0x11D)
        result ^= ((left >> bit) & 1) * right
    return result


def _qr_reed_solomon(data: list[int], degree: int) -> list[int]:
    divisor = [0] * degree
    divisor[-1] = 1
    root = 1
    for _ in range(degree):
        for index in range(degree):
            divisor[index] = _qr_gf_multiply(divisor[index], root)
            if index + 1 < degree:
                divisor[index] ^= divisor[index + 1]
        root = _qr_gf_multiply(root, 2)
    remainder = [0] * degree
    for value in data:
        factor = value ^ remainder.pop(0)
        remainder.append(0)
        for index, coefficient in enumerate(divisor):
            remainder[index] ^= _qr_gf_multiply(coefficient, factor)
    return remainder


def _qr_mask(mask: int, row: int, column: int) -> bool:
    if mask == 0:
        return (row + column) % 2 == 0
    if mask == 1:
        return row % 2 == 0
    if mask == 2:
        return column % 3 == 0
    if mask == 3:
        return (row + column) % 3 == 0
    if mask == 4:
        return (row // 2 + column // 3) % 2 == 0
    if mask == 5:
        return (row * column) % 2 + (row * column) % 3 == 0
    if mask == 6:
        return ((row * column) % 2 + (row * column) % 3) % 2 == 0
    return ((row + column) % 2 + (row * column) % 3) % 2 == 0


def _qr_format_bits(mask: int) -> int:
    data = (1 << 3) | mask  # Error correction level L is binary 01.
    remainder = data
    for _ in range(10):
        remainder = (remainder << 1) ^ ((remainder >> 9) * 0x537)
    return ((data << 10) | remainder) ^ 0x5412


def _qr_draw_format(modules: list[list[bool]], mask: int) -> None:
    size = len(modules)
    bits = _qr_format_bits(mask)
    for index in range(6):
        modules[index][8] = bool((bits >> index) & 1)
    modules[7][8] = bool((bits >> 6) & 1)
    modules[8][8] = bool((bits >> 7) & 1)
    modules[8][7] = bool((bits >> 8) & 1)
    for index in range(9, 15):
        modules[8][14 - index] = bool((bits >> index) & 1)
    for index in range(8):
        modules[8][size - 1 - index] = bool((bits >> index) & 1)
    for index in range(8, 15):
        modules[size - 15 + index][8] = bool((bits >> index) & 1)
    modules[size - 8][8] = True


def _qr_penalty(modules: list[list[bool]]) -> int:
    size = len(modules)
    score = 0
    for lines in (modules, [[modules[row][column] for row in range(size)] for column in range(size)]):
        for line in lines:
            run_color = line[0]
            run_length = 1
            for value in line[1:]:
                if value == run_color:
                    run_length += 1
                else:
                    if run_length >= 5:
                        score += 3 + run_length - 5
                    run_color = value
                    run_length = 1
            if run_length >= 5:
                score += 3 + run_length - 5
            pattern = "".join("1" if value else "0" for value in line)
            score += 40 * (pattern.count("00001011101") + pattern.count("10111010000"))
    for row in range(size - 1):
        for column in range(size - 1):
            value = modules[row][column]
            if all(modules[row + dy][column + dx] == value for dy in (0, 1) for dx in (0, 1)):
                score += 3
    dark = sum(value for row in modules for value in row)
    score += abs(dark * 20 - size * size * 10) // (size * size) * 10
    return score


def _qr_matrix(text: str) -> list[str]:
    """Create a fixed version-5-L QR matrix for a short local transfer URL."""
    payload = text.encode("utf-8")
    if len(payload) > 106:
        raise ValueError("Transfer URL is too long for the QR code")
    bits: list[int] = [0, 1, 0, 0]
    bits.extend((len(payload) >> shift) & 1 for shift in range(7, -1, -1))
    for value in payload:
        bits.extend((value >> shift) & 1 for shift in range(7, -1, -1))
    bits.extend([0] * min(4, 864 - len(bits)))
    bits.extend([0] * ((8 - len(bits) % 8) % 8))
    data = [sum(bits[offset + bit] << (7 - bit) for bit in range(8)) for offset in range(0, len(bits), 8)]
    pad = (0xEC, 0x11)
    while len(data) < 108:
        data.append(pad[(len(data) - (len(bits) // 8)) % 2])
    codewords = data + _qr_reed_solomon(data, 26)
    code_bits = [(value >> shift) & 1 for value in codewords for shift in range(7, -1, -1)]

    size = 37
    base = [[False] * size for _ in range(size)]
    function = [[False] * size for _ in range(size)]

    def set_function(row: int, column: int, value: bool) -> None:
        if 0 <= row < size and 0 <= column < size:
            base[row][column] = value
            function[row][column] = True

    for center_row, center_column in ((3, 3), (3, size - 4), (size - 4, 3)):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                distance = max(abs(dx), abs(dy))
                set_function(center_row + dy, center_column + dx, distance not in (2, 4))
    for index in range(size):
        if not function[6][index]:
            set_function(6, index, index % 2 == 0)
        if not function[index][6]:
            set_function(index, 6, index % 2 == 0)
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            set_function(30 + dy, 30 + dx, max(abs(dx), abs(dy)) != 1)
    for index in range(9):
        if index != 6:
            set_function(8, index, False)
            set_function(index, 8, False)
    for index in range(8):
        set_function(size - 1 - index, 8, False)
        set_function(8, size - 1 - index, False)
    set_function(size - 8, 8, True)

    best: list[list[bool]] | None = None
    best_score: int | None = None
    for mask in range(8):
        modules = [row[:] for row in base]
        bit_index = 0
        right = size - 1
        while right >= 1:
            if right == 6:
                right -= 1
            upward = ((right + 1) & 2) == 0
            for vertical in range(size):
                row = size - 1 - vertical if upward else vertical
                for column in (right, right - 1):
                    if not function[row][column]:
                        value = bool(code_bits[bit_index]) if bit_index < len(code_bits) else False
                        modules[row][column] = value ^ _qr_mask(mask, row, column)
                        bit_index += 1
            right -= 2
        _qr_draw_format(modules, mask)
        score = _qr_penalty(modules)
        if best_score is None or score < best_score:
            best, best_score = modules, score
    assert best is not None
    return ["".join("1" if value else "0" for value in row) for row in best]


def _lan_address() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 9))
        address = sock.getsockname()[0]
    except OSError as error:
        raise RuntimeError("Connect the Steam Deck to a local network before sharing") from error
    finally:
        sock.close()
    if address.startswith("127.") or address == "0.0.0.0":
        raise RuntimeError("Could not determine the Steam Deck's local network address")
    return address


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


def _stream_files(session_dir: Path) -> list[tuple[int, list[Path]]]:
    """Return each fragmented MP4 stream with init first and chunks numerically ordered."""
    streams: list[tuple[int, list[Path]]] = []
    for init in session_dir.glob("init-stream*.m4s"):
        match = INIT_STREAM_RE.match(init.name)
        if not match or not init.is_file():
            continue
        stream_id = int(match.group("id"))
        chunk_re = re.compile(rf"^chunk-stream{stream_id}-(\d+)\.m4s$")
        numbered_chunks = []
        for chunk in session_dir.glob(f"chunk-stream{stream_id}-*.m4s"):
            chunk_match = chunk_re.match(chunk.name)
            if chunk_match and chunk.is_file():
                numbered_chunks.append((int(chunk_match.group(1)), chunk))
        numbered_chunks.sort(key=lambda entry: entry[0])
        if numbered_chunks:
            streams.append((stream_id, [init, *(path for _, path in numbered_chunks)]))
    streams.sort(key=lambda entry: entry[0])
    return streams


def _join_fragments(files: list[Path], destination: Path, on_bytes) -> None:
    """Join fMP4 fragments into a temporary stream without changing source files."""
    with destination.open("xb") as output:
        for source in files:
            with source.open("rb") as input_file:
                while block := input_file.read(1024 * 1024):
                    output.write(block)
                    on_bytes(len(block))


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


def _safe_output_name(requested: str | None, clip: dict[str, Any]) -> str:
    default = f"{clip['game_name']} - {dt.datetime.fromisoformat(clip['recorded_at']).strftime('%Y-%m-%d %H-%M-%S')}"
    name = SAFE_NAME_RE.sub("_", (requested or default).strip()).strip(" ._")[:160] or "Steam clip"
    if name.lower().endswith(".mp4"):
        name = name[:-4].rstrip(" .")
    return name + ".mp4"


def _export_path(filename: str) -> Path:
    """Resolve one direct MP4 child of OUTPUT_DIR without accepting traversal or links."""
    if not isinstance(filename, str) or not filename or Path(filename).name != filename:
        raise ValueError("Invalid exported filename")
    if not filename.lower().endswith(".mp4"):
        raise ValueError("Only exported MP4 files can be managed")
    candidate = OUTPUT_DIR / filename
    try:
        if candidate.is_symlink() or not candidate.is_file():
            raise ValueError("Exported file no longer exists")
        if candidate.resolve().parent != OUTPUT_DIR.resolve():
            raise ValueError("Exported file is outside the DeckClip folder")
    except OSError as error:
        raise ValueError("Could not inspect exported file") from error
    return candidate


def _list_exports() -> list[dict[str, Any]]:
    if not OUTPUT_DIR.is_dir():
        return []
    exports = []
    try:
        candidates = list(OUTPUT_DIR.iterdir())
    except OSError:
        return []
    for candidate in candidates:
        try:
            path = _export_path(candidate.name)
            stat = path.stat()
        except (OSError, ValueError):
            continue
        exports.append({
            "filename": path.name,
            "size_bytes": stat.st_size,
            "modified_at": dt.datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(),
        })
    return sorted(exports, key=lambda item: item["modified_at"], reverse=True)


class Plugin:
    async def _main(self):
        self.jobs: dict[str, dict[str, Any]] = {}
        self.tasks: set[asyncio.Task] = set()
        self.transfer: dict[str, Any] | None = None
        self.transfer_expiry: asyncio.Task | None = None
        decky.logger.info("DeckClip loaded")

    async def _unload(self):
        await self._stop_transfer()
        for task in self.tasks:
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)

    async def list_clips(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(_discover)

    async def list_exports(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(_list_exports)

    async def trash_export(self, filename: str) -> dict[str, str]:
        path = await asyncio.to_thread(_export_path, filename)
        gio = shutil.which("gio")
        if gio is None:
            raise RuntimeError("The system Trash service is unavailable. Delete this file in Desktop Mode.")
        process = await asyncio.create_subprocess_exec(
            gio, "trash", "--", str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            detail = stderr.decode(errors="replace").strip()
            raise RuntimeError(detail or "Could not move the export to Trash")
        return {"filename": filename}

    async def start_transfer(self, filename: str) -> dict[str, Any]:
        path = await asyncio.to_thread(_export_path, filename)
        await self._stop_transfer()
        address = await asyncio.to_thread(_lan_address)
        token = secrets.token_urlsafe(32)
        expires_at = time.time() + TRANSFER_TTL_SECONDS
        server = await asyncio.start_server(
            self._handle_transfer_client,
            host="0.0.0.0",
            port=0,
            limit=16 * 1024,
        )
        sockets = server.sockets or []
        if not sockets:
            server.close()
            await server.wait_closed()
            raise RuntimeError("Could not start the local transfer server")
        port = int(sockets[0].getsockname()[1])
        # Point the QR directly at the MP4 so iOS opens its top-level native
        # video viewer, where Safari exposes Share and Save Video.
        url = f"http://{address}:{port}/{token}/video"
        self.transfer = {
            "server": server,
            "token": token,
            "path": path,
            "filename": path.name,
            "url": url,
            "expires_at": expires_at,
            "downloads": 0,
            "bytes_sent": 0,
            "state": "ready",
        }
        self.transfer_expiry = asyncio.create_task(self._expire_transfer(token))
        return self._transfer_public_status(include_qr=True)

    async def get_transfer_status(self) -> dict[str, Any]:
        if self.transfer is None:
            return {"state": "inactive"}
        if time.time() >= self.transfer["expires_at"]:
            await self._stop_transfer()
            return {"state": "expired"}
        return self._transfer_public_status(include_qr=False)

    async def stop_transfer(self) -> dict[str, str]:
        await self._stop_transfer()
        return {"state": "inactive"}

    def _transfer_public_status(self, include_qr: bool) -> dict[str, Any]:
        assert self.transfer is not None
        status = {
            "state": self.transfer["state"],
            "filename": self.transfer["filename"],
            "url": self.transfer["url"],
            "expires_at": dt.datetime.fromtimestamp(self.transfer["expires_at"]).astimezone().isoformat(),
            "downloads": self.transfer["downloads"],
            "bytes_sent": self.transfer["bytes_sent"],
        }
        if include_qr:
            status["qr"] = _qr_matrix(self.transfer["url"])
        return status

    async def _expire_transfer(self, token: str):
        try:
            await asyncio.sleep(TRANSFER_TTL_SECONDS)
            if self.transfer is not None and secrets.compare_digest(self.transfer["token"], token):
                await self._stop_transfer()
        except asyncio.CancelledError:
            pass

    async def _stop_transfer(self):
        transfer = self.transfer
        self.transfer = None
        expiry = self.transfer_expiry
        self.transfer_expiry = None
        if expiry is not None and expiry is not asyncio.current_task():
            expiry.cancel()
            await asyncio.gather(expiry, return_exceptions=True)
        if transfer is not None:
            server = transfer["server"]
            server.close()
            await server.wait_closed()

    async def _send_http(self, writer: asyncio.StreamWriter, status: str, headers: dict[str, str], body: bytes = b""):
        safe_headers = {
            "Connection": "close",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            **headers,
        }
        head = f"HTTP/1.1 {status}\r\n" + "".join(f"{key}: {value}\r\n" for key, value in safe_headers.items()) + "\r\n"
        writer.write(head.encode("ascii") + body)
        await writer.drain()

    async def _handle_transfer_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            try:
                request = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
            except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
                await self._send_http(writer, "400 Bad Request", {"Content-Length": "0"})
                return
            if len(request) > 8192:
                await self._send_http(writer, "431 Request Header Fields Too Large", {"Content-Length": "0"})
                return
            lines = request.decode("iso-8859-1").split("\r\n")
            parts = lines[0].split(" ")
            if len(parts) != 3 or parts[0] not in ("GET", "HEAD") or not parts[2].startswith("HTTP/1."):
                await self._send_http(writer, "405 Method Not Allowed", {"Allow": "GET, HEAD", "Content-Length": "0"})
                return
            method, target, _ = parts
            headers = {}
            for line in lines[1:]:
                if not line:
                    break
                if ":" not in line:
                    await self._send_http(writer, "400 Bad Request", {"Content-Length": "0"})
                    return
                key, value = line.split(":", 1)
                headers[key.strip().lower()] = value.strip()
            transfer = self.transfer
            if transfer is None or time.time() >= transfer["expires_at"]:
                await self._send_http(writer, "410 Gone", {"Content-Length": "0"})
                return
            base = f"/{transfer['token']}/"
            if target == base:
                filename = html.escape(transfer["filename"])
                body = (
                    "<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>"
                    "<title>DeckClip transfer</title><style>"
                    "body{background:#111;color:#fff;font:17px system-ui;margin:0 auto;max-width:760px;padding:24px}"
                    "a{color:#7cc4ff}small{color:#bbb}</style><h1>DeckClip</h1>"
                    f"<p>{filename}</p><p><a href='video'>Open video in Safari</a></p>"
                    "<p>Then use Safari's Share button and choose Save Video.</p>"
                    "<p><a href='download' download>Save to Files instead</a></p>"
                    "<small>This temporary link works only on the same local network.</small>"
                ).encode("utf-8")
                await self._send_http(writer, "200 OK", {
                    "Content-Type": "text/html; charset=utf-8",
                    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'",
                    "X-Frame-Options": "DENY",
                    "Content-Length": str(len(body)),
                }, b"" if method == "HEAD" else body)
                return
            if target not in (base + "video", base + "download"):
                await self._send_http(writer, "404 Not Found", {"Content-Length": "0"})
                return
            try:
                path = await asyncio.to_thread(_export_path, transfer["filename"])
                size = path.stat().st_size
            except (OSError, ValueError):
                await self._send_http(writer, "410 Gone", {"Content-Length": "0"})
                return
            start, end = 0, max(0, size - 1)
            response_status = "200 OK"
            range_header = headers.get("range")
            if range_header:
                match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
                if not match or (not match.group(1) and not match.group(2)):
                    await self._send_http(writer, "416 Range Not Satisfiable", {"Content-Range": f"bytes */{size}", "Content-Length": "0"})
                    return
                if match.group(1):
                    start = int(match.group(1))
                    end = min(int(match.group(2) or end), end)
                else:
                    suffix = min(int(match.group(2)), size)
                    start = size - suffix
                if start >= size or end < start:
                    await self._send_http(writer, "416 Range Not Satisfiable", {"Content-Range": f"bytes */{size}", "Content-Length": "0"})
                    return
                response_status = "206 Partial Content"
            length = end - start + 1 if size else 0
            download_name = quote(transfer["filename"], safe="")
            response_headers = {
                "Content-Type": "video/mp4",
                "Content-Disposition": f"{'attachment' if target.endswith('/download') else 'inline'}; filename*=UTF-8''{download_name}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(length),
            }
            if response_status.startswith("206"):
                response_headers["Content-Range"] = f"bytes {start}-{end}/{size}"
            await self._send_http(writer, response_status, response_headers)
            if method == "HEAD" or not length:
                return
            sent = 0
            with path.open("rb") as input_file:
                input_file.seek(start)
                remaining = length
                while remaining and self.transfer is transfer and time.time() < transfer["expires_at"]:
                    chunk = await asyncio.to_thread(input_file.read, min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    writer.write(chunk)
                    await writer.drain()
                    sent += len(chunk)
                    remaining -= len(chunk)
                    transfer["bytes_sent"] += len(chunk)
            if sent == length:
                transfer["downloads"] += 1
                transfer["state"] = "downloaded"
        except (ConnectionError, BrokenPipeError):
            pass
        except Exception:
            decky.logger.exception("DeckClip transfer request failed")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass

    async def start_export(self, items: list[dict[str, str]]) -> dict[str, str]:
        available = {clip["id"]: clip for clip in await self.list_clips()}
        if not items:
            raise ValueError("Select at least one clip")
        requested = []
        for item in items:
            clip = available.get(item.get("id", ""))
            if clip is None:
                raise ValueError("A selected clip is no longer available")
            requested.append((clip, item.get("name")))
        job_id = uuid.uuid4().hex
        self.jobs[job_id] = {
            "state": "queued", "progress": 0.0, "output_dir": str(OUTPUT_DIR),
            "clips": [{"id": clip["id"], "display_name": clip["game_name"], "progress": 0.0, "state": "queued"} for clip, _ in requested],
        }
        task = asyncio.create_task(self._run_export(job_id, requested))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return {"job_id": job_id}

    async def get_export_status(self, job_id: str) -> dict[str, Any]:
        if job_id not in self.jobs:
            raise ValueError("Unknown export job")
        return copy.deepcopy(self.jobs[job_id])

    async def _run_export(self, job_id: str, requested: list[tuple[dict[str, Any], str | None]]):
        job = self.jobs[job_id]
        job["state"] = "running"
        try:
            if shutil.which("ffmpeg") is None:
                raise RuntimeError("FFmpeg was not found. Install the Decky FFmpeg binary dependency before exporting.")
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            for index, (clip, rename) in enumerate(requested):
                await self._export_one(job, index, clip, rename)
            job["progress"] = 100.0
            job["state"] = "complete"
        except asyncio.CancelledError:
            job["state"] = "cancelled"
            raise
        except Exception as error:
            decky.logger.exception("DeckClip export failed")
            job["state"] = "failed"
            job["error"] = str(error)

    async def _export_one(self, job: dict[str, Any], index: int, clip: dict[str, Any], rename: str | None):
        item = job["clips"][index]
        item["state"] = "exporting"
        sessions = sorted(Path(clip["id"]).glob("video/**/session.mpd"), key=lambda p: p.stat().st_mtime)
        if not sessions:
            raise RuntimeError(f"No session manifest found for {clip['game_name']}")
        output = OUTPUT_DIR / _safe_output_name(rename, clip)
        counter = 2
        while output.exists():
            output = output.with_name(f"{output.stem} ({counter}).mp4")
            counter += 1
        # Keep temporary output beside the destination so the final atomic rename
        # cannot cross filesystems. Source recording directories are never written.
        with tempfile.TemporaryDirectory(prefix=".deckclip-", dir=OUTPUT_DIR) as temp_name:
            temp = Path(temp_name)
            parts = []
            for session_index, session in enumerate(sessions):
                part = temp / f"part-{session_index:03d}.mp4"
                await self._remux_session(
                    session,
                    part,
                    lambda percent, base=session_index: self._set_progress(
                        job, index, (base + percent / 100) / len(sessions) * 90
                    ),
                )
                parts.append(part)
            if len(parts) == 1:
                os.replace(parts[0], output)
            else:
                concat = temp / "concat.txt"
                concat.write_text("".join(f"file '{part.as_posix()}'\n" for part in parts))
                await self._run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-map", "0", "-c", "copy", "-movflags", "+faststart", str(output)])
        item.update({"progress": 100.0, "state": "complete", "output": str(output)})
        job["progress"] = sum(entry["progress"] for entry in job["clips"]) / len(job["clips"])

    def _set_progress(self, job: dict[str, Any], index: int, value: float):
        job["clips"][index]["progress"] = min(99.0, value)
        job["progress"] = sum(entry["progress"] for entry in job["clips"]) / len(job["clips"])

    async def _remux_session(self, manifest: Path, output: Path, progress):
        streams = _stream_files(manifest.parent)
        if not streams or streams[0][0] != 0:
            raise RuntimeError(f"No complete video fragment stream found beside {manifest.name}")

        total_bytes = sum(path.stat().st_size for _, files in streams for path in files)
        copied_bytes = 0

        def on_bytes(count: int):
            nonlocal copied_bytes
            copied_bytes += count
            if total_bytes:
                progress(copied_bytes / total_bytes * 70.0)

        assembled: list[Path] = []
        for stream_id, files in streams:
            stream_file = output.parent / f"{output.stem}-stream{stream_id}.mp4"
            await asyncio.to_thread(_join_fragments, files, stream_file, on_bytes)
            assembled.append(stream_file)

        command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
        for stream_file in assembled:
            command.extend(["-i", str(stream_file)])
        command.extend(["-map", "0:v:0"])
        for input_index in range(1, len(assembled)):
            command.extend(["-map", f"{input_index}:a:0?"])
        command.extend([
            "-c", "copy", "-movflags", "+faststart", "-progress", "pipe:1", str(output)
        ])

        duration = _duration_from_mpd(manifest) or 0
        process = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdout is not None
        async for raw in process.stdout:
            line = raw.decode(errors="replace").strip()
            if line.startswith("out_time_ms=") and duration:
                media_percent = int(line.split("=", 1)[1]) / 1_000_000 / duration * 100
                progress(70.0 + min(100.0, media_percent) * 0.3)
        stderr = (await process.stderr.read()).decode(errors="replace") if process.stderr else ""
        if await process.wait() != 0:
            raise RuntimeError(stderr.strip() or f"FFmpeg could not remux {manifest.parent.name}")
        progress(100.0)

    async def _run(self, command: list[str]):
        process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(stderr.decode(errors="replace").strip() or "FFmpeg concat failed")
