import asyncio
import importlib.util
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

fake_decky = types.SimpleNamespace(DECKY_USER_HOME="/home/deck", logger=types.SimpleNamespace(info=lambda *a: None, exception=lambda *a: None))
sys.modules.setdefault("decky", fake_decky)
spec = importlib.util.spec_from_file_location("deckclip_backend", Path(__file__).parents[1] / "main.py")
backend = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(backend)


class BackendTests(unittest.TestCase):
    def test_discovers_three_newest_and_reads_duration(self):
        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name)
            clips = root / "userdata/42/gamerecordings/clips"
            for number in range(4):
                session = clips / f"clip_123_2026010{number + 1}_120000/video/fg_123/session.mpd"
                session.parent.mkdir(parents=True)
                session.write_text('<MPD mediaPresentationDuration="PT1M2.5S"/>')
                os.utime(session.parents[2], (number + 1, number + 1))
            old = os.environ.get("DECKCLIP_STEAM_ROOT")
            os.environ["DECKCLIP_STEAM_ROOT"] = root_name
            try:
                all_found = backend._discover()
                found = backend._discover(3)
            finally:
                if old is None: os.environ.pop("DECKCLIP_STEAM_ROOT", None)
                else: os.environ["DECKCLIP_STEAM_ROOT"] = old
            self.assertEqual(4, len(all_found))
            self.assertEqual(3, len(found))
            self.assertEqual("clip_123_20260104_120000", Path(found[0]["id"]).name)
            self.assertEqual(62.5, found[0]["duration_seconds"])

    def test_sanitizes_requested_name(self):
        clip = {"game_name": "Game", "recorded_at": "2026-01-01T12:00:00+00:00"}
        self.assertEqual("my_clip.mp4", backend._safe_output_name("../my/clip.mp4", clip))

    def test_export_manager_only_lists_direct_regular_mp4_files(self):
        with tempfile.TemporaryDirectory() as folder_name:
            folder = Path(folder_name)
            previous = backend.OUTPUT_DIR
            backend.OUTPUT_DIR = folder
            try:
                (folder / "clip.mp4").write_bytes(b"video")
                (folder / "notes.txt").write_text("not an export")
                nested = folder / "nested"
                nested.mkdir()
                (nested / "hidden.mp4").write_bytes(b"video")
                (folder / "linked.mp4").symlink_to(folder / "clip.mp4")
                exports = backend._list_exports()
                self.assertEqual(["clip.mp4"], [item["filename"] for item in exports])
                with self.assertRaises(ValueError):
                    backend._export_path("../clip.mp4")
                with self.assertRaises(ValueError):
                    backend._export_path("linked.mp4")
            finally:
                backend.OUTPUT_DIR = previous

    def test_qr_matrix_has_expected_version_and_finder_patterns(self):
        matrix = backend._qr_matrix("http://192.168.1.10:12345/example/")
        self.assertEqual(37, len(matrix))
        self.assertTrue(all(len(row) == 37 for row in matrix))
        expected_finder = [
            "1111111",
            "1000001",
            "1011101",
            "1011101",
            "1011101",
            "1000001",
            "1111111",
        ]
        self.assertEqual(expected_finder, [row[:7] for row in matrix[:7]])

    def test_lan_transfer_requires_token_and_supports_ranges(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as folder_name:
                folder = Path(folder_name)
                previous_output = backend.OUTPUT_DIR
                backend.OUTPUT_DIR = folder
                plugin = backend.Plugin()
                await plugin._main()

                class MemoryWriter:
                    def __init__(self):
                        self.data = bytearray()

                    def write(self, value):
                        self.data.extend(value)

                    async def drain(self):
                        pass

                    def close(self):
                        pass

                    async def wait_closed(self):
                        pass

                async def request(target: str, extra: str = "") -> bytes:
                    reader = asyncio.StreamReader()
                    reader.feed_data(f"GET {target} HTTP/1.1\r\nHost: test\r\n{extra}\r\n".encode())
                    reader.feed_eof()
                    writer = MemoryWriter()
                    await plugin._handle_transfer_client(reader, writer)
                    return bytes(writer.data)

                try:
                    path = folder / "clip.mp4"
                    path.write_bytes(b"0123456789")
                    plugin.transfer = {
                        "server": None, "token": "secret", "path": path,
                        "filename": path.name, "url": "http://127.0.0.1:1234/secret/",
                        "expires_at": backend.time.time() + 60, "downloads": 0,
                        "bytes_sent": 0, "state": "ready",
                    }
                    denied = await request("/wrong/download")
                    self.assertIn(b"404 Not Found", denied)
                    landing = await request("/secret/")
                    self.assertIn(b"href='download' download>Download clip", landing)
                    inline = await request("/secret/video", "Range: bytes=0-1\r\n")
                    self.assertIn(b"Content-Disposition: inline", inline)
                    self.assertTrue(inline.endswith(b"01"))
                    ranged = await request("/secret/download", "Range: bytes=2-5\r\n")
                    self.assertIn(b"206 Partial Content", ranged)
                    self.assertTrue(ranged.endswith(b"2345"))
                    public = await plugin.get_transfer_status()
                    self.assertEqual(2, public["downloads"])
                finally:
                    plugin.transfer = None
                    backend.OUTPUT_DIR = previous_output

        asyncio.run(scenario())

    def test_orders_and_joins_every_stream_fragment(self):
        with tempfile.TemporaryDirectory() as folder_name:
            folder = Path(folder_name)
            (folder / "init-stream0.m4s").write_bytes(b"init")
            (folder / "chunk-stream0-00002.m4s").write_bytes(b"two")
            (folder / "chunk-stream0-00001.m4s").write_bytes(b"one")
            (folder / "init-stream1.m4s").write_bytes(b"audio-init")
            (folder / "chunk-stream1-00001.m4s").write_bytes(b"audio-one")
            streams = backend._stream_files(folder)
            self.assertEqual([0, 1], [stream_id for stream_id, _ in streams])
            destination = folder / "joined.mp4"
            backend._join_fragments(streams[0][1], destination, lambda _count: None)
            self.assertEqual(b"initonetwo", destination.read_bytes())

    def test_reads_names_from_secondary_steam_library(self):
        with tempfile.TemporaryDirectory() as root_name, tempfile.TemporaryDirectory() as library_name:
            root = Path(root_name)
            steamapps = root / "steamapps"
            steamapps.mkdir()
            (steamapps / "libraryfolders.vdf").write_text(
                f'"libraryfolders" {{ "1" {{ "path" "{library_name}" }} }}'
            )
            secondary = Path(library_name) / "steamapps"
            secondary.mkdir()
            (secondary / "appmanifest_123.acf").write_text(
                '"AppState" { "appid" "123" "name" "Example Game" }'
            )
            self.assertEqual("Example Game", backend._app_names([root])["123"])

    def test_reads_non_steam_shortcut_names(self):
        with tempfile.TemporaryDirectory() as folder_name:
            app_id = 0x81234567
            shortcut = (
                b"\x00shortcuts\0"
                b"\x000\0"
                b"\x02appid\0" + struct.pack("<I", app_id) +
                b"\x01appname\0Emulated Example\0"
                b"\x08\x08"
            )
            path = Path(folder_name) / "shortcuts.vdf"
            path.write_bytes(shortcut)
            names = backend._shortcut_names(path)
            self.assertEqual("Emulated Example", names[str(app_id)])
            self.assertEqual("Emulated Example", names[str((app_id << 32) | 0x02000000)])

    def test_reads_name_from_local_appinfo_cache(self):
        app_id = 1715130
        strings = [b"appinfo", b"common", b"name"]
        blob = (
            b"\x00" + struct.pack("<I", 0) +
            b"\x00" + struct.pack("<I", 1) +
            b"\x01" + struct.pack("<I", 2) + b"Crysis Remastered\0" +
            b"\x08\x08"
        )
        entry_size = 60 + len(blob)
        entry = struct.pack("<II", app_id, entry_size) + (b"\0" * 60) + blob
        string_offset = 16 + len(entry) + 4
        table = struct.pack("<I", len(strings)) + b"".join(value + b"\0" for value in strings)
        data = (
            backend.APPINFO_V41_MAGIC + struct.pack("<IQ", 1, string_offset) +
            entry + struct.pack("<I", 0) + table
        )
        self.assertEqual(
            "Crysis Remastered",
            backend._appinfo_names_from_data(data, {app_id})[str(app_id)],
        )

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg tools not installed")
    def test_remuxes_all_dash_fragments(self):
        with tempfile.TemporaryDirectory() as folder_name:
            folder = Path(folder_name)
            manifest = folder / "session.mpd"
            subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30:duration=7",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=7",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-seg_duration", "2", "-use_template", "1", "-use_timeline", "0",
                    "-init_seg_name", "init-stream$RepresentationID$.m4s",
                    "-media_seg_name", "chunk-stream$RepresentationID$-$Number%05d$.m4s",
                    "-adaptation_sets", "id=0,streams=v id=1,streams=a",
                    "-f", "dash", str(manifest),
                ],
                check=True,
            )
            output = folder / "result.mp4"
            asyncio.run(backend.Plugin()._remux_session(manifest, output, lambda _value: None))
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(output)],
                check=True,
                capture_output=True,
                text=True,
            )
            duration = float(json.loads(probe.stdout)["format"]["duration"])
            self.assertGreater(duration, 6.5)


if __name__ == "__main__":
    unittest.main()
