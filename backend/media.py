"""Fragmented MP4 assembly helpers that never modify Steam source files."""

import re
import asyncio
import threading
from pathlib import Path
from typing import Callable

INIT_STREAM_RE = re.compile(r"^init-stream(?P<id>\d+)\.m4s$")


def stream_files(session_dir: Path) -> list[tuple[int, list[Path]]]:
    """Return each fragmented MP4 stream with init first and chunks numerically ordered."""
    streams: list[tuple[int, list[Path]]] = []
    for init in session_dir.glob("init-stream*.m4s"):
        match = INIT_STREAM_RE.match(init.name)
        if not match or not init.is_file():
            continue
        stream_id = int(match.group("id"))
        chunk_re = re.compile(rf"^chunk-stream{stream_id}-(\d+)\.m4s$")
        numbered_chunks = []
        for chunk in session_dir.glob(f"chunk-stream{stream_id}-*.m4s"):
            chunk_match = chunk_re.match(chunk.name)
            if chunk_match and chunk.is_file():
                numbered_chunks.append((int(chunk_match.group(1)), chunk))
        numbered_chunks.sort(key=lambda entry: entry[0])
        if numbered_chunks:
            streams.append((stream_id, [init, *(path for _, path in numbered_chunks)]))
    streams.sort(key=lambda entry: entry[0])
    return streams


def join_fragments(files: list[Path], destination: Path, on_bytes: Callable[[int], None], stop=None) -> None:
    """Join fMP4 fragments into a temporary stream without changing source files."""
    with destination.open("xb") as output:
        for source in files:
            with source.open("rb") as input_file:
                while block := input_file.read(1024 * 1024):
                    if stop is not None and stop.is_set():
                        return
                    output.write(block)
                    on_bytes(len(block))


async def assemble_fragments(files, destination, on_bytes):
    """Join off-loop, but await worker exit before staging can be removed."""
    stop = threading.Event()
    loop = asyncio.get_running_loop()
    def report(count):
        if not stop.is_set():
            loop.call_soon_threadsafe(on_bytes, count)
    worker = asyncio.create_task(asyncio.to_thread(join_fragments, files, destination, report, stop))
    try:
        await asyncio.shield(worker)
    finally:
        stop.set()
        await worker
