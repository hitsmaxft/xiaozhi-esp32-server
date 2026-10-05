from types import SimpleNamespace
import unittest

from core.handle.receiveAudioHandle import unpack_asr_text
from core.providers.llm.ollama.ollama import LLMProvider


class RlcdVoicePipelineTests(unittest.TestCase):
    def test_asr_metadata_does_not_enter_user_text(self):
        self.assertEqual(
            unpack_asr_text(
                '{"content":"做出一个微笑的表情。","language":"zh","emotion":"😶"}'
            ),
            ("做出一个微笑的表情。", None),
        )
        self.assertEqual(
            unpack_asr_text('{"speaker":"Alice","content":"笑一个"}'),
            ("笑一个", "Alice"),
        )

    def test_ollama_tool_stream_hides_reasoning_and_keeps_tool_call(self):
        call = SimpleNamespace(function=SimpleNamespace(name="set_rlcd_expression"))

        def chunk(content=None, tool_calls=None):
            return SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(
                    content=content, tool_calls=tool_calls
                ))]
            )

        class Stream:
            def __iter__(self):
                return iter((
                    chunk("<thi"), chunk("nk>internal</think>"),
                    chunk(tool_calls=[call]), chunk("visible answer"),
                ))

            def close(self):
                pass

        provider = object.__new__(LLMProvider)
        provider.model_name = "qwen3:4b-instruct"
        provider.client = SimpleNamespace(chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kwargs: Stream())
        ))
        result = list(provider.response_with_functions("test", [], []))
        self.assertEqual(result, [(None, [call]), ("visible answer", None)])
