"""Temporary, token-protected LAN transfers for exported clips."""

import asyncio
import datetime as dt
import html
import json
import os
import re
import secrets
import socket
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from .qr import _qr_matrix
from .exports import open_export, file_identity

TRANSFER_TTL_SECONDS = 10 * 60
TRANSFER_COMPLETION_GRACE_SECONDS = 30
MAX_TRANSFER_FILES = 20
MAX_CLIENTS = 8
WRITE_TIMEOUT = 15
# Stopping the server never waits longer than this for listeners/clients.
STOP_TIMEOUT = 1.0

def _lan_address() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 9))
        address = sock.getsockname()[0]
    except OSError as error:
        raise RuntimeError("Connect the Steam Deck to a local network before sharing") from error
    finally:
        sock.close()
    if address.startswith("127.") or address == "0.0.0.0":
        raise RuntimeError("Could not determine the Steam Deck's local network address")
    return address

class TransferManager:
    def __init__(self, export_path: Callable[[str], Path], logger: Any):
        self._export_path = export_path
        self._logger = logger
        self.transfer: dict[str, Any] | None = None
        self.transfer_expiry: asyncio.Task | None = None
        self.transfer_completion: asyncio.Task | None = None
        self.lock = asyncio.Lock()
        self.clients = {}
        self.closing = False

    async def start_transfer(self, filenames: list[str] | str) -> dict[str, Any]:
        async with self.lock:
            return await self._start_transfer(filenames)

    async def _start_transfer(self, filenames):
        # Accept a string for compatibility with older frontends during upgrades.
        if isinstance(filenames, str):
            filenames = [filenames]
        if not isinstance(filenames, list) or not filenames:
            raise ValueError("Select at least one exported clip")
        if len(filenames) > MAX_TRANSFER_FILES:
            raise ValueError(f"Select no more than {MAX_TRANSFER_FILES} clips at once")
        if any(not isinstance(name, str) for name in filenames) or len(set(filenames)) != len(filenames):
            raise ValueError("The selected exported clips are invalid")
        paths = [await asyncio.to_thread(self._export_path, name) for name in filenames]
        files = []
        for index, path in enumerate(paths):
            with open_export(path) as stream:
                files.append({"id": index, "filename": path.name, "complete": False,
                              "identity": file_identity(os.fstat(stream.fileno()))})
        await self._stop_locked()
        address = await asyncio.to_thread(_lan_address)
        token = secrets.token_urlsafe(32)
        expires_at = time.time() + TRANSFER_TTL_SECONDS
        server = await asyncio.start_server(
            self._handle_transfer_client,
            host="0.0.0.0",
            port=0,
            limit=16 * 1024,
        )
        sockets = server.sockets or []
        if not sockets:
            server.close()
            await server.wait_closed()
            raise RuntimeError("Could not start the local transfer server")
        if self.closing:
            # The plugin started unloading while this listener was opening.
            server.close()
            raise RuntimeError("ClipPort is shutting down")
        port = int(sockets[0].getsockname()[1])
        url = f"http://{address}:{port}/{token}/"
        self.transfer = {
            "server": server,
            "token": token,
            "files": files,
            "url": url,
            "expires_at": expires_at,
            "downloads": 0,
            "completed_files": 0,
            "bytes_sent": 0,
            "state": "ready",
        }
        self.transfer_expiry = asyncio.create_task(self._expire_transfer(token))
        return self._transfer_public_status(include_qr=True)

    async def get_transfer_status(self) -> dict[str, Any]:
        async with self.lock:
            return await self._get_transfer_status()

    async def _get_transfer_status(self):
        if self.transfer is None:
            return {"state": "inactive"}
        if time.time() >= self.transfer["expires_at"]:
            await self._stop_locked()
            return {"state": "expired"}
        return self._transfer_public_status(include_qr=False)

    async def stop_transfer(self) -> dict[str, str]:
        await self._stop_transfer()
        return {"state": "inactive"}

    def _transfer_public_status(self, include_qr: bool) -> dict[str, Any]:
        assert self.transfer is not None
        status = {
            "state": self.transfer["state"],
            "filename": self.transfer["files"][0]["filename"] if len(self.transfer["files"]) == 1 else None,
            "filenames": [entry["filename"] for entry in self.transfer["files"]],
            "file_count": len(self.transfer["files"]),
            "completed_files": self.transfer["completed_files"],
            "url": self.transfer["url"],
            "expires_at": dt.datetime.fromtimestamp(self.transfer["expires_at"]).astimezone().isoformat(),
            "downloads": self.transfer["downloads"],
            "bytes_sent": self.transfer["bytes_sent"],
        }
        if include_qr:
            status["qr"] = _qr_matrix(self.transfer["url"])
        return status

    async def _expire_transfer(self, token: str):
        try:
            await asyncio.sleep(TRANSFER_TTL_SECONDS)
            await self._stop_transfer(token)
        except asyncio.CancelledError:
            pass

    async def _close_after_completion(self, token: str):
        try:
            await asyncio.sleep(TRANSFER_COMPLETION_GRACE_SECONDS)
            await self._stop_transfer(token)
        except asyncio.CancelledError:
            pass

    async def _stop_transfer(self, token=None):
        async with self.lock:
            if token is not None and (self.transfer is None or not secrets.compare_digest(self.transfer["token"], token)):
                return
            await self._stop_locked()

    async def shutdown(self):
        """Stop sharing for plugin unload: no lock wait, bounded, aborts clients."""
        self.closing = True
        await self._stop_locked(abort=True)

    async def _stop_locked(self, abort: bool = False):
        transfer = self.transfer
        self.transfer = None
        expiry = self.transfer_expiry
        self.transfer_expiry = None
        completion = self.transfer_completion
        self.transfer_completion = None
        timers = [task for task in (expiry, completion) if task is not None and task is not asyncio.current_task()]
        for task in timers:
            task.cancel()
        server = transfer["server"] if transfer is not None else None
        if server is not None:
            server.close()  # Stop accepting first.
        # Close clients before waiting on the server: since Python 3.12,
        # Server.wait_closed() also waits for every open connection.
        clients = list(self.clients.items())
        for task, writer in clients:
            transport = getattr(writer, "transport", None)
            if abort and transport is not None:
                transport.abort()  # Drop unsent data instead of flushing to a stalled phone.
            else:
                writer.close()
            task.cancel()
        pending = [*timers, *(task for task, _ in clients)]
        if pending:
            await asyncio.wait(pending, timeout=STOP_TIMEOUT)
        if server is not None:
            try:
                await asyncio.wait_for(server.wait_closed(), STOP_TIMEOUT)
            except asyncio.TimeoutError:
                getattr(self._logger, "warning", lambda *args: None)("ClipPort transfer server did not close in time")

    async def _send_http(self, writer: asyncio.StreamWriter, status: str, headers: dict[str, str], body: bytes = b""):
        safe_headers = {
            "Connection": "close",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            **headers,
        }
        head = f"HTTP/1.1 {status}\r\n" + "".join(f"{key}: {value}\r\n" for key, value in safe_headers.items()) + "\r\n"
        writer.write(head.encode("ascii") + body)
        await asyncio.wait_for(writer.drain(), WRITE_TIMEOUT)

    async def _handle_transfer_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        if len(self.clients) >= MAX_CLIENTS or self.transfer is None:
            writer.close()
            return
        task = asyncio.current_task()
        self.clients[task] = writer
        transfer = self.transfer
        input_file = None
        try:
            try:
                request = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
            except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
                await self._send_http(writer, "400 Bad Request", {"Content-Length": "0"})
                return
            if len(request) > 8192:
                await self._send_http(writer, "431 Request Header Fields Too Large", {"Content-Length": "0"})
                return
            lines = request.decode("iso-8859-1").split("\r\n")
            parts = lines[0].split(" ")
            if len(parts) != 3 or parts[0] not in ("GET", "HEAD") or not parts[2].startswith("HTTP/1."):
                await self._send_http(writer, "405 Method Not Allowed", {"Allow": "GET, HEAD", "Content-Length": "0"})
                return
            method, target, _ = parts
            headers = {}
            for line in lines[1:]:
                if not line:
                    break
                if ":" not in line:
                    await self._send_http(writer, "400 Bad Request", {"Content-Length": "0"})
                    return
                key, value = line.split(":", 1)
                headers[key.strip().lower()] = value.strip()
            if self.transfer is not transfer or time.time() >= transfer["expires_at"]:
                await self._send_http(writer, "410 Gone", {"Content-Length": "0"})
                return
            base = f"/{transfer['token']}/"
            if target == base:
                manifest_url = transfer["url"] + "manifest"
                shortcut_url = (
                    "shortcuts://run-shortcut?name=ClipPort%20Save%20to%20Photos"
                    f"&input=text&text={quote(manifest_url, safe='')}"
                )
                safe_shortcut_url = html.escape(shortcut_url, quote=True)
                file_links = "".join(
                    f"<li><span>{html.escape(entry['filename'])}</span>"
                    f"<a href='file/{entry['id']}' download>Download</a></li>"
                    for entry in transfer["files"]
                )
                count = len(transfer["files"])
                body = (
                    "<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>"
                    "<title>ClipPort transfer</title><style>"
                    "body{background:#111;box-sizing:border-box;color:#fff;display:flex;flex-direction:column;font:17px system-ui;margin:0 auto;max-width:760px;min-height:100svh;padding:24px}"
                    "a{color:#70b7ff}a.button{background:#1473e6;border-radius:10px;color:#fff;display:block;font-weight:600;margin:12px 0;padding:14px;text-align:center;text-decoration:none}"
                    "li{align-items:center;display:flex;gap:12px;justify-content:space-between;padding:10px 0}li span{overflow-wrap:anywhere}"
                    "aside{background:#1d1d1d;border-radius:10px;color:#ddd;margin:20px 0;padding:14px}"
                    "footer{margin-top:auto;padding-top:18px}small{color:#bbb}</style><h1>ClipPort</h1>"
                    f"<p>{count} selected clip{'s' if count != 1 else ''}</p>"
                    f"<ul>{file_links}</ul>"
                    "<aside><strong>First time?</strong> Set up the iPhone Shortcut from ClipPort’s "
                    "<strong>Help &amp; Settings</strong> screen before starting a transfer.</aside>"
                    f"<footer><small>Use this temporary link on a trusted local network.</small>"
                    f"<a class='button' href='{safe_shortcut_url}'>Save {'all ' if count != 1 else ''}to Photos</a></footer>"
                ).encode("utf-8")
                await self._send_http(writer, "200 OK", {
                    "Content-Type": "text/html; charset=utf-8",
                    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'",
                    "X-Frame-Options": "DENY",
                    "Content-Length": str(len(body)),
                }, b"" if method == "HEAD" else body)
                return
            if target == base + "manifest":
                manifest = json.dumps({"files": [
                    {"name": entry["filename"], "url": transfer["url"] + f"file/{entry['id']}"}
                    for entry in transfer["files"]
                ]}, ensure_ascii=False).encode("utf-8")
                await self._send_http(writer, "200 OK", {
                    "Content-Type": "application/json; charset=utf-8",
                    "Content-Length": str(len(manifest)),
                }, b"" if method == "HEAD" else manifest)
                return
            file_match = re.fullmatch(re.escape(base) + r"file/([0-9]{1,3})", target)
            if file_match is None:
                await self._send_http(writer, "404 Not Found", {"Content-Length": "0"})
                return
            file_index = int(file_match.group(1))
            if file_index >= len(transfer["files"]):
                await self._send_http(writer, "404 Not Found", {"Content-Length": "0"})
                return
            file_entry = transfer["files"][file_index]
            try:
                # Revalidate containment and existence for every request. Never trust
                # a path sent by the browser or retained across filesystem changes.
                path = await asyncio.to_thread(self._export_path, file_entry["filename"])
                input_file = open_export(path)
                info = os.fstat(input_file.fileno())
                if file_identity(info) != file_entry["identity"]:
                    raise ValueError("Shared file changed; start a new transfer")
                size = info.st_size
            except (OSError, ValueError):
                await self._send_http(writer, "410 Gone", {"Content-Length": "0"})
                return
            start, end = 0, max(0, size - 1)
            response_status = "200 OK"
            range_header = headers.get("range")
            if range_header:
                match = re.fullmatch(r"bytes=([0-9]{0,20})-([0-9]{0,20})", range_header)
                if not match or (not match.group(1) and not match.group(2)):
                    await self._send_http(writer, "416 Range Not Satisfiable", {"Content-Range": f"bytes */{size}", "Content-Length": "0"})
                    return
                if match.group(1):
                    start = int(match.group(1))
                    end = min(int(match.group(2) or end), end)
                else:
                    suffix = min(int(match.group(2)), size)
                    start = size - suffix
                if start >= size or end < start:
                    await self._send_http(writer, "416 Range Not Satisfiable", {"Content-Range": f"bytes */{size}", "Content-Length": "0"})
                    return
                response_status = "206 Partial Content"
            length = end - start + 1 if size else 0
            download_name = quote(file_entry["filename"], safe="")
            response_headers = {
                "Content-Type": "video/mp4",
                "Content-Disposition": f"attachment; filename*=UTF-8''{download_name}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(length),
            }
            if response_status.startswith("206"):
                response_headers["Content-Range"] = f"bytes {start}-{end}/{size}"
            await self._send_http(writer, response_status, response_headers)
            if method == "HEAD" or not length:
                return
            sent = 0
            with input_file:
                input_file.seek(start)
                remaining = length
                while remaining and self.transfer is transfer and time.time() < transfer["expires_at"]:
                    # A bounded local read avoids a canceled worker retaining the
                    # descriptor after session shutdown closes it.
                    chunk = input_file.read(min(256 * 1024, remaining))
                    if not chunk:
                        break
                    writer.write(chunk)
                    await asyncio.wait_for(writer.drain(), WRITE_TIMEOUT)
                    sent += len(chunk)
                    remaining -= len(chunk)
                    transfer["bytes_sent"] += len(chunk)
            if sent == length:
                if start == 0 and end == size - 1 and not file_entry["complete"]:
                    file_entry["complete"] = True
                    transfer["downloads"] += 1
                    transfer["completed_files"] += 1
                all_complete = transfer["completed_files"] == len(transfer["files"])
                transfer["state"] = "downloaded" if all_complete else "downloading"
                if all_complete and self.transfer_completion is None:
                    self.transfer_completion = asyncio.create_task(
                        self._close_after_completion(transfer["token"])
                    )
        except (ConnectionError, BrokenPipeError, asyncio.TimeoutError):
            pass
        except Exception:
            self._logger.exception("ClipPort transfer request failed")
        finally:
            if input_file is not None:
                input_file.close()
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 0.5 if self.closing else 2)
            except (ConnectionError, asyncio.TimeoutError):
                pass
            finally:
                self.clients.pop(task, None)
