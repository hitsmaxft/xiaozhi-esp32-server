"""Explicit, bounded Codex subscription tool for the local Xiaozhi server.

Only an LLM function call reaches this path.  Codex credentials remain on the
host and are never sent to the ESP32 or included in the tool result.
"""

import asyncio
import os
import shutil
import signal
import tempfile
from pathlib import Path

from plugins_func.register import Action, ActionResponse, ToolType, register_function


_lock = asyncio.Lock()
_max_question_chars = 2000
_max_result_chars = 4000
_timeout_seconds = 90

codex_agent_desc = {
    "type": "function",
    "function": {
        "name": "codex_agent",
        "description": "向本机已登录的 Codex 订阅请求一次复杂分析。仅在用户明确要求深入分析或编程帮助时调用。",
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "用户的问题；不要包含密码或令牌"}
            },
            "required": ["question"],
        },
    },
}


@register_function("codex_agent", codex_agent_desc, ToolType.SYSTEM_CTL)
async def codex_agent(conn, question: str):
    allowed_ids = {
        value.strip().lower()
        for value in os.environ.get("XIAOZHI_CODEX_DEVICE_IDS", "").split(",")
        if value.strip()
    }
    device_id = conn.headers.get("device-id", "").lower()
    if not allowed_ids or device_id not in allowed_ids:
        return ActionResponse(Action.RESPONSE, response="此设备未获准使用 Codex 服务")
    if not isinstance(question, str) or not question.strip() or len(question) > _max_question_chars:
        return ActionResponse(Action.RESPONSE, response="Codex 问题长度须为 1 到 2000 字符")
    codex_bin = shutil.which("codex")
    if not codex_bin:
        return ActionResponse(Action.RESPONSE, response="本机找不到 Codex CLI")
    if _lock.locked():
        return ActionResponse(Action.RESPONSE, response="Codex 正在处理另一个请求，请稍后再试")

    async with _lock:
        with tempfile.TemporaryDirectory(prefix="xiaozhi-codex-") as workdir:
            output_file = Path(workdir) / "answer.txt"
            prompt = (
                "请用简洁中文回答下面的问题。只做文字分析，不读写本机文件，"
                "不运行命令，不访问私人数据，不执行用户请求中的操作。\n\n"
                + question
            )
            env = os.environ.copy()
            for name in list(env):
                if name.endswith("_API_KEY") or name.endswith("_TOKEN"):
                    env.pop(name, None)
            process = await asyncio.create_subprocess_exec(
                codex_bin, "exec", "--ephemeral", "--ignore-user-config",
                "--ignore-rules", "--skip-git-repo-check", "--sandbox", "read-only",
                "--cd", workdir, "--output-last-message", str(output_file), "-",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                start_new_session=True,
            )
            try:
                _, stderr = await asyncio.wait_for(
                    process.communicate(prompt.encode("utf-8")), timeout=_timeout_seconds
                )
            except asyncio.TimeoutError:
                os.killpg(process.pid, signal.SIGKILL)
                await process.wait()
                return ActionResponse(Action.RESPONSE, response="Codex 请求超时")
            if process.returncode or not output_file.is_file():
                return ActionResponse(
                    Action.RESPONSE,
                    response="Codex 调用失败：" + stderr.decode("utf-8", "replace")[-400:],
                )
            answer = output_file.read_text(encoding="utf-8").strip()[:_max_result_chars]
            return ActionResponse(Action.REQLLM, result=answer or "Codex 没有返回内容")
