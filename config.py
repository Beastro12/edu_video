"""All tunable settings in one place. Change these, not the agents."""
import os

from dotenv import load_dotenv

load_dotenv()

# --- Models (names change often; override in .env if a call fails) -----
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5")
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "imagen-4.0-generate-001")
VEO_MODEL = os.getenv("VEO_MODEL", "veo-3.0-generate-001")

# --- Keys ---------------------------------------------------------------
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# --- Length ---------------------------------------------------------------
DEFAULT_MINUTES = 15
WORDS_PER_MIN = 120          # rough; slow voice. Real length is printed at the end.

# --- Voice: sleep-soft ---------------------------------------------------
TTS_MODEL = "eleven_multilingual_v2"
VOICE_SETTINGS = {
    "stability": 0.75,          # higher = more even, less dramatic
    "similarity_boost": 0.75,
    "style": 0.0,
    "use_speaker_boost": True,
    "speed": 0.85,              # ElevenLabs accepts roughly 0.7-1.2
}

# --- Video & pacing -------------------------------------------------------
WIDTH, HEIGHT, FPS = 1920, 1080, 30
ASPECT_RATIO = "16:9"         # what the image/video models are asked for; matches WIDTH x HEIGHT
LEAD_IN_S = 0.6              # silence before each scene's narration
TAIL_S = 1.8                 # silence after it
XFADE_S = 1.2                # crossfade between scenes; must stay below TAIL_S
KEN_BURNS_ZOOM = 0.10        # how far stills drift (10%) over a scene
MAX_AI_SLOWDOWN = 1.6        # stretch Veo clips up to this before holding the last frame
assert XFADE_S < TAIL_S, "crossfade would overlap narration"

# --- Music ----------------------------------------------------------------
MUSIC_DIR = "music"
MUSIC_VOLUME = 0.22          # base level before ducking (lower for sleep)
DUCK_RATIO = 6
MUSIC_FADE_S = 6.0           # fade in/out of the whole bed
MUSIC_XFADE_S = 5.0          # crossfade between tracks / loop repeats (no audible seam)
VOICE_LUFS = -18             # every scene's narration is levelled to this before mixing
TARGET_LUFS = -16            # final master

# --- Pipeline ---------------------------------------------------------------
MAX_CRITIC_ROUNDS = 2
MAX_MANIM_ATTEMPTS = 3
BUILD_DIR = "build"
