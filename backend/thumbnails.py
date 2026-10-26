"""Bounded cached previews; source input may use an auto-removed temporary file."""
import asyncio
import base64
import os
import sys
from collections import OrderedDict
from backend.process_env import find_ffmpeg, system_tool_env
from backend.exports import open_export, file_identity
from backend.processes import spawn, stop_process
from backend.source_preview import open_source_preview

MAX_IN_FLIGHT = 8
# Returned by Thumbnails.get when MAX_IN_FLIGHT requests are already waiting.
# The panel retries these with backoff instead of keeping a blank preview.
THUMBNAIL_BUSY = object()


class Thumbnails:
    def __init__(self, resolve):
        self.resolve = resolve
        self.cache = OrderedDict()
        self.lock = asyncio.Lock()
        self.tasks = set()
        self.closing = False

    async def get(self, filename, source_path=None):
        if self.closing:
            return None
        if len(self.tasks) >= MAX_IN_FLIGHT:
            return THUMBNAIL_BUSY
        task = asyncio.current_task()
        self.tasks.add(task)
        try:
            return await self._get(filename, source_path)
        finally:
            self.tasks.discard(task)

    async def close(self):
        self.closing = True
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _get(self, filename, source_path=None):
        async with self.lock:
            if source_path is not None:
                key = ("source", filename, *file_identity(source_path.stat()))
                if key in self.cache:
                    self.cache.move_to_end(key)
                    return self.cache[key]
                # Keep ownership until the worker exits, including cancellation.
                worker = asyncio.create_task(asyncio.to_thread(open_source_preview, source_path))
                try:
                    stream = await asyncio.shield(worker)
                except asyncio.CancelledError:
                    try:
                        (await worker).close()
                    except (OSError, ValueError):
                        pass
                    raise
                except (OSError, ValueError):
                    return None
            else:
                path = self.resolve(filename)
                stream = open_export(path)
                key = ("export", filename, *file_identity(os.fstat(stream.fileno())))
            if key in self.cache:
                stream.close()
                self.cache.move_to_end(key)
                return self.cache[key]
            process = None
            try:
                descriptor_path = f"/proc/self/fd/{stream.fileno()}" if sys.platform.startswith("linux") else f"/dev/fd/{stream.fileno()}"
                process = await spawn(
                    find_ffmpeg() or "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                    "-protocol_whitelist", "file,pipe", "-i", descriptor_path,
                    "-frames:v", "1", "-vf", "scale=160:90:force_original_aspect_ratio=decrease",
                    "-an", "-threads", "1", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                    env=system_tool_env(),
                    pass_fds=(stream.fileno(),),
                )
                async def read_preview():
                    data = bytearray()
                    while chunk := await process.stdout.read(8192):
                        data.extend(chunk)
                        if len(data) > 65536:
                            raise ValueError("Preview exceeds size limit")
                    await process.wait()
                    return bytes(data)
                data = await asyncio.wait_for(read_preview(), timeout=10)
                result = "data:image/jpeg;base64," + base64.b64encode(data).decode() if process.returncode == 0 and 0 < len(data) <= 65536 else None
            except (OSError, asyncio.TimeoutError, ValueError):
                result = None
            finally:
                if process is not None:
                    await stop_process(process)
                stream.close()
            self.cache[key] = result
            while len(self.cache) > 100:
                self.cache.popitem(last=False)
            return result
