"""Owned subprocess lifetimes and bounded, concurrently drained diagnostics.

Every child is started in its own session (process group), so stopping it
also stops anything it spawned. Decky sends SIGTERM to the plugin and
SIGKILL 5 seconds later, so stopping is bounded: SIGTERM, a short grace
period, then SIGKILL.
"""
import asyncio
import os
import signal

from .process_env import find_ffmpeg, system_tool_env

MAX_STDERR = 128 * 1024
# Grace after SIGTERM before SIGKILL, and how long to wait for the reap after.
TERM_GRACE = 1.0
KILL_WAIT = 0.5

# Children still running. _unload signals all of them at once.
# Keyed by id(): process objects need not be hashable.
_live: dict = {}


async def spawn(*command, **kwargs):
    """asyncio.create_subprocess_exec in a new process group, tracked for shutdown."""
    kwargs.setdefault("start_new_session", True)
    process = await asyncio.create_subprocess_exec(*command, **kwargs)
    process._deckclip_group = bool(kwargs["start_new_session"])
    _live[id(process)] = process
    return process


def live_processes() -> list:
    for key, process in list(_live.items()):
        if process.returncode is not None:
            del _live[key]
    return list(_live.values())


def _signal(process, sig) -> None:
    if process.returncode is not None:
        return
    if getattr(process, "_deckclip_group", False):
        try:
            os.killpg(process.pid, sig)
            return
        except (ProcessLookupError, PermissionError, OSError):
            pass  # Fall back to the child alone.
    try:
        process.send_signal(sig)
    except ProcessLookupError:
        pass


def terminate_all() -> None:
    """Send SIGTERM to every live child group, without waiting."""
    for process in live_processes():
        _signal(process, signal.SIGTERM)


def kill_all() -> None:
    """Send SIGKILL to every live child group, without waiting."""
    for process in live_processes():
        _signal(process, signal.SIGKILL)


async def reap_all(timeout: float) -> None:
    """Wait, at most ``timeout`` seconds, for every live child to be reaped."""
    processes = live_processes()
    if processes and timeout > 0:
        await asyncio.wait([asyncio.ensure_future(process.wait()) for process in processes], timeout=timeout)
    live_processes()


async def stop_process(process, grace: float = TERM_GRACE):
    """Stop a child within about ``grace`` + KILL_WAIT seconds, never longer."""
    try:
        if process.returncode is None:
            _signal(process, signal.SIGTERM)
            try:
                await asyncio.wait_for(process.wait(), grace)
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                _signal(process, signal.SIGKILL)
                raise
            if process.returncode is None:
                _signal(process, signal.SIGKILL)
                try:
                    await asyncio.wait_for(process.wait(), KILL_WAIT)
                except asyncio.TimeoutError:
                    pass
    finally:
        if process.returncode is not None:
            _live.pop(id(process), None)


async def run_ffmpeg(command, on_progress=None, *, pass_fds=(), on_started=None):
    """Run FFmpeg (argument list, never a shell) and raise on failure.

    A leading ``"ffmpeg"`` is replaced by the resolved executable. ``pass_fds``
    are inherited by the child; ``on_started`` runs right after the spawn so a
    caller can close its copies of those descriptors and start feeding pipes.
    """
    command = list(command)
    if command and command[0] == "ffmpeg":
        command[0] = find_ffmpeg() or "ffmpeg"
    process = await spawn(
        *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env=system_tool_env(), pass_fds=tuple(pass_fds),
    )
    if on_started is not None:
        on_started()
    tail = bytearray()
    truncated = False

    async def errors():
        nonlocal truncated
        while chunk := await process.stderr.read(8192):
            tail.extend(chunk)
            if len(tail) > MAX_STDERR:
                del tail[:-MAX_STDERR]
                truncated = True

    async def progress():
        async for line in process.stdout:
            if on_progress:
                on_progress(line.decode(errors="replace").strip())

    readers = [asyncio.create_task(errors()), asyncio.create_task(progress())]
    try:
        await asyncio.gather(*readers)
        if await process.wait() != 0:
            detail = tail.decode(errors="replace").strip()
            if truncated:
                detail = "[Earlier FFmpeg output omitted; final 128 KiB retained]\n" + detail
            raise RuntimeError(detail or f"FFmpeg exited with code {process.returncode}")
    finally:
        await stop_process(process)
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
