"""Regression tests for the review fixes (previews, exports, discovery, naming)."""
import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from test_backend import backend
import backend.library as library_backend
import backend.thumbnails as thumbnails_backend
from backend.exports import MAX_NAME_BYTES, safe_output_name
from backend.media import IncompleteRecording, validate_recording
from backend.process_env import find_ffmpeg
from backend.source_preview import open_source_preview

HAS_FFMPEG = shutil.which("ffmpeg") is not None


def make_dash(session: Path, seconds: int = 3, audio: bool = False, timeline: bool = True):
    session.mkdir(parents=True, exist_ok=True)
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
               "-f", "lavfi", "-i", f"testsrc=size=320x180:rate=30:duration={seconds}"]
    if audio:
        command += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:a", "aac"]
    command += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "30", "-seg_duration", "1",
                "-use_template", "1", "-use_timeline", "1" if timeline else "0",
                "-init_seg_name", "init-stream$RepresentationID$.m4s",
                "-media_seg_name", "chunk-stream$RepresentationID$-$Number%05d$.m4s"]
    if audio:
        command += ["-adaptation_sets", "id=0,streams=v id=1,streams=a"]
    command += ["-f", "dash", str(session / "session.mpd")]
    subprocess.run(command, check=True)
    return session / "session.mpd"


def write_fragments(folder: Path, stream: int, numbers):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"init-stream{stream}.m4s").write_bytes(b"init")
    for number in numbers:
        (folder / f"chunk-stream{stream}-{number:05d}.m4s").write_bytes(b"chunk")


MPD = """<?xml version="1.0"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static" mediaPresentationDuration="PT3S">
 <Period><AdaptationSet id="0" contentType="video">
  <Representation id="0"><SegmentTemplate timescale="1000" startNumber="{start}"
    initialization="init-stream$RepresentationID$.m4s" media="chunk-stream$RepresentationID$-$Number%05d$.m4s">
   <SegmentTimeline><S t="0" d="1000" r="{repeat}"/></SegmentTimeline></SegmentTemplate></Representation>
 </AdaptationSet></Period></MPD>"""


class SteamRoot:
    def __init__(self, root: Path):
        self.root = root
        self.clips = root / "userdata/42/gamerecordings/clips"

    def __enter__(self):
        self.old = os.environ.get("DECKCLIP_STEAM_ROOT")
        os.environ["DECKCLIP_STEAM_ROOT"] = str(self.root)
        return self

    def __exit__(self, *exc):
        if self.old is None:
            os.environ.pop("DECKCLIP_STEAM_ROOT", None)
        else:
            os.environ["DECKCLIP_STEAM_ROOT"] = self.old


def age(path: Path, seconds: float = 3600):
    stamp = os.path.getmtime(path) - seconds
    for item in [path, *path.rglob("*")]:
        os.utime(item, (stamp, stamp))


class PreviewTests(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(HAS_FFMPEG, "FFmpeg required")
    async def test_clip_thumbnail_uses_source_preview_for_scanned_clip(self):
        with tempfile.TemporaryDirectory() as temp, SteamRoot(Path(temp)) as steam:
            clip = steam.clips / "clip_123_20260101_120000"
            await asyncio.to_thread(make_dash, clip / "video" / "fg_123_20260101_120000")
            plugin = backend.Plugin()
            await plugin._main()
            try:
                clips = await plugin.list_clips()
                self.assertEqual([entry["id"] for entry in clips], [str(clip.resolve())])
                with patch("backend.thumbnails.open_source_preview", wraps=open_source_preview) as spy:
                    result = await plugin.get_clip_thumbnail(clips[0]["id"])
                spy.assert_called_once_with(Path(clips[0]["id"]))
                self.assertEqual(result["status"], "ready")
                self.assertTrue(result["image"].startswith("data:image/jpeg;base64,"))
            finally:
                await plugin._unload()

    async def test_full_preview_queue_reports_busy_instead_of_blank(self):
        plugin = backend.Plugin()
        await plugin._main()
        try:
            with patch.object(thumbnails_backend, "MAX_IN_FLIGHT", 0):
                self.assertEqual(await plugin.get_export_thumbnail("clip.mp4"), {"status": "busy"})
                self.assertIs(await plugin.thumbnails.get("clip.mp4"), thumbnails_backend.THUMBNAIL_BUSY)
        finally:
            await plugin._unload()

    async def test_missing_export_preview_is_ready_without_image(self):
        plugin = backend.Plugin()
        await plugin._main()
        try:
            plugin.thumbnails.resolve = lambda name: (_ for _ in ()).throw(ValueError("gone"))
            with self.assertRaises(ValueError):
                await plugin.get_export_thumbnail("gone.mp4")
        finally:
            await plugin._unload()


class CompletenessTests(unittest.TestCase):
    def test_accepts_contiguous_matching_streams(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            write_fragments(folder, 0, [1, 2, 3])
            write_fragments(folder, 1, [1, 2, 3])
            self.assertEqual([0, 1], [stream for stream, _ in validate_recording(folder)])

    def test_rejects_gap(self):
        with tempfile.TemporaryDirectory() as temp:
            write_fragments(Path(temp), 0, [1, 3])
            with self.assertRaisesRegex(IncompleteRecording, "missing segment.*2"):
                validate_recording(Path(temp))

    def test_rejects_audio_video_count_mismatch(self):
        with tempfile.TemporaryDirectory() as temp:
            write_fragments(Path(temp), 0, [1, 2, 3])
            write_fragments(Path(temp), 1, [1, 2])
            validate_recording(Path(temp))  # off by one: normal audio frame alignment
            for chunk in Path(temp).glob("chunk-stream1-00002.m4s"):
                chunk.unlink()
            warnings = []
            validate_recording(Path(temp), warn=warnings.append)  # 0.15.0 exported these; warn only
            self.assertEqual(warnings, ["stream 1 has 1 segments but video has 3"])

    def test_compares_against_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            write_fragments(folder, 0, [1, 2, 3])
            manifest = folder / "session.mpd"
            manifest.write_text(MPD.format(start=1, repeat=2))
            validate_recording(folder, manifest)
            # Real Steam manifests can disagree with their chunks; these warn, not block.
            manifest.write_text(MPD.format(start=1, repeat=3))
            warnings = []
            validate_recording(folder, manifest, warnings.append)
            self.assertTrue(any("manifest lists 4" in warning for warning in warnings), warnings)
            manifest.write_text(MPD.format(start=2, repeat=2))
            warnings = []
            validate_recording(folder, manifest, warnings.append)
            self.assertTrue(any("manifest says 2" in warning for warning in warnings), warnings)
            manifest.write_text("not xml")
            validate_recording(folder, manifest)

    @unittest.skipUnless(HAS_FFMPEG, "FFmpeg required")
    def test_real_dash_manifest_and_missing_chunk(self):
        with tempfile.TemporaryDirectory() as temp:
            manifest = make_dash(Path(temp) / "session", seconds=4, audio=True)
            streams = validate_recording(manifest.parent, manifest)
            self.assertEqual([0, 1], [stream for stream, _ in streams])
            streams[0][1][2].unlink()
            with self.assertRaises(IncompleteRecording):
                validate_recording(manifest.parent, manifest)


class DiscoveryTests(unittest.TestCase):
    def test_sessions_sort_by_timestamped_folder_name(self):
        with tempfile.TemporaryDirectory() as temp:
            clip = Path(temp)
            later = clip / "video/fg_1_20260102_000000/session.mpd"
            earlier = clip / "video/fg_1_20260101_000000/session.mpd"
            for manifest in (earlier, later):
                manifest.parent.mkdir(parents=True)
                manifest.write_text("x")
            os.utime(earlier, (2_000_000_000, 2_000_000_000))  # newer mtime, older name
            self.assertEqual(library_backend.sorted_sessions(clip), [earlier, later])

    def test_vanishing_clip_is_skipped_and_fresh_clip_is_flagged_saving(self):
        with tempfile.TemporaryDirectory() as temp, SteamRoot(Path(temp)) as steam:
            for name in ("clip_1_20260101_120000", "clip_2_20260102_120000", "clip_3_20260103_120000"):
                manifest = steam.clips / name / "video/fg_1/session.mpd"
                manifest.parent.mkdir(parents=True)
                manifest.write_text('<MPD mediaPresentationDuration="PT1S"/>')
            age(steam.clips / "clip_1_20260101_120000")
            age(steam.clips / "clip_3_20260103_120000")
            real = library_backend.sorted_sessions
            def flaky(clip_dir):
                if clip_dir.name.startswith("clip_3"):
                    raise FileNotFoundError("deleted mid-scan")
                return real(clip_dir)
            with patch.object(library_backend, "sorted_sessions", side_effect=flaky):
                found = library_backend._discover()
            self.assertEqual({Path(clip["id"]).name: clip["saving"] for clip in found},
                             {"clip_1_20260101_120000": False, "clip_2_20260102_120000": True})

    def test_export_refuses_clip_still_saving(self):
        async def scenario():
            plugin = backend.Plugin()
            await plugin._main()
            plugin.list_clips = AsyncMock(return_value=[{"id": "x", "game_name": "Game", "saving": True}])
            try:
                with self.assertRaisesRegex(ValueError, "still being saved"):
                    await plugin.start_export([{"id": "x"}])
            finally:
                await plugin._unload()
        asyncio.run(scenario())


class ExportTests(unittest.IsolatedAsyncioTestCase):
    async def test_filename_is_capped_in_utf8_bytes(self):
        clip = {"game_name": "Game", "recorded_at": "2026-01-01T12:00:00+00:00"}
        for requested in ("あ" * 300, "a" * 300, "ゲーム" * 40 + ".mp4"):
            name = safe_output_name(requested, clip)
            stem = name[:-4]
            self.assertTrue(name.endswith(".mp4"))
            self.assertLessEqual(len(stem.encode("utf-8")), MAX_NAME_BYTES)
            self.assertLessEqual(len(f"{stem} (10000).mp4".encode("utf-8")), 255)
            stem.encode("utf-8").decode("utf-8")
        self.assertEqual(safe_output_name("short", clip), "short.mp4")

    async def test_active_export_is_reported_for_resume(self):
        plugin = backend.Plugin()
        plugin.jobs = {"old": {"state": "complete", "clips": []}, "now": {"state": "running", "clips": [], "progress": 5}}
        active = await plugin.get_active_export()
        self.assertEqual(active["job_id"], "now")
        self.assertEqual(active["job"]["progress"], 5)
        active["job"]["progress"] = 99
        self.assertEqual(plugin.jobs["now"]["progress"], 5)
        plugin.jobs["now"]["state"] = "complete"
        self.assertIsNone(await plugin.get_active_export())

    @unittest.skipUnless(HAS_FFMPEG, "FFmpeg required")
    async def test_multi_session_export_never_stages_joined_stream_copies(self):
        with tempfile.TemporaryDirectory() as source_name, tempfile.TemporaryDirectory() as output_name:
            clip_dir = Path(source_name) / "clip_123_20260101_120000"
            for stamp in ("20260101_120000", "20260101_120100"):
                await asyncio.to_thread(make_dash, clip_dir / "video" / f"fg_123_{stamp}", seconds=2, audio=True)
            output_dir = Path(output_name)
            snapshots = []
            real_run = backend.run_ffmpeg
            async def spy(command, *args, **kwargs):
                snapshots.append((list(command), sorted(p.name for p in output_dir.glob(".deckclip-*/*"))))
                return await real_run(command, *args, **kwargs)
            plugin = backend.Plugin()
            clip = {"id": str(clip_dir), "game_name": "Game", "recorded_at": "2026-01-01T12:00:00+00:00"}
            job = {"clips": [{"state": "queued", "progress": 0}], "progress": 0}
            with patch.object(backend, "OUTPUT_DIR", output_dir), patch.object(backend, "run_ffmpeg", side_effect=spy):
                await plugin._export_one(job, 0, clip, "result")
            self.assertEqual(len(snapshots), 3)  # two session remuxes, one concat
            for command, staged in snapshots[:2]:
                inputs = [command[i + 1] for i, value in enumerate(command) if value == "-i"]
                self.assertTrue(inputs and all(value.startswith("pipe:") for value in inputs), command)
            self.assertEqual(snapshots[0][1], [])
            self.assertEqual(snapshots[1][1], ["part-000.mp4"])
            self.assertEqual(snapshots[2][1], ["concat.txt", "part-000.mp4", "part-001.mp4"])
            self.assertEqual([p.name for p in output_dir.iterdir()], ["result.mp4"])
            self.assertEqual(job["clips"][0]["state"], "complete")

    async def test_export_rejects_incomplete_recording_before_staging(self):
        with tempfile.TemporaryDirectory() as source_name, tempfile.TemporaryDirectory() as output_name:
            session = Path(source_name) / "video" / "fg_1_20260101_120000"
            write_fragments(session, 0, [1, 2, 4])
            (session / "session.mpd").write_text("x")
            plugin = backend.Plugin()
            plugin._remux_session = AsyncMock()
            job = {"clips": [{"state": "queued", "progress": 0}], "progress": 0}
            with patch.object(backend, "OUTPUT_DIR", Path(output_name)):
                with self.assertRaisesRegex(RuntimeError, "incomplete.*missing segment"):
                    await plugin._export_one(job, 0, {"id": source_name, "game_name": "G", "recorded_at": "2026-01-01T12:00:00"}, None)
            plugin._remux_session.assert_not_awaited()
            self.assertEqual(list(Path(output_name).iterdir()), [])


class EnvironmentTests(unittest.TestCase):
    def test_output_dir_follows_decky_user_home(self):
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home, True)
        loader = """
import importlib.util, sys, types
sys.modules['decky'] = types.SimpleNamespace(DECKY_USER_HOME=sys.argv[2],
    logger=types.SimpleNamespace(info=lambda *a: None, exception=lambda *a: None))
spec = importlib.util.spec_from_file_location('deckclip_home', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
print(module.OUTPUT_DIR)
"""
        env = {key: value for key, value in os.environ.items() if key != "DECKY_USER_HOME"}
        result = subprocess.run([sys.executable, "-c", loader, str(Path(__file__).parents[1] / "main.py"), home],
                                capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), f"{home}/Videos/DeckClip")

    def test_find_ffmpeg_prefers_user_local_bin_then_path(self):
        with tempfile.TemporaryDirectory() as home:
            with patch.dict(os.environ, {"DECKY_USER_HOME": home}), patch("shutil.which", return_value="/usr/bin/ffmpeg"):
                self.assertEqual(find_ffmpeg(), "/usr/bin/ffmpeg")
                binary = Path(home) / ".local/bin/ffmpeg"
                binary.parent.mkdir(parents=True)
                binary.write_text("#!/bin/sh\n")
                self.assertEqual(find_ffmpeg(), "/usr/bin/ffmpeg")  # not executable yet
                binary.chmod(0o755)
                self.assertEqual(find_ffmpeg(), str(binary))
            with patch.dict(os.environ, {"DECKY_USER_HOME": home}), patch("shutil.which", return_value=None):
                binary.unlink()
                self.assertIsNone(find_ffmpeg())


if __name__ == "__main__":
    unittest.main()
