import importlib.util
import os
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


if __name__ == "__main__":
    unittest.main()
