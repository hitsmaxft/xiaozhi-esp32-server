"""Bind XiaoZhi tool calls to the RLCD Device Use call graph."""

import asyncio
import contextvars
import hashlib
import os
from pathlib import Path

import aiohttp


turn_id_context = contextvars.ContextVar("device_use_turn_id", default=None)
tool_id_context = contextvars.ContextVar("device_use_tool_id", default=None)
HOST = "http://127.0.0.1:8767"


def control_url(path):
    return f"{HOST}{path}"


def control_headers():
    token_path = os.environ.get("RLCD_DEVICE_USE_TOKEN_FILE", "")
    if not token_path or not Path(token_path).is_file():
        raise RuntimeError("Device Use Host 令牌未配置")
    token = Path(token_path).read_text(encoding="ascii").strip()
    if len(token) != 64:
        raise RuntimeError("Device Use Host 令牌无效")
    return {"Authorization": f"Bearer {token}"}


async def _post(session, path, payload, headers):
    async with session.post(control_url(path), json=payload, headers=headers) as response:
        result = await response.json()
        if response.status != 200:
            raise RuntimeError(result.get("error", f"Device Use HTTP {response.status}"))
        return result


async def call_metadata(conn, session, headers):
    """Create a turn root lazily; repeated tool calls reuse it safely."""
    turn_id = turn_id_context.get()
    if not turn_id:
        return {}
    root = await _post(session, "/calls/begin", {"callId": turn_id}, headers)
    if root.get("callId") != turn_id or root.get("state") != "accepted":
        raise RuntimeError("Device Use 对话调用未被设备接受")
    conn.device_use_started_turns.add(turn_id)
    tool_id = tool_id_context.get()
    if not tool_id:
        raise RuntimeError("Device Use 工具调用缺少稳定 ID")
    call_id = hashlib.sha256(f"{turn_id}:{tool_id}".encode()).hexdigest()[:32]
    return {"parentId": turn_id, "callId": call_id}


async def finish_turn(conn, turn_id, canceled):
    """End a turn without blocking the audio worker; retain it for retry on failure."""
    if turn_id not in conn.device_use_started_turns:
        return
    path = "/calls/cancel" if canceled else "/calls/complete"
    timeout = aiohttp.ClientTimeout(total=3)
    for attempt in range(3):
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                result = await _post(session, path,
                    {"callId": turn_id, "generation": 1}, control_headers())
            if result.get("callId") != turn_id or result.get("state") not in (
                    "canceled", "completed"):
                raise RuntimeError("Device Use 对话调用结束状态无效")
            conn.device_use_started_turns.discard(turn_id)
            return
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError,
                RuntimeError) as error:
            if attempt == 2:
                conn.logger.bind(tag=__name__).warning(
                    f"Device Use 对话调用 {turn_id} 结束失败: {error}")
            else:
                await asyncio.sleep(0.2 * (attempt + 1))
