"""Anthropic Messages adapter for the local CC Switch Claude proxy."""

import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

from anthropic import Anthropic

from core.providers.llm.base import LLMProviderBase


class LLMProvider(LLMProviderBase):
    def __init__(self, config):
        settings_path = Path(config.get("claude_settings", "~/.claude/settings.json")).expanduser()
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        env = settings.get("env", {})
        base_url = config.get("base_url") or env.get("ANTHROPIC_BASE_URL", "")
        if urlparse(base_url).hostname not in ("127.0.0.1", "localhost"):
            raise ValueError("CC Switch Anthropic endpoint must be local")
        api_key = env.get("ANTHROPIC_AUTH_TOKEN") or env.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("Claude settings contain no CC Switch auth token")
        self.model_name = config.get("model_name", "openrouter/free")
        self.max_tokens = int(config.get("max_tokens", 512))
        self.client = Anthropic(base_url=base_url, api_key=api_key, timeout=40)

    @staticmethod
    def _messages(dialogue):
        system = []
        messages = []
        for item in dialogue:
            role = item.get("role")
            if role == "system":
                if item.get("content"):
                    system.append(item["content"])
                continue
            if role == "tool":
                role = "user"
                blocks = [{
                    "type": "tool_result",
                    "tool_use_id": item["tool_call_id"],
                    "content": item.get("content") or "",
                }]
            elif role == "assistant" and item.get("tool_calls"):
                blocks = []
                for call in item["tool_calls"]:
                    function = call["function"]
                    blocks.append({
                        "type": "tool_use",
                        "id": call["id"],
                        "name": function["name"],
                        "input": json.loads(function.get("arguments") or "{}"),
                    })
            elif role in ("user", "assistant"):
                blocks = [{"type": "text", "text": item.get("content") or ""}]
            else:
                continue
            if messages and messages[-1]["role"] == role:
                messages[-1]["content"].extend(blocks)
            else:
                messages.append({"role": role, "content": blocks})
        return "\n\n".join(system), messages

    @staticmethod
    def _tools(functions):
        return [{
            "name": item["function"]["name"],
            "description": item["function"].get("description", ""),
            "input_schema": item["function"].get("parameters") or {
                "type": "object", "properties": {}
            },
        } for item in functions]

    def _complete(self, dialogue, functions=None):
        system, messages = self._messages(dialogue)
        request = {
            "model": self.model_name,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": messages,
        }
        if functions:
            request["tools"] = self._tools(functions)
        return self.client.messages.create(**request)

    def response(self, session_id, dialogue, **kwargs):
        result = self._complete(dialogue)
        text = "".join(block.text for block in result.content if block.type == "text").strip()
        if not text:
            raise RuntimeError("CC Switch returned no answer")
        yield text

    def response_with_functions(self, session_id, dialogue, functions=None):
        result = self._complete(dialogue, functions)
        calls = []
        text_parts = []
        for block in result.content:
            if block.type == "tool_use":
                calls.append(SimpleNamespace(
                    index=len(calls),
                    id=block.id,
                    function=SimpleNamespace(
                        name=block.name,
                        arguments=json.dumps(block.input, ensure_ascii=False),
                    ),
                ))
            elif block.type == "text":
                text_parts.append(block.text)
            # Anthropic thinking and signature blocks never enter speech.
        if calls:
            yield None, calls
        text = "".join(text_parts).strip()
        if text and not calls:
            yield text, None
        if not calls and not text:
            raise RuntimeError("CC Switch returned no answer or tool call")
