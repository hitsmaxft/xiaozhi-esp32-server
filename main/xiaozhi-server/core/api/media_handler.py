"""Read-only LAN media endpoint for device images and audio files."""

import asyncio
import io
from pathlib import Path

from aiohttp import web
from PIL import Image, ImageOps


class MediaHandler:
    def __init__(self, config: dict):
        root = config.get("media", {}).get("root", "data/media")
        self.root = Path(root).resolve()
        self.transcode_slots = asyncio.Semaphore(2)

    def _find(self, name: str) -> Path:
        if name in (".", "..") or "/" in name or "\\" in name or not name:
            raise web.HTTPNotFound()
        path = (self.root / name).resolve()
        if path.parent != self.root or not path.is_file():
            raise web.HTTPNotFound()
        return path

    async def get(self, request: web.Request) -> web.StreamResponse:
        path = self._find(request.match_info["filename"])
        suffix = path.suffix.lower()
        limits = {".jpg": 512 * 1024, ".jpeg": 512 * 1024,
                  ".png": 512 * 1024, ".ogg": 64 * 1024 * 1024,
                  ".mp3": 64 * 1024 * 1024}
        if suffix not in limits or path.stat().st_size > limits[suffix]:
            raise web.HTTPNotFound()
        response = web.FileResponse(path)
        response.headers["Cache-Control"] = "private, max-age=60"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    async def opus(self, request: web.Request) -> web.StreamResponse:
        """Convert a bounded local/NAS MP3 to Ogg Opus without buffering it in RAM."""
        path = self._find(request.match_info["filename"])
        if path.suffix.lower() != ".mp3" or path.stat().st_size > 64 * 1024 * 1024:
            raise web.HTTPNotFound()
        try:
            start_ms = int(request.query.get("start_ms", "0"))
        except ValueError:
            raise web.HTTPBadRequest(text="Invalid start_ms")
        if not 0 <= start_ms <= 3_600_000:
            raise web.HTTPBadRequest(text="start_ms out of range")
        async with self.transcode_slots:
            process = await asyncio.create_subprocess_exec(
                "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                "-ss", f"{start_ms / 1000:.3f}", "-i", str(path),
                "-vn", "-ac", "1", "-ar", "24000",
                "-c:a", "libopus", "-b:a", "32k", "-f", "ogg", "pipe:1",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            response = web.StreamResponse(headers={
                "Content-Type": "audio/ogg",
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            })
            await response.prepare(request)
            try:
                while chunk := await process.stdout.read(4096):
                    await response.write(chunk)
                await response.write_eof()
                await process.wait()
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
            return response

    async def display_image(self, request: web.Request) -> web.Response:
        """Prepare a small monochrome JPEG for the RLCD LVGL decoder."""
        path = self._find(request.match_info["filename"])
        if path.suffix.lower() not in (".jpg", ".jpeg", ".png") or path.stat().st_size > 8 * 1024 * 1024:
            raise web.HTTPNotFound()
        try:
            with Image.open(path) as source:
                if source.width * source.height > 4_000_000:
                    raise web.HTTPRequestEntityTooLarge(max_size=4_000_000, actual_size=source.width * source.height)
                image = ImageOps.contain(source.convert("L"), (400, 300))
                canvas = Image.new("L", (400, 300), 255)
                canvas.paste(image, ((400 - image.width) // 2, (300 - image.height) // 2))
                output = io.BytesIO()
                canvas.save(output, format="JPEG", quality=75)
        except (OSError, ValueError):
            raise web.HTTPUnsupportedMediaType()
        data = output.getvalue()
        if len(data) > 512 * 1024:
            raise web.HTTPRequestEntityTooLarge(max_size=512 * 1024, actual_size=len(data))
        return web.Response(body=data, content_type="image/jpeg", headers={
            "Cache-Control": "private, max-age=60",
            "X-Content-Type-Options": "nosniff",
        })
