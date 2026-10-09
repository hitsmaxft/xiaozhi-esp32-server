"""Use the whisper.cpp service supervised by Codex Bridge for RLCD ASR."""

import asyncio
import io
import wave
from typing import Optional

import requests

from core.providers.asr.base import ASRProviderBase
from core.providers.asr.dto.dto import InterfaceType


class ASRProvider(ASRProviderBase):
    def __init__(self, config: dict, delete_audio_file: bool):
        super().__init__()
        self.interface_type = InterfaceType.NON_STREAM
        self.url = config.get("url", "http://127.0.0.1:18792/inference")
        self.language = config.get("language", "zh")
        self.output_dir = config.get("output_dir", "tmp/")
        self.delete_audio_file = delete_audio_file

    def _transcribe(self, pcm: bytes) -> str:
        wav = io.BytesIO()
        with wave.open(wav, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(pcm)
        response = requests.post(
            self.url,
            files={"file": ("recording.wav", wav.getvalue(), "audio/wav")},
            data={"response_format": "json", "temperature": "0.0", "language": self.language},
            timeout=45,
        )
        response.raise_for_status()
        return response.json().get("text", "").strip()

    async def speech_to_text(self, opus_data: list[bytes], session_id: str,
                             artifacts=None) -> tuple[Optional[str], Optional[str]]:
        if artifacts is None or not artifacts.pcm_bytes:
            return "", None
        return await asyncio.to_thread(self._transcribe, artifacts.pcm_bytes), None
