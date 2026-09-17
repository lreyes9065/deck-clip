"""Small, bounded in-memory previews; no source or cache files are written."""
import asyncio
import base64
import os
import sys
from collections import OrderedDict
from backend.process_env import system_tool_env
from backend.exports import open_export, file_identity
from backend.processes import stop_process


class Thumbnails:
    def __init__(self, resolve):
        self.resolve = resolve
        self.cache = OrderedDict()
        self.lock = asyncio.Lock()
        self.tasks = set()
        self.closing = False

    async def get(self, filename):
        if self.closing or len(self.tasks) >= 8:
            return None
        task = asyncio.current_task()
        self.tasks.add(task)
        try:
            return await self._get(filename)
        finally:
            self.tasks.discard(task)

    async def close(self):
        self.closing = True
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _get(self, filename):
        async with self.lock:
            path = self.resolve(filename)
            stream = open_export(path)
            key = (filename, *file_identity(os.fstat(stream.fileno())))
            if key in self.cache:
                stream.close()
                self.cache.move_to_end(key)
                return self.cache[key]
            process = None
            try:
                descriptor_path = f"/proc/self/fd/{stream.fileno()}" if sys.platform.startswith("linux") else f"/dev/fd/{stream.fileno()}"
                process = await asyncio.create_subprocess_exec(
                    "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
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
