"""Explicit, device-scoped recovery request for an RLCD on the local server."""

import os

from core.providers.tools.device_mcp.mcp_handler import call_mcp_tool
from plugins_func.register import Action, ActionResponse, ToolType, register_function


recovery_desc = {
    "type": "function",
    "function": {
        "name": "enter_rlcd_download_mode",
        "description": "仅当用户明确要求给当前 RLCD 刷机或进入下载模式时调用。设备会断开语音连接，USB ROM 下载口随后枚举。",
        "parameters": {"type": "object", "properties": {}},
    },
}


@register_function("enter_rlcd_download_mode", recovery_desc, ToolType.SYSTEM_CTL)
async def enter_rlcd_download_mode(conn):
    allowed = {
        item.strip().lower()
        for item in os.environ.get("XIAOZHI_RECOVERY_DEVICE_IDS", "").split(",")
        if item.strip()
    }
    device_id = conn.headers.get("device-id", "").lower()
    if not allowed or device_id not in allowed:
        return ActionResponse(Action.RESPONSE, response="当前设备未列入 RLCD 下载模式白名单")
    token = os.environ.get("XIAOZHI_RECOVERY_TOKEN", "")
    if len(token) != 64:
        return ActionResponse(Action.RESPONSE, response="本机没有配置 RLCD 恢复令牌")
    if not getattr(conn, "mcp_client", None):
        return ActionResponse(Action.RESPONSE, response="设备没有 MCP 连接")
    try:
        result = await call_mcp_tool(
            conn, conn.mcp_client, "self.system.enter_download_mode", {"token": token},
            timeout=10, allow_unlisted=True,
        )
    except (RuntimeError, ValueError, TimeoutError):
        return ActionResponse(Action.RESPONSE, response="设备未确认下载模式请求")
    if result not in ("true", "True", "1"):
        return ActionResponse(Action.RESPONSE, response="设备拒绝进入下载模式；请检查 eFuse 与固件版本")
    return ActionResponse(Action.RESPONSE, response="设备已接受 ROM 下载模式请求，请连接 USB JTAG 端口刷写")
