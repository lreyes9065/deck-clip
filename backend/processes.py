"""Owned subprocess lifetimes and bounded, concurrently drained diagnostics."""
import asyncio

from .process_env import find_ffmpeg, system_tool_env

MAX_STDERR = 128 * 1024


async def stop_process(process):
    if process.returncode is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), 2)
        except asyncio.TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()


async def run_ffmpeg(command, on_progress=None, *, pass_fds=(), on_started=None):
    """Run FFmpeg (argument list, never a shell) and raise on failure.

    A leading ``"ffmpeg"`` is replaced by the resolved executable. ``pass_fds``
    are inherited by the child; ``on_started`` runs right after the spawn so a
    caller can close its copies of those descriptors and start feeding pipes.
    """
    command = list(command)
    if command and command[0] == "ffmpeg":
        command[0] = find_ffmpeg() or "ffmpeg"
    process = await asyncio.create_subprocess_exec(
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
