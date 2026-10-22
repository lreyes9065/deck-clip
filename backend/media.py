"""Fragmented MP4 assembly helpers that never modify Steam source files."""

import re
import asyncio
import os
import threading
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from typing import Callable

INIT_STREAM_RE = re.compile(r"^init-stream(?P<id>\d+)\.m4s$")
CHUNK_RE = re.compile(r"^chunk-stream(?P<id>\d+)-(?P<number>\d+)\.m4s$")
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
# Audio segments end on codec frame boundaries, so a complete recording can
# have one more or one fewer audio segment than video (FFmpeg's DASH muxer does
# this). A larger difference means a stream stopped early.
MAX_SEGMENT_COUNT_DIFFERENCE = 1


class IncompleteRecording(ValueError):
    """A session's fragments are missing, mismatched, or disagree with its manifest."""


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


def feed_fragments(files: list[Path], fd: int, on_bytes: Callable[[int], None], stop=None) -> None:
    """Write fragments, in order, into a pipe that FFmpeg reads. Takes ownership of ``fd``.

    Nothing is staged on disk. A closed reader (FFmpeg exited or was stopped)
    ends feeding quietly; FFmpeg's own exit status decides success.
    """
    try:
        with os.fdopen(fd, "wb") as pipe:
            for source in files:
                with source.open("rb") as input_file:
                    while block := input_file.read(1024 * 1024):
                        if stop is not None and stop.is_set():
                            return
                        pipe.write(block)
                        on_bytes(len(block))
    except BrokenPipeError:
        return


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def manifest_segments(manifest: Path) -> dict[int, dict]:
    """Per-representation ``{"start": int, "count": int | None}`` from a DASH MPD.

    ``count`` is only known when the manifest has a SegmentTimeline. Unreadable
    or non-XML manifests return ``{}`` (nothing to compare against).
    """
    try:
        if manifest.stat().st_size > MAX_MANIFEST_BYTES:
            return {}
        root = ElementTree.fromstring(manifest.read_bytes())
    except (OSError, ElementTree.ParseError):
        return {}
    result: dict[int, dict] = {}
    for adaptation in root.iter():
        if _local(adaptation.tag) != "AdaptationSet":
            continue
        shared = next((child for child in adaptation if _local(child.tag) == "SegmentTemplate"), None)
        for representation in adaptation:
            if _local(representation.tag) != "Representation":
                continue
            rep_id = representation.get("id", "")
            if not rep_id.isdigit():
                continue
            template = next((child for child in representation if _local(child.tag) == "SegmentTemplate"), shared)
            if template is None:
                continue
            try:
                start = int(template.get("startNumber", "1"))
            except ValueError:
                continue
            count = None
            timeline = next((child for child in template if _local(child.tag) == "SegmentTimeline"), None)
            if timeline is not None:
                count = 0
                for segment in timeline:
                    if _local(segment.tag) != "S":
                        continue
                    try:
                        repeat = int(segment.get("r", "0"))
                    except ValueError:
                        repeat = -1
                    if repeat < 0:
                        count = None  # open-ended repeat: count unknown
                        break
                    count += 1 + repeat
            result[int(rep_id)] = {"start": start, "count": count}
    return result


def validate_recording(session_dir: Path, manifest: Path | None = None) -> list[tuple[int, list[Path]]]:
    """Return ``stream_files(session_dir)`` only if the session looks complete.

    Requires a video stream (id 0), contiguous chunk numbers per stream, the
    same number of chunks in every stream (within MAX_SEGMENT_COUNT_DIFFERENCE),
    and agreement with the manifest's
    start number and segment count wherever the manifest states them.
    """
    streams = stream_files(session_dir)
    if not streams or streams[0][0] != 0:
        raise IncompleteRecording("no video fragments")
    expected = manifest_segments(manifest) if manifest is not None else {}
    counts: dict[int, int] = {}
    for stream_id, files in streams:
        numbers = []
        for path in files[1:]:
            match = CHUNK_RE.match(path.name)
            if match is None:
                raise IncompleteRecording(f"unexpected fragment {path.name}")
            numbers.append(int(match.group("number")))
        first = numbers[0]
        if numbers != list(range(first, first + len(numbers))):
            missing = sorted(set(range(first, numbers[-1] + 1)) - set(numbers))
            shown = ", ".join(str(number) for number in missing[:5]) + ("…" if len(missing) > 5 else "")
            raise IncompleteRecording(f"stream {stream_id} is missing segment(s) {shown}")
        info = expected.get(stream_id)
        if info is not None:
            if first != info["start"]:
                raise IncompleteRecording(f"stream {stream_id} starts at segment {first}, manifest says {info['start']}")
            if info["count"] is not None and len(numbers) != info["count"]:
                raise IncompleteRecording(f"stream {stream_id} has {len(numbers)} segments, manifest lists {info['count']}")
        counts[stream_id] = len(numbers)
    for stream_id in expected:
        if stream_id not in counts:
            raise IncompleteRecording(f"manifest lists stream {stream_id} but it has no fragments")
    video = counts[0]
    for stream_id, count in counts.items():
        if abs(count - video) > MAX_SEGMENT_COUNT_DIFFERENCE:
            raise IncompleteRecording(f"stream {stream_id} has {count} segments but video has {video}")
    return streams
