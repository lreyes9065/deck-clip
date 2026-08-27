"""Temporary, token-protected LAN transfers for exported clips."""

import asyncio
import datetime as dt
import html
import re
import secrets
import socket
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from .qr import _qr_matrix

TRANSFER_TTL_SECONDS = 10 * 60
TRANSFER_COMPLETION_GRACE_SECONDS = 30

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

    async def start_transfer(self, filename: str) -> dict[str, Any]:
        path = await asyncio.to_thread(self._export_path, filename)
        await self._stop_transfer()
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
        port = int(sockets[0].getsockname()[1])
        url = f"http://{address}:{port}/{token}/"
        self.transfer = {
            "server": server,
            "token": token,
            "path": path,
            "filename": path.name,
            "url": url,
            "expires_at": expires_at,
            "downloads": 0,
            "bytes_sent": 0,
            "state": "ready",
        }
        self.transfer_expiry = asyncio.create_task(self._expire_transfer(token))
        return self._transfer_public_status(include_qr=True)

    async def get_transfer_status(self) -> dict[str, Any]:
        if self.transfer is None:
            return {"state": "inactive"}
        if time.time() >= self.transfer["expires_at"]:
            await self._stop_transfer()
            return {"state": "expired"}
        return self._transfer_public_status(include_qr=False)

    async def stop_transfer(self) -> dict[str, str]:
        await self._stop_transfer()
        return {"state": "inactive"}

    def _transfer_public_status(self, include_qr: bool) -> dict[str, Any]:
        assert self.transfer is not None
        status = {
            "state": self.transfer["state"],
            "filename": self.transfer["filename"],
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
            if self.transfer is not None and secrets.compare_digest(self.transfer["token"], token):
                await self._stop_transfer()
        except asyncio.CancelledError:
            pass

    async def _close_after_completion(self, token: str):
        try:
            await asyncio.sleep(TRANSFER_COMPLETION_GRACE_SECONDS)
            if self.transfer is not None and secrets.compare_digest(self.transfer["token"], token):
                await self._stop_transfer()
        except asyncio.CancelledError:
            pass

    async def _stop_transfer(self):
        transfer = self.transfer
        self.transfer = None
        expiry = self.transfer_expiry
        self.transfer_expiry = None
        completion = self.transfer_completion
        self.transfer_completion = None
        if expiry is not None and expiry is not asyncio.current_task():
            expiry.cancel()
            await asyncio.gather(expiry, return_exceptions=True)
        if completion is not None and completion is not asyncio.current_task():
            completion.cancel()
            await asyncio.gather(completion, return_exceptions=True)
        if transfer is not None:
            server = transfer["server"]
            server.close()
            await server.wait_closed()

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
        await writer.drain()

    async def _handle_transfer_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
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
            transfer = self.transfer
            if transfer is None or time.time() >= transfer["expires_at"]:
                await self._send_http(writer, "410 Gone", {"Content-Length": "0"})
                return
            base = f"/{transfer['token']}/"
            if target == base:
                filename = html.escape(transfer["filename"])
                body = (
                    "<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>"
                    "<title>DeckClip transfer</title><style>"
                    "body{background:#111;color:#fff;font:17px system-ui;margin:0 auto;max-width:760px;padding:24px}"
                    "a{color:#7cc4ff}small{color:#bbb}</style><h1>DeckClip</h1>"
                    f"<p>{filename}</p><p><a href='download' download>Download clip</a></p>"
                    "<small>This temporary link works only on the same local network.</small>"
                ).encode("utf-8")
                await self._send_http(writer, "200 OK", {
                    "Content-Type": "text/html; charset=utf-8",
                    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'",
                    "X-Frame-Options": "DENY",
                    "Content-Length": str(len(body)),
                }, b"" if method == "HEAD" else body)
                return
            if target not in (base + "video", base + "download"):
                await self._send_http(writer, "404 Not Found", {"Content-Length": "0"})
                return
            try:
                path = await asyncio.to_thread(self._export_path, transfer["filename"])
                size = path.stat().st_size
            except (OSError, ValueError):
                await self._send_http(writer, "410 Gone", {"Content-Length": "0"})
                return
            start, end = 0, max(0, size - 1)
            response_status = "200 OK"
            range_header = headers.get("range")
            if range_header:
                match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
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
            download_name = quote(transfer["filename"], safe="")
            response_headers = {
                "Content-Type": "video/mp4",
                "Content-Disposition": f"{'attachment' if target.endswith('/download') else 'inline'}; filename*=UTF-8''{download_name}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(length),
            }
            if response_status.startswith("206"):
                response_headers["Content-Range"] = f"bytes {start}-{end}/{size}"
            await self._send_http(writer, response_status, response_headers)
            if method == "HEAD" or not length:
                return
            sent = 0
            with path.open("rb") as input_file:
                input_file.seek(start)
                remaining = length
                while remaining and self.transfer is transfer and time.time() < transfer["expires_at"]:
                    chunk = await asyncio.to_thread(input_file.read, min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    writer.write(chunk)
                    await writer.drain()
                    sent += len(chunk)
                    remaining -= len(chunk)
                    transfer["bytes_sent"] += len(chunk)
            if sent == length:
                transfer["downloads"] += 1
                transfer["state"] = "downloaded"
                if start == 0 and end == size - 1 and self.transfer_completion is None:
                    self.transfer_completion = asyncio.create_task(
                        self._close_after_completion(transfer["token"])
                    )
        except (ConnectionError, BrokenPipeError):
            pass
        except Exception:
            self._logger.exception("DeckClip transfer request failed")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
