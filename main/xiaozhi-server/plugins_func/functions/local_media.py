"""Resolve local/NAS media names on the server, then call the thin client."""

import os
from pathlib import Path
from urllib.parse import quote, urlsplit

import aiohttp

from core.device_use_turn import call_metadata, control_headers, control_url
from core.providers.tools.device_mcp.mcp_handler import call_mcp_tool
from plugins_func.register import Action, ActionResponse, ToolType, register_function


def _media_root(conn) -> Path:
    return Path(conn.config.get("media", {}).get("root", "data/media")).resolve()


list_media_desc = {
    "type": "function",
    "function": {
        "name": "list_local_media",
        "description": "列出本地或挂载 NAS 目录中可播放的 MP3 和可显示的图片文件名。",
        "parameters": {"type": "object", "properties": {}},
    },
}


@register_function("list_local_media", list_media_desc, ToolType.SYSTEM_CTL)
async def list_local_media(conn):
    root = _media_root(conn)
    if not root.is_dir():
        return ActionResponse(Action.REQLLM, result="媒体目录为空")
    names = sorted(
        path.name for path in root.iterdir()
        if path.is_file() and path.suffix.lower() == ".mp3"
        and path.stat().st_size <= 64 * 1024 * 1024
    )[:100]
    images = sorted(
        path.name for path in root.iterdir()
        if path.is_file() and path.suffix.lower() in (".jpg", ".jpeg", ".png")
        and path.stat().st_size <= 8 * 1024 * 1024
    )[:100]
    return ActionResponse(Action.REQLLM,
                          result=f"MP3: {', '.join(names) or '无'}; 图片: {', '.join(images) or '无'}")


play_media_desc = {
    "type": "function",
    "function": {
        "name": "play_local_media",
        "description": "在当前 ESP32 设备播放本地或 NAS 的 MP3。需要精确文件名，可先调用 list_local_media。",
        "parameters": {
            "type": "object",
            "properties": {"filename": {"type": "string", "description": "MP3 文件名"}},
            "required": ["filename"],
        },
    },
}


@register_function("play_local_media", play_media_desc, ToolType.SYSTEM_CTL)
async def play_local_media(conn, filename: str):
    if not isinstance(filename, str) or not filename or Path(filename).name != filename:
        return ActionResponse(Action.RESPONSE, response="无效的媒体文件名")
    root = _media_root(conn)
    path = (root / filename).resolve()
    if (path.parent != root or not path.is_file() or path.suffix.lower() != ".mp3"
            or path.stat().st_size > 64 * 1024 * 1024):
        return ActionResponse(Action.RESPONSE, response="找不到该 MP3 文件")
    vision_url = conn.config["server"]["vision_explain"]
    parsed = urlsplit(vision_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ActionResponse(Action.RESPONSE, response="媒体服务地址未配置")
    url = f"{parsed.scheme}://{parsed.netloc}/media/opus/{quote(filename)}"
    allowed = {
        item.strip().lower()
        for item in os.environ.get("XIAOZHI_DEVICE_USE_DEVICE_IDS", "").split(",")
        if item.strip()
    }
    if (conn.headers or {}).get("device-id", "").lower() in allowed:
        try:
            timeout = aiohttp.ClientTimeout(total=8)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                headers = control_headers()
                metadata = await call_metadata(conn, session, headers)
                async with session.post(
                    control_url("/media/play"),
                    json={"url": url, **metadata}, headers=headers,
                ) as response:
                    result = await response.json()
                    if response.status != 200 or not result.get("accepted"):
                        raise RuntimeError(result.get("error", "设备未确认媒体请求"))
        except (aiohttp.ClientError, TimeoutError, OSError, ValueError,
                RuntimeError) as error:
            return ActionResponse(Action.RESPONSE, response=f"设备播放请求失败：{error}")
        return ActionResponse(Action.RECORD, result=f"已排队播放 {filename}",
                              response=f"即将播放 {filename}")
    if not getattr(conn, "mcp_client", None):
        return ActionResponse(Action.RESPONSE, response="设备没有 MCP 连接")
    try:
        await call_mcp_tool(conn, conn.mcp_client, "self_audio_play_stream", {"url": url})
    except (RuntimeError, ValueError, TimeoutError) as error:
        return ActionResponse(Action.RESPONSE, response=f"设备播放请求失败：{error}")
    return ActionResponse(Action.RECORD, result=f"已排队播放 {filename}", response=f"即将播放 {filename}")


show_image_desc = {
    "type": "function",
    "function": {
        "name": "show_local_image",
        "description": "在当前 ESP32 反射式屏幕显示本地或 NAS 图片。需要精确文件名。",
        "parameters": {
            "type": "object",
            "properties": {"filename": {"type": "string", "description": "JPG 或 PNG 文件名"}},
            "required": ["filename"],
        },
    },
}


@register_function("show_local_image", show_image_desc, ToolType.SYSTEM_CTL)
async def show_local_image(conn, filename: str):
    if not isinstance(filename, str) or not filename or Path(filename).name != filename:
        return ActionResponse(Action.RESPONSE, response="无效的图片文件名")
    root = _media_root(conn)
    path = (root / filename).resolve()
    if (path.parent != root or not path.is_file() or path.suffix.lower() not in
            (".jpg", ".jpeg", ".png") or path.stat().st_size > 8 * 1024 * 1024):
        return ActionResponse(Action.RESPONSE, response="找不到该图片")
    parsed = urlsplit(conn.config["server"]["vision_explain"])
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ActionResponse(Action.RESPONSE, response="媒体服务地址未配置")
    url = f"{parsed.scheme}://{parsed.netloc}/media/display/{quote(filename)}"
    if not getattr(conn, "mcp_client", None):
        return ActionResponse(Action.RESPONSE, response="设备没有 MCP 连接")
    try:
        await call_mcp_tool(conn, conn.mcp_client, "self_screen_preview_image", {"url": url})
    except (RuntimeError, ValueError, TimeoutError) as error:
        return ActionResponse(Action.RESPONSE, response=f"图片展示失败：{error}")
    return ActionResponse(Action.RECORD, result=f"已显示 {filename}", response=f"已显示 {filename}")
