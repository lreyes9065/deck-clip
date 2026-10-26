"""Regressions found on a real Steam Deck with 0.15.1 (backend never loaded)."""
import asyncio
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from test_backend import backend
import backend.library as library_backend
from backend.media import IncompleteRecording, manifest_segments, validate_recording
from backend.paths import DEFAULT_HOME, user_home

PROJECT = Path(__file__).parents[1]

# Steam's own session.mpd layout (FFmpeg DASH muxer output): namespaced,
# one AdaptationSet per stream, shared SegmentTemplate, SegmentTimeline.
STEAM_MPD = """<?xml version="1.0" encoding="utf-8"?>
<MPD xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
\txmlns="urn:mpeg:dash:schema:mpd:2011"
\txmlns:xlink="http://www.w3.org/1999/xlink"
\txsi:schemaLocation="urn:mpeg:DASH:schema:MPD:2011 http://standards.iso.org/ittf/PubliclyAvailableStandards/MPEG-DASH_schema_files/DASH-MPD.xsd"
\tprofiles="urn:mpeg:dash:profile:isoff-live:2011"
\ttype="static"
\tmediaPresentationDuration="PT{duration}S"
\tmaxSegmentDuration="PT3.0S"
\tminBufferTime="PT6.0S">
\t<ProgramInformation>
\t</ProgramInformation>
\t<!-- written by Steam <Representation id="9"> -->
\t<ServiceDescription id="0">
\t</ServiceDescription>
\t<Period id="0" start="PT0.0S">
\t\t<AdaptationSet id="0" contentType="video" startWithSAP="1" segmentAlignment="true" bitstreamSwitching="true" frameRate="60/1" maxWidth="1280" maxHeight="800" par="8:5">
\t\t\t<Representation id="0" mimeType="video/mp4" codecs="avc1.640020" bandwidth="12000000" width="1280" height="800" sar="1:1">
\t\t\t\t<SegmentTemplate timescale="1000000" initialization="init-stream$RepresentationID$.m4s" media="chunk-stream$RepresentationID$-$Number%05d$.m4s" startNumber="{start}">
\t\t\t\t\t<SegmentTimeline>
\t\t\t\t\t\t<S t="0" d="3000000" r="{video_repeat}" />
\t\t\t\t\t</SegmentTimeline>
\t\t\t\t</SegmentTemplate>
\t\t\t</Representation>
\t\t</AdaptationSet>
\t\t<AdaptationSet id="1" contentType="audio" startWithSAP="1" segmentAlignment="true" bitstreamSwitching="true">
\t\t\t<Representation id="1" mimeType="audio/mp4" codecs="mp4a.40.2" bandwidth="128000" audioSamplingRate="48000">
\t\t\t\t<AudioChannelConfiguration schemeIdUri="urn:mpeg:dash:23003:3:audio_channel_configuration:2011" value="2" />
\t\t\t\t<SegmentTemplate timescale="48000" initialization="init-stream$RepresentationID$.m4s" media="chunk-stream$RepresentationID$-$Number%05d$.m4s" startNumber="{start}">
\t\t\t\t\t<SegmentTimeline>
\t\t\t\t\t\t<S t="0" d="144384" r="{audio_repeat}" />
\t\t\t\t\t</SegmentTimeline>
\t\t\t\t</SegmentTemplate>
\t\t\t</Representation>
\t\t</AdaptationSet>
\t</Period>
</MPD>
"""


def write_session(folder: Path, video=range(1, 4), audio=range(1, 4), manifest: str | None = "default"):
    folder.mkdir(parents=True, exist_ok=True)
    for stream, numbers in ((0, video), (1, audio)):
        if numbers is None:
            continue
        (folder / f"init-stream{stream}.m4s").write_bytes(b"init")
        for number in numbers:
            (folder / f"chunk-stream{stream}-{number:05d}.m4s").write_bytes(b"chunk")
    if manifest == "default":
        numbers = list(video or audio or [1])
        count = len(numbers)
        manifest = STEAM_MPD.format(duration=f"{3 * count}.0", start=numbers[0],
                                    video_repeat=count - 1, audio_repeat=count - 1)
    if manifest is not None:
        (folder / "session.mpd").write_text(manifest)
    return folder / "session.mpd"


def age(path: Path, seconds: float = 3600):
    stamp = os.path.getmtime(path) - seconds
    for item in [path, *path.rglob("*")]:
        os.utime(item, (stamp, stamp))


class SteamLayout:
    """A fake Deck home: ~/.local/share/Steam/userdata/<id>/gamerecordings/..."""

    def __init__(self, home: Path):
        self.home = home
        self.steam = home / ".local/share/Steam"
        self.recordings = self.steam / "userdata/123456789/gamerecordings"
        self.clips = self.recordings / "clips"
        (self.steam / "steamapps").mkdir(parents=True)
        (self.steam / "steamapps/appmanifest_1245620.acf").write_text('"AppState"\n{\n\t"appid"\t\t"1245620"\n\t"name"\t\t"ELDEN RING"\n}\n')


def deck_like_decky(home: Path | None):
    """The decky module as Decky provides it, optionally without a home."""
    module = types.SimpleNamespace(logger=types.SimpleNamespace(
        info=lambda *a: None, warning=lambda *a: None, exception=lambda *a: None))
    if home is not None:
        module.DECKY_USER_HOME = str(home)
        module.DECKY_USER = "deck"
    return module


class DeckyRuntimeImportTests(unittest.TestCase):
    """Decky's frozen Python only ships some stdlib modules (root cause of the 0.15.1 hang).

    The import allowlist checks themselves live in test_decky_imports.py.
    """

    def test_media_does_not_use_xml_etree(self):
        source = (PROJECT / "backend" / "media.py").read_text()
        self.assertNotRegex(source, r"(?m)^\s*(import|from)\s+xml\.etree")

    def test_every_backend_module_is_packaged(self):
        script = (PROJECT / "scripts" / "package-decky.sh").read_text()
        verify = (PROJECT / "scripts" / "verify-decky-zip.py").read_text()
        for path in (PROJECT / "backend").glob("*.py"):
            self.assertIn(f"backend/{path.name}", script, path.name)
            self.assertIn(f"DeckClip/backend/{path.name}", verify, path.name)


class ManifestParsingTests(unittest.TestCase):
    def test_reads_steam_manifest_without_xml_library(self):
        with tempfile.TemporaryDirectory() as temp:
            manifest = write_session(Path(temp))
            self.assertEqual(manifest_segments(manifest), {0: {"start": 1, "count": 3}, 1: {"start": 1, "count": 3}})

    def test_garbage_and_truncated_manifests_compare_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            manifest = Path(temp) / "session.mpd"
            for text in ("", "not xml", STEAM_MPD[:400], "<MPD><Period></MPD>", "\x00\xff<MPD"):
                manifest.write_text(text)
                self.assertEqual(manifest_segments(manifest), {}, text[:40])


class ExportGateTests(unittest.TestCase):
    def test_clip_cut_from_background_recording_keeps_its_numbering(self):
        # Steam clips need not start at chunk 1, and the MPD may still say 1.
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            write_session(folder, video=range(41, 51), audio=range(41, 52),
                          manifest=STEAM_MPD.format(duration="30.0", start=1, video_repeat=9, audio_repeat=9))
            warnings = []
            streams = validate_recording(folder, folder / "session.mpd", warnings.append)
            self.assertEqual([0, 1], [stream for stream, _ in streams])
            self.assertTrue(any("starts at segment 41" in warning for warning in warnings), warnings)

    def test_manifest_stream_without_fragments_is_a_warning(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            write_session(folder, audio=None)
            warnings = []
            validate_recording(folder, folder / "session.mpd", warnings.append)
            self.assertIn("manifest lists stream 1 but it has no fragments", warnings)

    def test_only_real_damage_blocks_export(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            write_session(folder, video=[1, 2, 4])
            with self.assertRaisesRegex(IncompleteRecording, "stream 0 is missing segment"):
                validate_recording(folder, folder / "session.mpd")
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            write_session(folder, video=None)
            with self.assertRaisesRegex(IncompleteRecording, "no video"):
                validate_recording(folder, folder / "session.mpd")


class SteamDiscoveryTests(unittest.TestCase):
    """Listing must show every clip 0.15.0 showed: any clip_* folder with a session.mpd."""

    def build(self, layout: SteamLayout):
        clips = layout.clips
        names = {
            # Normal Steam clip, foreground recording session.
            "clip_1245620_20261020_201500": "fg_1245620_20261020_201312",
            # Clip saved from a background recording, numbering kept.
            "clip_1245620_20261019_181000": "bg_1245620_20261019_175900",
            # Downloaded clip and a non-Steam shortcut ID.
            "clip_downloaded_20261018_101010": "fg_0_20261018_101010",
            "clip_3489660928_20261017_090000": "fg_3489660928_20261017_090000",
            # Odd folder names that still start with clip_.
            "clip_1245620_badstamp": "session",
            "clip_weird name (copy)": "fg_x",
        }
        for clip, session in names.items():
            write_session(clips / clip / "video" / session)
        # Background recording session with a broken manifest and a gap.
        write_session(clips / "clip_1245620_20261019_181000" / "video" / "bg_1245620_20261019_180500",
                      video=[7, 9], manifest="not xml")
        # A clip whose manifest disagrees with its chunks.
        write_session(clips / "clip_1245620_20261016_120000" / "video" / "fg_1245620_20261016_115900",
                      video=range(5, 8), audio=range(5, 12),
                      manifest=STEAM_MPD.format(duration="99.0", start=1, video_repeat=40, audio_repeat=40))
        # Missing manifest entirely (0.15.0 did not list this either).
        write_session(clips / "clip_1245620_20261015_120000" / "video" / "fg_1", manifest=None)
        # Background recordings live under video/, not clips/, and are not listed.
        write_session(layout.recordings / "video" / "bg_1245620_20261020_190000")
        # Not a clip folder.
        (clips / "thumbnail.jpg").write_bytes(b"jpg")
        for clip in clips.iterdir():
            age(clip)
        return names

    def discover(self, home: Path | None, env_home: str | None = None):
        env = {key: value for key, value in os.environ.items() if key not in ("DECKCLIP_STEAM_ROOT", "DECKY_USER_HOME")}
        if env_home is not None:
            env["DECKY_USER_HOME"] = env_home
        env.pop("HOME", None)
        module = deck_like_decky(home)
        with patch.dict(os.environ, env, clear=True), patch.object(library_backend, "decky", module), \
                patch.dict(sys.modules, {"decky": module}), \
                patch("backend.paths.DEFAULT_HOME", Path("/nonexistent-deck-home")):
            return library_backend._discover()

    def expected_ids(self, layout: SteamLayout):
        return {str((layout.clips / name).resolve()) for name in [
            "clip_1245620_20261020_201500", "clip_1245620_20261019_181000", "clip_downloaded_20261018_101010",
            "clip_3489660928_20261017_090000", "clip_1245620_badstamp", "clip_weird name (copy)",
            "clip_1245620_20261016_120000"]}

    def test_realistic_steam_layout_lists_every_clip_with_a_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            layout = SteamLayout(Path(temp))
            self.build(layout)
            found = self.discover(layout.home)
            self.assertEqual({clip["id"] for clip in found}, self.expected_ids(layout))
            by_name = {Path(clip["id"]).name: clip for clip in found}
            self.assertEqual(by_name["clip_1245620_20261020_201500"]["game_name"], "ELDEN RING")
            self.assertEqual(by_name["clip_1245620_20261019_181000"]["session_count"], 2)
            self.assertEqual(by_name["clip_downloaded_20261018_101010"]["game_name"], "Steam clip")
            self.assertFalse(any(clip["saving"] for clip in found))

    def test_home_from_environment_only(self):
        with tempfile.TemporaryDirectory() as temp:
            layout = SteamLayout(Path(temp))
            self.build(layout)
            found = self.discover(None, env_home=str(layout.home))
            self.assertEqual({clip["id"] for clip in found}, self.expected_ids(layout))

    def test_empty_or_root_decky_home_falls_back_to_decky_helper(self):
        with tempfile.TemporaryDirectory() as temp:
            layout = SteamLayout(Path(temp))
            self.build(layout)
            for bad in ("", "   ", "/", "/root", "relative/home"):
                found = self.discover(layout.home, env_home=bad)
                self.assertEqual({clip["id"] for clip in found}, self.expected_ids(layout), repr(bad))

    def test_one_unreadable_clip_never_fails_the_list(self):
        with tempfile.TemporaryDirectory() as temp:
            layout = SteamLayout(Path(temp))
            self.build(layout)
            real = library_backend._duration_from_mpd
            def broken(path):
                if "20261020" in str(path):
                    raise RuntimeError("unexpected")
                return real(path)
            with patch.object(library_backend, "_duration_from_mpd", side_effect=broken), \
                    patch.object(library_backend, "_app_names", side_effect=RuntimeError("bad appinfo")):
                found = self.discover(layout.home)
            self.assertEqual(len(found), len(self.expected_ids(layout)) - 1)
            self.assertTrue(all(clip["game_name"].startswith("Steam") for clip in found))

    def test_plugin_lists_clips_through_main(self):
        async def scenario(home):
            module = deck_like_decky(home)
            with patch.object(library_backend, "decky", module), patch.dict(sys.modules, {"decky": module}), \
                    patch.dict(os.environ, {"DECKY_USER_HOME": str(home)}):
                os.environ.pop("DECKCLIP_STEAM_ROOT", None)
                plugin = backend.Plugin()
                await plugin._main()
                try:
                    return await plugin.list_clips()
                finally:
                    await plugin._unload()
        with tempfile.TemporaryDirectory() as temp:
            layout = SteamLayout(Path(temp))
            self.build(layout)
            saved = os.environ.get("DECKCLIP_STEAM_ROOT")
            try:
                found = asyncio.run(scenario(layout.home))
            finally:
                if saved is not None:
                    os.environ["DECKCLIP_STEAM_ROOT"] = saved
            self.assertEqual({clip["id"] for clip in found}, self.expected_ids(layout))


class UserHomeTests(unittest.TestCase):
    def resolve(self, env: dict, decky_module):
        base = {key: value for key, value in os.environ.items() if key not in ("DECKY_USER_HOME", "DECKY_USER", "HOME")}
        base.update(env)
        with patch.dict(os.environ, base, clear=True), patch.dict(sys.modules, {"decky": decky_module}):
            return user_home()

    def test_order_and_fallbacks(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            helper = types.SimpleNamespace(DECKY_USER_HOME=second)
            self.assertEqual(self.resolve({"DECKY_USER_HOME": first}, helper), Path(first))
            self.assertEqual(self.resolve({}, helper), Path(second))
            self.assertEqual(self.resolve({"DECKY_USER_HOME": ""}, helper), Path(second))
            self.assertEqual(self.resolve({"DECKY_USER_HOME": "/"}, helper), Path(second))
            self.assertEqual(self.resolve({"DECKY_USER_HOME": "/root", "HOME": "/root"}, helper), Path(second))
            # Set but missing on disk: an existing later candidate wins.
            self.assertEqual(self.resolve({"DECKY_USER_HOME": first + "/missing"}, helper), Path(second))
            self.assertEqual(self.resolve({"HOME": first}, types.SimpleNamespace()), Path(first))

    def test_defaults_to_deck_home(self):
        self.assertEqual(self.resolve({"HOME": "/root"}, types.SimpleNamespace()), DEFAULT_HOME)
        self.assertEqual(self.resolve({}, types.SimpleNamespace(DECKY_USER_HOME=None)), DEFAULT_HOME)

    def test_main_imports_without_any_decky_home(self):
        loader = """
import importlib.util, sys, types
sys.modules['decky'] = types.SimpleNamespace(logger=types.SimpleNamespace(info=lambda *a: None, exception=lambda *a: None))
spec = importlib.util.spec_from_file_location('deckclip_nohome', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
print(module.OUTPUT_DIR)
"""
        env = {key: value for key, value in os.environ.items() if key not in ("DECKY_USER_HOME", "DECKY_USER")}
        env["HOME"] = "/root"
        result = subprocess.run([sys.executable, "-c", loader, str(PROJECT / "main.py")],
                                capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "/home/deck/Videos/DeckClip")


if __name__ == "__main__":
    unittest.main()
