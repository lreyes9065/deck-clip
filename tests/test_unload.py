"""Decky sends SIGTERM, then SIGKILL 5 s later: _unload must stop FFmpeg fast.

These tests use real child processes: a fake "ffmpeg" that ignores SIGTERM,
starts a grandchild, and writes partial output, the worst case for shutdown.
"""
import asyncio
import os
import signal
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from test_backend import backend
from test_deck_regressions import write_session
import backend.processes as processes
import backend.transfer as transfer_module

BUDGET = 3.0  # Decky's SIGKILL comes at 5 s; stay well under it.

FAKE_FFMPEG = """#!{python}
import os, signal, subprocess, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)   # Worst case: ignore SIGTERM.
child = subprocess.Popen(["sleep", "60"])         # A grandchild in the same group.
output = sys.argv[-1]
if output.endswith(".mp4"):
    with open(output, "wb") as handle:            # Partial output.
        handle.write(b"partial" * 1000)
with open(os.path.join({pids!r}, str(os.getpid())), "w") as handle:
    handle.write(f"{{os.getpid()}} {{child.pid}}")
print("out_time_ms=1000", flush=True)
time.sleep(60)
"""


def alive(pid: int) -> bool:
    """True if pid exists and is not a zombie waiting for its parent."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        if sys.platform.startswith("linux"):
            state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        else:
            state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    except OSError:
        return False
    return bool(state) and not state.startswith("Z")


async def wait_dead(pids, timeout=2.0):
    """Orphaned grandchildren are reaped by init, which can take a moment."""
    end = time.monotonic() + timeout
    while time.monotonic() < end and any(alive(pid) for pid in pids):
        await asyncio.sleep(0.05)
    return [pid for pid in pids if alive(pid)]


class UnloadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.out = self.root / "Videos" / "DeckClip"
        self.out.mkdir(parents=True)
        self.pids = self.root / "pids"
        self.pids.mkdir()
        self.ffmpeg = self.root / "ffmpeg"
        self.ffmpeg.write_text(FAKE_FFMPEG.format(python=sys.executable, pids=str(self.pids)))
        self.ffmpeg.chmod(0o755)
        self.patches = [
            patch.object(backend, "OUTPUT_DIR", self.out),
            patch.object(backend, "find_ffmpeg", return_value=str(self.ffmpeg)),
            patch.object(processes, "find_ffmpeg", return_value=str(self.ffmpeg)),
            patch("backend.thumbnails.find_ffmpeg", return_value=str(self.ffmpeg)),
            patch.object(transfer_module, "_lan_address", return_value="127.0.0.1"),
        ]
        for item in self.patches:
            item.start()

    async def asyncTearDown(self):
        processes.kill_all()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def started_pids(self):
        pids = []
        for entry in self.pids.iterdir():
            text = entry.read_text().split()
            if len(text) == 2:
                pids.extend(int(value) for value in text)
        return pids

    async def wait_for_children(self, count, timeout=10):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if len(self.started_pids()) >= 2 * count:
                return self.started_pids()
            await asyncio.sleep(0.05)
        self.fail(f"fake ffmpeg did not start {count} time(s): {self.started_pids()} {getattr(self, 'debug', lambda: '')()}")

    def start_export(self, plugin):
        clip = self.root / "clips" / "clip_1245620_20261020_201500"
        write_session(clip / "video" / "fg_1245620_20261020_201312")
        job_id = "unload-test"
        plugin.jobs[job_id] = {
            "started_at": datetime.now(timezone.utc).isoformat(), "state": "queued", "progress": 0.0,
            "output_dir": str(self.out),
            "clips": [{"id": str(clip), "display_name": "ELDEN RING", "progress": 0.0, "state": "queued"}],
        }
        task = asyncio.create_task(plugin._run_export(job_id, [({"id": str(clip), "game_name": "ELDEN RING", "recorded_at": "2026-10-20T20:15:00"}, None)]))
        plugin.tasks.add(task)
        task.add_done_callback(plugin.tasks.discard)
        return job_id

    async def test_unload_stops_export_preview_and_sharing_within_budget(self):
        (self.out / "shared.mp4").write_bytes(os.urandom(8 * 1024 * 1024))
        plugin = backend.Plugin()
        await plugin._main()
        job_id = self.start_export(plugin)
        preview = asyncio.create_task(plugin.get_export_thumbnail("shared.mp4"))
        self.debug = lambda: (plugin.jobs[job_id], preview.done() and preview.result())
        pids = await self.wait_for_children(2)
        staging = [path for path in self.out.iterdir() if path.name.startswith(".deckclip-")]
        self.assertTrue(staging, "export should be staging partial output")

        # A phone that requests a large file and never reads it.
        status = await plugin.start_transfer(["shared.mp4"])
        port = int(status["url"].split(":")[2].split("/")[0])
        token = status["url"].rstrip("/").rsplit("/", 1)[1]
        phone = socket.create_connection(("127.0.0.1", port))
        phone.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        phone.sendall(f"GET /{token}/file/0 HTTP/1.1\r\nHost: deck\r\n\r\n".encode())
        await asyncio.sleep(0.3)
        self.assertTrue(plugin.transfers.clients, "stalled phone should be connected")

        # Another request holds the operation lock; unload must not wait for it.
        await plugin.operation_lock.acquire()
        try:
            started = time.monotonic()
            await plugin._unload()
            elapsed = time.monotonic() - started
            if os.environ.get("DECKCLIP_TIMING"): print(f"_unload took {elapsed:.2f}s", file=sys.stderr)
        finally:
            plugin.operation_lock.release()
            phone.close()
        await asyncio.gather(preview, return_exceptions=True)

        self.assertLess(elapsed, BUDGET, f"_unload took {elapsed:.2f}s")
        self.assertEqual(await wait_dead(pids), [], "child or grandchild processes were left running")
        self.assertEqual(processes.live_processes(), [])
        self.assertEqual(sorted(path.name for path in self.out.iterdir()), ["shared.mp4"],
                         "partial output or staging was left behind")
        self.assertEqual(plugin.jobs[job_id]["state"], "cancelled")
        self.assertIsNone(plugin.transfers.transfer)
        self.assertFalse(plugin.transfers.clients)

    async def test_stop_process_kills_a_child_that_ignores_sigterm(self):
        process = await processes.spawn(str(self.ffmpeg), "pipe:1", stdout=asyncio.subprocess.DEVNULL)
        pids = await self.wait_for_children(1)
        started = time.monotonic()
        await processes.stop_process(process)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, processes.TERM_GRACE + processes.KILL_WAIT + 0.5)
        self.assertEqual(process.returncode, -signal.SIGKILL)
        self.assertEqual(await wait_dead(pids), [], "grandchild survived: process group was not killed")

    async def test_children_run_in_their_own_process_group(self):
        process = await processes.spawn(sys.executable, "-c", "import time; time.sleep(30)")
        try:
            self.assertEqual(os.getpgid(process.pid), process.pid)
            self.assertNotEqual(os.getpgid(process.pid), os.getpgrp())
        finally:
            await processes.stop_process(process)

    async def test_unload_without_work_is_fast_and_main_removes_stale_staging(self):
        stale = self.out / ".deckclip-abc123"
        stale.mkdir()
        (stale / "part-000.mp4").write_bytes(b"partial")
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "keep.mp4").write_bytes(b"keep")
        (self.out / ".deckclip-link").symlink_to(outside, target_is_directory=True)
        (self.out / "done.mp4").write_bytes(b"done")
        plugin = backend.Plugin()
        await plugin._main()
        self.assertFalse(stale.exists())
        self.assertTrue((outside / "keep.mp4").exists(), "must never follow a symlink")
        self.assertTrue((self.out / "done.mp4").exists())
        started = time.monotonic()
        await plugin._unload()
        self.assertLess(time.monotonic() - started, 0.5)


if __name__ == "__main__":
    unittest.main()
