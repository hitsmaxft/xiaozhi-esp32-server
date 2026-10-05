"""Send a spoken expression request through the local RLCD Device Use Host."""

import os
from pathlib import Path

import aiohttp
from core.device_use_turn import call_metadata, control_url

from plugins_func.register import Action, ActionResponse, ToolType, register_function


expression_desc = {
    "type": "function",
    "function": {
        "name": "set_rlcd_expression",
        "description": "用户要求这台 RLCD 笑一个、难过、惊讶或恢复平静时调用，在设备屏幕显示对应表情。",
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "enum": ["happy", "sad", "surprised", "neutral"],
                    "description": "happy=笑/开心，sad=难过，surprised=惊讶，neutral=平静",
                }
            },
            "required": ["expression"],
        },
    },
}


@register_function("set_rlcd_expression", expression_desc, ToolType.SYSTEM_CTL)
async def set_rlcd_expression(conn, expression: str):
    allowed = {
        item.strip().lower()
        for item in os.environ.get("XIAOZHI_DEVICE_USE_DEVICE_IDS", "").split(",")
        if item.strip()
    }
    device_id = conn.headers.get("device-id", "").lower()
    if device_id not in allowed:
        return ActionResponse(Action.RESPONSE, response="当前设备未获准控制 RLCD 表情")
    if expression not in ("happy", "sad", "surprised", "neutral"):
        return ActionResponse(Action.RESPONSE, response="不支持该表情")
    token_path = os.environ.get("RLCD_DEVICE_USE_TOKEN_FILE", "")
    if not token_path or not Path(token_path).is_file():
        return ActionResponse(Action.RESPONSE, response="Device Use Host 令牌未配置")
    token = Path(token_path).read_text(encoding="ascii").strip()
    if len(token) != 64:
        return ActionResponse(Action.RESPONSE, response="Device Use Host 令牌无效")
    try:
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            headers = {"Authorization": f"Bearer {token}"}
            metadata = await call_metadata(conn, session, headers)
            async with session.post(
                control_url("/expression"),
                json={"id": expression, **metadata},
                headers=headers,
            ) as response:
                result = await response.json()
                if response.status != 200 or not result.get("accepted"):
                    raise RuntimeError(result.get("error", "设备未确认表情动作"))
    except (aiohttp.ClientError, TimeoutError, ValueError, RuntimeError) as error:
        return ActionResponse(Action.RESPONSE, response=f"表情动作失败：{error}")
    labels = {"happy": "开心", "sad": "难过", "surprised": "惊讶", "neutral": "平静"}
    text = f"已让设备显示{labels[expression]}表情"
    return ActionResponse(Action.RESPONSE, response=text)
