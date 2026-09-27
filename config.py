"""All tunable settings in one place. Change these, not the agents."""
import math
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
PROJECT_DIR = Path(__file__).resolve().parent  # build/ and music/ don't depend on the cwd

# --- Models (names change often; override in .env if a call fails) -----
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5")
# Imagen 4 and Veo 3.0 were retired from the Gemini API in 2026 (D13). Verify these names.
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "gemini-3.1-flash-image")   # verify
IMAGE_SIZE = "1K"                                                  # verify; 2K/4K cost more per image
VEO_MODEL = os.getenv("VEO_MODEL", "veo-3.1-generate-preview")      # verify

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
AUDIO_RATE = 48000            # every scene is a whole number of frames = AUDIO_RATE / FPS samples each
assert AUDIO_RATE % FPS == 0, "a video frame must be a whole number of audio samples"
ASPECT_RATIO = "16:9"         # what the image/video models are asked for; matches WIDTH x HEIGHT
LEAD_IN_S = 1.4              # silence before each scene's narration
TAIL_S = 1.4                 # silence after it
XFADE_S = 1.2                # crossfade between scenes; must stay below LEAD_IN_S and TAIL_S (D5, D14)
KEN_BURNS_ZOOM = 0.10        # how far stills drift (10%) over a scene
MAX_AI_SLOWDOWN = 1.6        # stretch Veo clips up to this before holding the last frame
FADE_IN_S = 1.5              # the film fades in from black...
FADE_OUT_S = 2.5             # ...and out to black
assert XFADE_S < TAIL_S, "crossfade would overlap the end of a narration"
assert XFADE_S < LEAD_IN_S, "crossfade would overlap the start of the next narration"
assert abs(XFADE_S * FPS - round(XFADE_S * FPS)) < 1e-9, "crossfade must be whole frames (D12)"

# --- Music ----------------------------------------------------------------
MUSIC_DIR = str(PROJECT_DIR / "music")
MUSIC_VOLUME = 0.22          # base level before ducking (lower for sleep)
DUCK_RATIO = 6
MUSIC_FADE_S = 6.0           # fade in/out of the whole bed
MUSIC_XFADE_S = 5.0          # crossfade between tracks / loop repeats (no audible seam)
VOICE_LUFS = -18             # every scene's narration is levelled to this before mixing
TARGET_LUFS = -16            # final master
MASTER_TP_DBTP = -2.0        # true-peak ceiling for loudnorm; AAC adds a little, QA allows -1 (was -1.5)

# --- QA (qa.py) ---------------------------------------------------------------
QA_DURATION_TOL = 0.30        # film length vs the script's estimate (words at WORDS_PER_MIN)
QA_LOUDNESS_TOL_LU = 1.0      # integrated loudness within this of TARGET_LUFS
QA_MAX_TRUE_PEAK_DBTP = -1.0
QA_MIN_DUCKING_DB = 6.0       # music during speech vs during pauses
QA_SPEECH_DB = -40.0          # narration louder than this (dBFS, 100 ms RMS) counts as speech
QA_SILENCE_DB = -50.0         # ...quieter than this counts as silence
QA_BLACK_MIN_S = 0.5          # shortest black segment reported
QA_BLACK_PIX_TH = 0.05        # luma below this counts as black; 0.10 flagged the Manim background
QA_PAUSE_S = 1.0              # voice silent this long = a pause (the ducking compressor has released)
QA_ATTACK_S = 0.3             # skip this much of each speech onset (compressor attack)
assert LEAD_IN_S + TAIL_S - XFADE_S >= QA_PAUSE_S + 0.2, \
    "pauses between narrations too short for the music to come back (and for qa.py to hear it)"

# --- Pipeline ---------------------------------------------------------------
MAX_CRITIC_ROUNDS = 2
MAX_MANIM_ATTEMPTS = 3
MANIM_MAX_SHORTFALL = 0.25    # a render this much shorter than its scene gets one retiming request
STILL_REVIEW_RETRIES = 2      # a still Claude rejects is regenerated with the critique this many times, then Manim
STILL_REVIEW_MAX_TOKENS = 500 # the verdict is a short JSON
PREVIEW_PX = 1024             # long edge of the JPEG preview Claude reviews
# Transient API errors (429, 5xx, timeouts): retried with jittered exponential backoff,
# wait = min(base * 2**n + random(0, jitter), max). Other errors fail on the first try.
MAX_RETRIES = 4
RETRY_BASE_S = 1.0
RETRY_JITTER_S = 1.0
RETRY_MAX_S = 30.0
GOOGLE_TIMEOUT_S = 120        # per HTTP request to Google (the SDK has none by default)
BUILD_DIR = str(PROJECT_DIR / "build")

# --- Money --------------------------------------------------------------------
# Total spend allowed across all runs, summed from build/ledger.jsonl. Checked before
# every paid call; a call that would pass it stops the run.
BUDGET_EUR = float(os.getenv("BUDGET_EUR", "10"))
assert math.isfinite(BUDGET_EUR) and BUDGET_EUR >= 0, "BUDGET_EUR must be a number >= 0"

# Prices are estimates for the ledger. VERIFY each against the provider's own price page
# (last checked 2026-09-27 from secondary sources; see DECISIONS D10).
USD_TO_EUR = 0.92                       # verify
CLAUDE_USD_PER_MTOK = {                 # (input, output) per million tokens; verify
    "claude-sonnet-5": (2.00, 10.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-haiku-4-5": (1.00, 5.00),
}
CLAUDE_USD_PER_MTOK_UNLISTED = (10.00, 50.00)  # a model not listed above: assume the priciest tier
TTS_USD_PER_1K_CHARS = 0.10             # verify: ElevenLabs API, Multilingual v2
IMAGE_USD_PER_IMAGE = {"1K": 0.067, "2K": 0.101, "4K": 0.151}  # verify: Gemini 3.1 Flash Image, by IMAGE_SIZE
VEO_USD_PER_SECOND = 0.40               # verify
VEO_CLIP_S = 8                          # verify: length of one Veo clip, billed per second
VEO_MAX_PER_DAY = 2                     # CLAUDE.md hard rule; counted from the ledger, per UTC day

# Rough sizes used only by `--estimate` (and the pre-call check for Claude's input).
EST_CHARS_PER_TOKEN = 3                 # conservative: more tokens than typical English
EST_CHARS_PER_WORD = 6                  # narration characters per word, spaces included
EST_WORDS_PER_SCENE = 55                # writer is asked for 40-70
EST_CHAPTERS = 5                        # outline is asked for 4-6
EST_OUTLINE_TOKENS_PER_CHAPTER = 300    # outline JSON out
EST_MANIM_SHARE = 0.3                   # D3: ~30% of scenes are diagrams; plus one Veo scene per chapter
EST_PROMPT_TOKENS = 2_000               # system prompt + outline sent with each script call
EST_SCRIPT_TOKENS_PER_WORD = 3          # chapter JSON out: narration + visual descriptions
EST_MANIM_TOKENS = (1_500, 2_500)       # (input, output) per Manim attempt
EST_IMAGE_TOKENS = 1_600                # one image in a Claude request (1024 px preview)
EST_STILL_REVIEW_TOKENS = (2_000, 200)  # (input, output) per vision review of a still
