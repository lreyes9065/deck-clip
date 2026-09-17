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
import time
import unittest
from unittest.mock import AsyncMock, patch
from pathlib import Path

import backend.transfer as transfer_backend
from backend.media import join_fragments
from backend.qr import _qr_matrix
from backend.exports import file_identity

fake_decky = types.SimpleNamespace(DECKY_USER_HOME="/home/deck", logger=types.SimpleNamespace(info=lambda *a: None, exception=lambda *a: None))
sys.modules.setdefault("decky", fake_decky)
import backend.library as library_backend
spec = importlib.util.spec_from_file_location("deckclip_backend", Path(__file__).parents[1] / "main.py")
backend = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(backend)


class BackendTests(unittest.TestCase):
    def test_all_ffmpeg_launches_use_child_library_environment(self):
        async def check():
            plugin = backend.Plugin()
            process = types.SimpleNamespace(
                returncode=0, communicate=AsyncMock(return_value=(b"jpeg", b"")),
                stdout=AsyncMock(), stderr=types.SimpleNamespace(read=AsyncMock(return_value=b"")),
                wait=AsyncMock(return_value=0),
            )
            process.stdout.__aiter__.return_value = []
            process.stdout.read = AsyncMock(side_effect=[b"jpeg", b""])
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                clip = root / "clip.mp4"
                clip.write_bytes(b"fixture")
                with patch.dict(os.environ, {"LD_LIBRARY_PATH": "/tmp/_MEItest", "LD_LIBRARY_PATH_ORIG": "/original/lib"}), patch.object(backend.asyncio, "create_subprocess_exec", new_callable=AsyncMock, return_value=process) as spawn:
                    await plugin._run(["ffmpeg", "-version"])
                    await backend.Thumbnails(lambda name: clip).get("clip.mp4")
                    with patch.object(backend, "_stream_files", return_value=[(0, [clip])]), patch.object(backend, "_duration_from_mpd", return_value=1):
                        await plugin._remux_session(root / "session.mpd", root / "out.mp4", lambda value: None)
                    self.assertEqual(spawn.await_count, 3)
                    for call in spawn.await_args_list:
                        self.assertEqual(call.kwargs["env"]["LD_LIBRARY_PATH"], "/original/lib")
                    self.assertEqual(os.environ["LD_LIBRARY_PATH"], "/tmp/_MEItest")
        asyncio.run(check())

    def test_recent_statuses_are_latest_five_and_detached(self):
        plugin = backend.Plugin()
        plugin.jobs = {str(i): {"state": "complete", "clips": [{"display_name": str(i)}]} for i in range(8)}
        result = asyncio.run(plugin.get_recent_statuses())
        self.assertEqual([entry["id"] for entry in result], ["7", "6", "5", "4", "3"])
        result[0]["clips"][0]["display_name"] = "changed"
        self.assertEqual(plugin.jobs["7"]["clips"][0]["display_name"], "7")

    def test_failed_export_retains_full_details_and_marks_clip_failed(self):
        plugin = backend.Plugin()
        error = "No space left on device\n" + "diagnostic line\n" * 200
        plugin.jobs = {"test": {"state": "queued", "clips": [{"state": "exporting", "stage": "Assembling stream 0"}]}}
        plugin._export_one = AsyncMock(side_effect=OSError(error))
        with tempfile.TemporaryDirectory() as temp, patch.object(backend, "OUTPUT_DIR", Path(temp)), patch.object(backend.shutil, "which", return_value="ffmpeg"):
            asyncio.run(plugin._run_export("test", [({}, None)]))
        result = asyncio.run(plugin.get_recent_statuses())[0]
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["error"], error)
        self.assertIn(error, result["details"])
        self.assertEqual(result["clips"][0]["state"], "failed")
        self.assertEqual(result["clips"][0]["stage"], "Assembling stream 0")
        self.assertIn("finished_at", result)
        self.assertIn("free_bytes", result)

    def test_successful_export_has_finished_status(self):
        plugin = backend.Plugin()
        plugin.jobs = {"test": {"state": "queued", "clips": []}}
        with tempfile.TemporaryDirectory() as temp, patch.object(backend, "OUTPUT_DIR", Path(temp)), patch.object(backend.shutil, "which", return_value="ffmpeg"):
            asyncio.run(plugin._run_export("test", []))
        result = asyncio.run(plugin.get_recent_statuses())[0]
        self.assertEqual(result["state"], "complete")
        self.assertIn("finished_at", result)

    def test_transfer_rejects_empty_duplicate_and_oversized_batches(self):
        manager = transfer_backend.TransferManager(lambda name: Path(name), fake_decky.logger)
        with self.assertRaisesRegex(ValueError, "at least one"):
            asyncio.run(manager.start_transfer([]))
        with self.assertRaisesRegex(ValueError, "invalid"):
            asyncio.run(manager.start_transfer(["clip.mp4", "clip.mp4"]))
        with self.assertRaisesRegex(ValueError, "no more than"):
            asyncio.run(manager.start_transfer([f"clip-{index}.mp4" for index in range(21)]))

    def test_main_loads_when_plugin_directory_is_not_on_python_path(self):
        project = Path(__file__).parents[1]
        loader = """
import importlib.util, sys, types
from pathlib import Path
sys.modules['decky'] = types.SimpleNamespace(
    DECKY_USER_HOME='/home/deck',
    logger=types.SimpleNamespace(info=lambda *a: None, exception=lambda *a: None),
)
path = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location('deckclip_isolated', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert module.Plugin
"""
        with tempfile.TemporaryDirectory() as working_dir:
            result = subprocess.run(
                [sys.executable, "-c", loader, str(project / "main.py")],
                cwd=working_dir,
                capture_output=True,
                text=True,
            )
        self.assertEqual(0, result.returncode, result.stderr)

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
        matrix = _qr_matrix("http://192.168.1.10:12345/example/")
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

    def test_lan_transfer_requires_token_and_serves_batch_manifest_and_ranges(self):
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
                    await plugin.transfers._handle_transfer_client(reader, writer)
                    return bytes(writer.data)

                try:
                    path = folder / "clip & one.mp4"
                    second = folder / "clip-two.mp4"
                    path.write_bytes(b"0123456789")
                    second.write_bytes(b"abcdefghij")
                    plugin.transfers.transfer = {
                        "server": None, "token": "secret",
                        "files": [
                            {"id": 0, "identity": file_identity(path.stat()), "filename": path.name, "complete": False},
                            {"id": 1, "identity": file_identity(second.stat()), "filename": second.name, "complete": False},
                        ],
                        "url": "http://127.0.0.1:1234/secret/",
                        "expires_at": time.time() + 60, "downloads": 0,
                        "completed_files": 0, "bytes_sent": 0, "state": "ready",
                    }
                    denied = await request("/wrong/file/0")
                    self.assertIn(b"404 Not Found", denied)
                    landing = await request("/secret/")
                    self.assertIn(b">Save all to Photos</a>", landing)
                    self.assertIn(b"Help &amp; Settings", landing)
                    self.assertGreater(landing.index(b">Save all to Photos</a>"), landing.index(b"clip-two.mp4"))
                    self.assertIn(b"shortcuts://run-shortcut?name=DeckClip%20Save%20to%20Photos", landing)
                    self.assertIn(b"http%3A%2F%2F127.0.0.1%3A1234%2Fsecret%2Fmanifest", landing)
                    self.assertIn(b"clip &amp; one.mp4", landing)
                    self.assertNotIn(b"clipboard", landing.lower())
                    manifest = await request("/secret/manifest")
                    self.assertIn(b'"name": "clip & one.mp4"', manifest)
                    self.assertIn(b'"url": "http://127.0.0.1:1234/secret/file/1"', manifest)
                    self.assertNotIn(str(folder).encode(), manifest)
                    ranged = await request("/secret/file/0", "Range: bytes=2-5\r\n")
                    self.assertIn(b"206 Partial Content", ranged)
                    self.assertIn(b"Content-Disposition: attachment", ranged)
                    self.assertTrue(ranged.endswith(b"2345"))
                    missing = await request("/secret/file/2")
                    self.assertIn(b"404 Not Found", missing)
                    public = await plugin.get_transfer_status()
                    self.assertEqual(2, public["file_count"])
                    self.assertEqual(0, public["completed_files"])
                finally:
                    plugin.transfers.transfer = None
                    backend.OUTPUT_DIR = previous_output

        asyncio.run(scenario())

    def test_batch_closes_only_after_every_full_download(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as folder_name:
                folder = Path(folder_name)
                previous_output = backend.OUTPUT_DIR
                previous_grace = transfer_backend.TRANSFER_COMPLETION_GRACE_SECONDS
                backend.OUTPUT_DIR = folder
                transfer_backend.TRANSFER_COMPLETION_GRACE_SECONDS = 0.01
                plugin = backend.Plugin()
                await plugin._main()

                class FakeServer:
                    def close(self): pass
                    async def wait_closed(self): pass

                class MemoryWriter:
                    def __init__(self): self.data = bytearray()
                    def write(self, value): self.data.extend(value)
                    async def drain(self): pass
                    def close(self): pass
                    async def wait_closed(self): pass

                try:
                    path = folder / "clip.mp4"
                    second = folder / "clip-two.mp4"
                    path.write_bytes(b"0123456789")
                    second.write_bytes(b"abcdefghij")
                    plugin.transfers.transfer = {
                        "server": FakeServer(), "token": "secret",
                        "files": [
                            {"id": 0, "identity": file_identity(path.stat()), "filename": path.name, "complete": False},
                            {"id": 1, "identity": file_identity(second.stat()), "filename": second.name, "complete": False},
                        ],
                        "url": "http://127.0.0.1:1234/secret/",
                        "expires_at": time.time() + 60, "downloads": 0,
                        "completed_files": 0, "bytes_sent": 0, "state": "ready",
                    }
                    reader = asyncio.StreamReader()
                    reader.feed_data(b"GET /secret/file/0 HTTP/1.1\r\nHost: test\r\n\r\n")
                    reader.feed_eof()
                    writer = MemoryWriter()
                    await plugin.transfers._handle_transfer_client(reader, writer)
                    self.assertEqual("downloading", plugin.transfers.transfer["state"])
                    await asyncio.sleep(0.03)
                    self.assertIsNotNone(plugin.transfers.transfer)
                    reader = asyncio.StreamReader()
                    reader.feed_data(b"GET /secret/file/1 HTTP/1.1\r\nHost: test\r\n\r\n")
                    reader.feed_eof()
                    writer = MemoryWriter()
                    await plugin.transfers._handle_transfer_client(reader, writer)
                    self.assertEqual("downloaded", plugin.transfers.transfer["state"])
                    await asyncio.sleep(0.03)
                    self.assertIsNone(plugin.transfers.transfer)
                finally:
                    await plugin.transfers._stop_transfer()
                    backend.OUTPUT_DIR = previous_output
                    transfer_backend.TRANSFER_COMPLETION_GRACE_SECONDS = previous_grace

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
            join_fragments(streams[0][1], destination, lambda _count: None)
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
            self.assertEqual("Example Game", library_backend._app_names([root])["123"])

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
            names = library_backend._shortcut_names(path)
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
            library_backend.APPINFO_V41_MAGIC + struct.pack("<IQ", 1, string_offset) +
            entry + struct.pack("<I", 0) + table
        )
        self.assertEqual(
            "Crysis Remastered",
            library_backend._appinfo_names_from_data(data, {app_id})[str(app_id)],
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
