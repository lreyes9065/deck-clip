import asyncio
import importlib.util
import json
import os
import shutil
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
                found = backend._discover()
            finally:
                if old is None: os.environ.pop("DECKCLIP_STEAM_ROOT", None)
                else: os.environ["DECKCLIP_STEAM_ROOT"] = old
            self.assertEqual(3, len(found))
            self.assertEqual("clip_123_20260104_120000", Path(found[0]["id"]).name)
            self.assertEqual(62.5, found[0]["duration_seconds"])

    def test_sanitizes_requested_name(self):
        clip = {"game_name": "Game", "recorded_at": "2026-01-01T12:00:00+00:00"}
        self.assertEqual("my_clip.mp4", backend._safe_output_name("../my/clip.mp4", clip))

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
