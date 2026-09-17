import asyncio
import os
import sys
import tempfile
import types
import shutil
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from test_backend import backend, fake_decky
from backend.exports import export_path, open_export, publish_export
from backend.history import load_history, save_history
from backend.processes import run_ffmpeg, MAX_STDERR
from backend.media import assemble_fragments
from backend.thumbnails import Thumbnails
from backend.transfer import TransferManager
import backend.transfer as transfer_module


class Writer:
    def __init__(self, stalled=False):
        self.data = bytearray()
        self.closed = False
        self.stalled = stalled
        self.started = asyncio.Event()

    def write(self, data): self.data.extend(data)
    def close(self): self.closed = True
    async def wait_closed(self): pass
    async def drain(self):
        self.started.set()
        if self.stalled:
            await asyncio.Event().wait()


class FakeServer:
    def __init__(self):
        self.closed = False
        self.sockets = [types.SimpleNamespace(getsockname=lambda: ("127.0.0.1", 1234))]
    def close(self): self.closed = True
    async def wait_closed(self): pass


class HardeningTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_publication_never_overwrites_and_suffixes_are_stable(self):
        stage = self.root / "stage"
        stage.write_bytes(b"complete")
        target = self.root / "clip.mp4"
        target.write_bytes(b"original")
        (self.root / "clip (2).mp4").symlink_to(target)
        published = publish_export(stage, target)
        self.assertEqual(published.name, "clip (3).mp4")
        self.assertEqual(target.read_bytes(), b"original")
        self.assertEqual(published.read_bytes(), b"complete")

    async def test_failed_concat_is_never_listed_and_staging_is_removed(self):
        clip_dir = self.root / "source"
        for number in range(2):
            session = clip_dir / "video" / str(number) / "session.mpd"
            session.parent.mkdir(parents=True)
            session.write_text("fixture")
        async def remux(manifest, output, progress, stage): output.write_bytes(b"part")
        async def concat(command):
            Path(command[-1]).write_bytes(b"partial")
            raise RuntimeError("concat failed")
        plugin = backend.Plugin()
        plugin._remux_session = remux
        plugin._run = concat
        job = {"clips": [{"state": "queued", "progress": 0}]}
        clip = {"id": str(clip_dir), "game_name": "Test", "recorded_at": "2026-09-16T12:00:00"}
        with patch.object(backend, "OUTPUT_DIR", self.root):
            with self.assertRaisesRegex(RuntimeError, "concat failed"):
                await plugin._export_one(job, 0, clip, "test")
            self.assertEqual(backend._list_exports(), [])
        self.assertEqual(list(self.root.glob(".deckclip-*")), [])

    async def test_open_rejects_replaced_symlink_and_fifo(self):
        path = self.root / "clip.mp4"
        path.write_bytes(b"original")
        validated = export_path(self.root, path.name)
        path.unlink()
        path.symlink_to(self.root / "private")
        with self.assertRaises(OSError): open_export(validated)
        path.unlink()
        os.mkfifo(path)
        with self.assertRaises(ValueError): open_export(path)

    async def test_stderr_is_drained_and_bounded(self):
        with self.assertRaises(RuntimeError) as result:
            await asyncio.wait_for(run_ffmpeg([sys.executable, "-c", "import sys; sys.stderr.write('x'*1000000+'TAIL'); sys.exit(1)"]), 5)
        self.assertIn("omitted", str(result.exception))
        self.assertTrue(str(result.exception).endswith("TAIL"))
        self.assertLess(len(str(result.exception)), MAX_STDERR + 100)

    async def test_cancel_reaps_subprocess(self):
        ready = asyncio.Event()
        pid = []
        def progress(line):
            pid.append(int(line))
            ready.set()
        task = asyncio.create_task(run_ffmpeg([sys.executable, "-c", "import os,time; print(os.getpid(),flush=True); time.sleep(30)"], progress))
        try:
            await asyncio.wait_for(ready.wait(), 5)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
            with self.assertRaises(ProcessLookupError): os.kill(pid[0], 0)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def start_manager(self):
        (self.root / "clip.mp4").write_bytes(b"video")
        manager = TransferManager(lambda name: export_path(self.root, name), fake_decky.logger)
        with patch.object(transfer_module, "_lan_address", return_value="127.0.0.1"), patch.object(asyncio, "start_server", new_callable=AsyncMock, return_value=FakeServer()):
            await manager.start_transfer(["clip.mp4"])
        return manager

    async def request(self, manager, suffix, writer=None, headers=""):
        reader = asyncio.StreamReader()
        reader.feed_data(f"GET /{manager.transfer['token']}/{suffix} HTTP/1.1\r\nHost: test\r\n{headers}\r\n".encode())
        reader.feed_eof()
        writer = writer or Writer()
        await manager._handle_transfer_client(reader, writer)
        return writer

    async def test_stop_cancels_stalled_clients(self):
        manager = await self.start_manager()
        writer = Writer(stalled=True)
        request = asyncio.create_task(self.request(manager, "file/0", writer))
        await asyncio.wait_for(writer.started.wait(), 2)
        await asyncio.wait_for(manager.stop_transfer(), 3)
        self.assertTrue(writer.closed)
        self.assertTrue(request.cancelled())
        self.assertFalse(manager.clients)
        self.assertIsNone(manager.transfer)

    async def test_replaced_file_and_oversized_range_are_rejected(self):
        manager = await self.start_manager()
        try:
            writer = await self.request(manager, "file/0", headers="Range: bytes=" + "9" * 100 + "-\r\n")
            self.assertIn(b"416 Range", writer.data)
            replacement = self.root / "new"
            replacement.write_bytes(b"private replacement")
            replacement.replace(self.root / "clip.mp4")
            writer = await self.request(manager, "file/0")
            self.assertIn(b"410 Gone", writer.data)
            self.assertNotIn(b"private replacement", writer.data)
        finally:
            await manager.stop_transfer()

    async def test_concurrent_starts_close_previous_listener(self):
        manager = await self.start_manager()
        old = manager.transfer["server"]
        servers = [FakeServer(), FakeServer()]
        try:
            with patch.object(transfer_module, "_lan_address", return_value="127.0.0.1"), patch.object(asyncio, "start_server", new_callable=AsyncMock, side_effect=servers):
                await asyncio.gather(manager.start_transfer(["clip.mp4"]), manager.start_transfer(["clip.mp4"]))
            self.assertTrue(old.closed)
            self.assertTrue(servers[0].closed)
            self.assertFalse(servers[1].closed)
        finally:
            await manager.stop_transfer()
        self.assertTrue(servers[1].closed)

    async def test_history_survives_restart_and_marks_interrupted_jobs(self):
        jobs = {str(i): {"state": "running", "started_at": "now", "output_dir": "exports", "progress": 12, "clips": []} for i in range(8)}
        save_history(self.root, jobs)
        loaded = load_history(self.root)
        self.assertEqual(list(loaded), ["3", "4", "5", "6", "7"])
        self.assertTrue(all(job["state"] == "interrupted" for job in loaded.values()))
        self.assertEqual((self.root / "recent-exports.json").stat().st_mode & 0o777, 0o600)
        (self.root / "recent-exports.json").write_text("broken json")
        self.assertEqual(load_history(self.root), {})

    async def test_duplicate_export_requests_are_rejected(self):
        plugin = backend.Plugin()
        await plugin._main()
        plugin.list_clips = AsyncMock(return_value=[{"id": "test", "game_name": "Test"}])
        plugin._run_export = AsyncMock()
        try:
            results = await asyncio.gather(plugin.start_export([{"id": "test"}]), plugin.start_export([{"id": "test"}]), return_exceptions=True)
            self.assertEqual(sum(isinstance(result, ValueError) for result in results), 1)
            self.assertEqual(len(plugin.jobs), 1)
        finally:
            await plugin._unload()

    async def test_fragment_cancellation_waits_for_worker_exit(self):
        started = threading.Event()
        finished = threading.Event()
        def worker(files, destination, report, stop):
            started.set()
            stop.wait(5)
            finished.set()
        with patch("backend.media.join_fragments", side_effect=worker):
            task = asyncio.create_task(assemble_fragments([], self.root / "out", lambda count: None))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 3)
                self.assertTrue(finished.is_set())
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    async def test_stalled_writer_times_out(self):
        manager = await self.start_manager()
        writer = Writer(stalled=True)
        try:
            with patch.object(transfer_module, "WRITE_TIMEOUT", 0.01):
                await asyncio.wait_for(self.request(manager, "file/0", writer), 2)
            self.assertTrue(writer.closed)
            self.assertFalse(manager.clients)
        finally:
            await manager.stop_transfer()

    async def test_client_limit_rejects_excess_connections(self):
        manager = await self.start_manager()
        requests = []
        try:
            with patch.object(transfer_module, "MAX_CLIENTS", 1):
                first = Writer(stalled=True)
                requests.append(asyncio.create_task(self.request(manager, "file/0", first)))
                await asyncio.wait_for(first.started.wait(), 2)
                second = await self.request(manager, "file/0")
                self.assertTrue(second.closed)
                self.assertEqual(second.data, b"")
                self.assertEqual(len(manager.clients), 1)
        finally:
            await manager.stop_transfer()
            await asyncio.gather(*requests, return_exceptions=True)

    async def test_invalid_export_batches_fail_before_discovery(self):
        plugin = backend.Plugin()
        await plugin._main()
        plugin.list_clips = AsyncMock()
        try:
            for items in (None, [], [{}], [{"id": "x", "name": 123}], [{"id": "x"}] * 101, [{"id": "x"}] * 2):
                with self.assertRaises(ValueError):
                    await plugin.start_export(items)
            plugin.list_clips.assert_not_awaited()
        finally:
            await plugin._unload()

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required for preview integration")
    async def test_thumbnail_reads_pinned_descriptor(self):
        clip = self.root / "clip.mp4"
        await run_ffmpeg(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=160x90:d=0.2", "-c:v", "mpeg4", str(clip)])
        previews = Thumbnails(lambda name: export_path(self.root, name))
        try:
            preview = await previews.get(clip.name)
            self.assertIsNotNone(preview)
            self.assertTrue(preview.startswith("data:image/jpeg;base64,"))
        finally:
            await previews.close()
