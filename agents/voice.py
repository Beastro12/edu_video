"""Agent 3: ElevenLabs narration, one file per scene, named by what produced it."""
from pathlib import Path

import requests

import config
import ledger
import retries
from utils import atomic_output, content_key

URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=mp3_44100_128"


def cache_path(text: str, out_dir: Path) -> Path:
    key = content_key("tts", text, config.ELEVENLABS_VOICE_ID, config.TTS_MODEL,
                      config.VOICE_SETTINGS, URL)
    return out_dir / f"{key}.mp3"


def narrate(text: str, out_dir: Path) -> Path:
    out = cache_path(text, out_dir)
    if out.exists():
        return out  # cached: same text and voice, don't pay twice
    cost = ledger.tts_eur(len(text))
    ledger.check("elevenlabs", cost)

    def post():
        r = requests.post(
            URL.format(voice=config.ELEVENLABS_VOICE_ID),
            headers={"xi-api-key": config.ELEVENLABS_API_KEY,
                     "Content-Type": "application/json"},
            json={"text": text, "model_id": config.TTS_MODEL,
                  "voice_settings": config.VOICE_SETTINGS},
            timeout=120,
        )
        if r.status_code in retries.TRANSIENT_STATUS:
            raise retries.TransientError(f"ElevenLabs {r.status_code}: {r.text[:200]}",
                                         retries.retry_after_s(r.headers))
        return r

    r = retries.call(post, "ElevenLabs")
    if r.status_code != 200:
        raise RuntimeError(f"ElevenLabs {r.status_code}: {r.text[:500]}")
    ledger.record("elevenlabs", config.TTS_MODEL, {"characters": len(text)}, cost)
    with atomic_output(out) as tmp:
        tmp.write_bytes(r.content)
    return out
