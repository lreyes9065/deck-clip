import asyncio
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_backend import backend
from backend.source_preview import open_source_preview
from backend.processes import run_ffmpeg
from backend.thumbnails import Thumbnails


class SourcePreviewTests(unittest.TestCase):
    def test_bounded_read_and_symlink_rejection(self):
        with tempfile.TemporaryDirectory() as temp:
            clip = Path(temp)
            session = clip / "video" / "session"
            session.mkdir(parents=True)
            (session / "session.mpd").write_text("fixture")
            (session / "init-stream0.m4s").write_bytes(b"init")
            chunk = session / "chunk-stream0-00001.m4s"
            chunk.write_bytes(b"chunk")
            with open_source_preview(clip) as stream:
                self.assertEqual(stream.read(), b"initchunk")
            with patch("backend.source_preview.MAX_PREVIEW_INPUT", 5):
                with self.assertRaises(ValueError): open_source_preview(clip)
            chunk.unlink()
            chunk.symlink_to(session / "init-stream0.m4s")
            with self.assertRaises(OSError): open_source_preview(clip)

    def test_api_rejects_unknown_source_paths(self):
        async def scenario():
            plugin = backend.Plugin()
            await plugin._main()
            try:
                with self.assertRaises(ValueError):
                    await plugin.get_clip_thumbnail("/etc/passwd")
            finally:
                await plugin._unload()
        asyncio.run(scenario())

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
    def test_real_fragmented_recording_preview_is_read_only_and_cached(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as temp:
                clip = Path(temp)
                session = clip / "video" / "session"
                session.mkdir(parents=True)
                await run_ffmpeg([
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                    "-i", "testsrc=size=320x180:rate=30:duration=3", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-g", "30", "-seg_duration", "1",
                    "-init_seg_name", "init-stream$RepresentationID$.m4s",
                    "-media_seg_name", "chunk-stream$RepresentationID$-$Number%05d$.m4s",
                    "-f", "dash", str(session / "session.mpd"),
                ])
                before = {path: path.read_bytes() for path in session.iterdir()}
                previews = Thumbnails(lambda name: None)
                try:
                    result = await previews.get(str(clip), source_path=clip)
                    self.assertIsNotNone(result)
                    self.assertTrue(result.startswith("data:image/jpeg;base64,"))
                    with patch("backend.thumbnails.open_source_preview", side_effect=AssertionError("Cache missed")):
                        self.assertEqual(await previews.get(str(clip), source_path=clip), result)
                    self.assertEqual(before, {path: path.read_bytes() for path in session.iterdir()})
                finally:
                    await previews.close()
        asyncio.run(scenario())
