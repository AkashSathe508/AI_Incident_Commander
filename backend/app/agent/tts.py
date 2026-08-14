"""
ElevenLabs Text-to-Speech Module.

Synthesizes short text summaries to raw 16kHz 16-bit mono PCM audio bytes
for outgoing playback in the Agora meeting room.

Confirmed API (from ElevenLabs official REST API docs):
    POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=pcm_16000
    Headers: {"xi-api-key": apiKey}
    Body: {"text": text, "model_id": "eleven_turbo_v2_5"}
"""

import logging
import os
import httpx

from app.config.settings import settings

logger = logging.getLogger(__name__)

# Default voice: "Rachel" (21m00Tcm4TlvDq8ikWAM)
DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"
DEFAULT_MODEL_ID = "eleven_turbo_v2_5"


def synthesize_speech(text_summary: str, voice_id: str = DEFAULT_VOICE_ID) -> bytes:
    """
    Synthesizes text summary into 16kHz 16-bit mono PCM audio bytes using ElevenLabs API.

    Returns:
        bytes: Raw PCM audio bytes (32,000 bytes per second)
    """
    if not text_summary or not text_summary.strip():
        return b""

    api_key = settings.elevenlabs_api_key or os.environ.get("ELEVENLABS_API_KEY", "")
    if not api_key:
        logger.warning(
            "ELEVENLABS_API_KEY is not set — cannot synthesize spoken audio summary"
        )
        return b""

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=pcm_16000"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "text": text_summary.strip(),
        "model_id": DEFAULT_MODEL_ID,
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(url, headers=headers, json=payload)
            if resp.status_code == 200:
                pcm_bytes = resp.content
                logger.info(
                    "[TTS] Synthesized %d bytes of 16kHz PCM audio for text: '%s'",
                    len(pcm_bytes),
                    text_summary[:50],
                )
                return pcm_bytes
            else:
                logger.error(
                    "ElevenLabs TTS API error (HTTP %d): %s",
                    resp.status_code,
                    resp.text[:200],
                )
                return b""
    except Exception as exc:
        logger.error("ElevenLabs TTS request failed: %s", exc)
        return b""
