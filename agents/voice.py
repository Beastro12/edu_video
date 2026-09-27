"""Agent 3: ElevenLabs narration, one file per scene."""
from pathlib import Path

import requests

import config

URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=mp3_44100_128"


def narrate(text: str, out_path: Path) -> Path:
    if out_path.exists():
        return out_path  # cached: don't pay twice
    r = requests.post(
        URL.format(voice=config.ELEVENLABS_VOICE_ID),
        headers={"xi-api-key": config.ELEVENLABS_API_KEY,
                 "Content-Type": "application/json"},
        json={"text": text, "model_id": config.TTS_MODEL,
              "voice_settings": config.VOICE_SETTINGS},
        timeout=120,
    )
    if r.status_code != 200:
        raise RuntimeError(f"ElevenLabs {r.status_code}: {r.text[:500]}")
    out_path.write_bytes(r.content)
    return out_path
