"""Loopback-only bridge from the owner CLI to a connected RLCD's ROM entry tool."""

import hmac
import os

from aiohttp import web

from core.providers.tools.device_mcp.mcp_handler import call_mcp_tool
from core.recovery_registry import connections


async def enter_download_mode(request: web.Request) -> web.Response:
    if request.remote not in ("127.0.0.1", "::1"):
        raise web.HTTPForbidden()
    token = os.environ.get("XIAOZHI_RECOVERY_TOKEN", "")
    supplied = request.headers.get("X-Recovery-Token", "")
    if len(token) != 64 or not hmac.compare_digest(token, supplied):
        raise web.HTTPForbidden()
    data = await request.json()
    device_id = data.get("device_id", "")
    if not isinstance(device_id, str) or len(device_id) > 64:
        raise web.HTTPBadRequest(text="invalid device_id")
    device_id = device_id.lower()
    allowed = {
        item.strip().lower()
        for item in os.environ.get("XIAOZHI_RECOVERY_DEVICE_IDS", "").split(",")
        if item.strip()
    }
    if device_id not in allowed:
        raise web.HTTPForbidden(text="device not allowed")
    conn = connections.get(device_id)
    if conn is None or not getattr(conn, "mcp_client", None):
        raise web.HTTPServiceUnavailable(text="device not connected")
    try:
        result = await call_mcp_tool(
            conn, conn.mcp_client, "self.system.enter_download_mode", {"token": token},
            timeout=10, allow_unlisted=True,
        )
    except (RuntimeError, ValueError, TimeoutError) as error:
        raise web.HTTPServiceUnavailable(text="device did not confirm recovery request") from error
    if result not in ("true", "True", "1"):
        raise web.HTTPConflict(text="device rejected ROM download mode")
    return web.json_response({"accepted": True, "device_id": device_id})
