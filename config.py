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
# No Veo 4 found (D20). VEO_MODEL=veo-3.1-lite-generate-preview in .env costs ~1/5 as much
# (the cheapest tier; its picture is unseen): which one is the default is Pietro's call.
VEO_MODEL = os.getenv("VEO_MODEL", "veo-3.1-generate-preview")       # verify

# --- Keys ---------------------------------------------------------------
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# --- Length ---------------------------------------------------------------
DEFAULT_MINUTES = 15
WORDS_PER_MIN = 120          # rough; slow voice. Real length is printed at the end.

# --- Voice: sleep-soft ---------------------------------------------------
# TTS_MODEL=eleven_v4 in .env switches to ElevenLabs' newer model (2026-09-28). It reportedly
# has no speed control, so it may read faster than WORDS_PER_MIN assumes: listen first (D21).
TTS_MODEL = os.getenv("TTS_MODEL", "eleven_multilingual_v2")
VOICE_SETTINGS = {
    "stability": 0.75,          # higher = more even, less dramatic
    "similarity_boost": 0.75,
    "style": 0.0,
    "use_speaker_boost": True,
    "speed": 0.85,              # ElevenLabs accepts roughly 0.7-1.2
}
# The VOICE_SETTINGS a model takes, where it isn't all of them (D21: reported, not verified live).
TTS_SETTINGS_ACCEPTED = {
    "eleven_v4": ("stability", "similarity_boost"),
    "eleven_v4_turbo": ("stability", "similarity_boost"),
}

# --- Video & pacing -------------------------------------------------------
WIDTH, HEIGHT, FPS = 1920, 1080, 30
VEO_RESOLUTION = f"{HEIGHT}p"  # Veo makes 720p or 1080p; the film's size (720p costs less, D20)
assert VEO_RESOLUTION in ("720p", "1080p"), "Veo makes only 720p or 1080p: set VEO_RESOLUTION"
AUDIO_RATE = 48000            # every scene is a whole number of frames = AUDIO_RATE / FPS samples each
assert AUDIO_RATE % FPS == 0, "a video frame must be a whole number of audio samples"
ASPECT_RATIO = "16:9"         # what the image/video models are asked for; matches WIDTH x HEIGHT
LEAD_IN_S = 1.4              # silence before each scene's narration
TAIL_S = 1.4                 # silence after it
XFADE_S = 1.2                # crossfade between scenes; must stay below LEAD_IN_S and TAIL_S (D5, D14)
KEN_BURNS_ZOOM = 0.10        # how far stills drift (10%) over a scene
KEN_BURNS_UPSCALE = 3        # stills are upscaled this much first: zoompan moves in whole (even, in yuv420p) pixels (D18)
# x264 (preset, crf). Scene clips are intermediates that the crossfade re-encodes, so still and
# Manim clips are made fast at higher quality (D18). Veo clips keep a slow encode, in a setting
# of their own so that changing FILM_X264 leaves them alone (D22). The film gets FILM_X264.
CLIP_X264 = ("veryfast", 14)
VEO_CLIP_X264 = ("medium", 18)
FILM_X264 = ("medium", 18)
MAX_AI_SLOWDOWN = 1.6        # stretch Veo clips up to this before holding the last frame
FADE_IN_S = 1.5              # the film fades in from black...
FADE_OUT_S = 2.5             # ...and out to black
FINAL_TAIL_S = FADE_OUT_S + 1.0  # the last scene's tail: a moment of stillness after the last word, then the fade (P2-7)
assert XFADE_S < TAIL_S, "crossfade would overlap the end of a narration"
assert FINAL_TAIL_S >= FADE_OUT_S, "the closing fade would start before the last narration ends"
assert XFADE_S < LEAD_IN_S, "crossfade would overlap the start of the next narration"
assert abs(XFADE_S * FPS - round(XFADE_S * FPS)) < 1e-9, "crossfade must be whole frames (D12)"

# --- Music ----------------------------------------------------------------
MUSIC_DIR = str(PROJECT_DIR / "music")
MUSIC_VOLUME = 0.22          # base level before ducking (lower for sleep)
DUCK_RATIO = 6
MUSIC_FADE_S = 6.0           # fade in/out of the whole bed
MUSIC_XFADE_S = 5.0          # crossfade between tracks / loop repeats (no audible seam)
VOICE_LUFS = -18             # every scene's narration is levelled to this before mixing
VOICE_TP_DBTP = -2.0         # true-peak ceiling of a levelled narration: a limiter joins only if the gain would pass it (P2-6)
assert -20 <= VOICE_TP_DBTP <= 0, "the narration limiter's ceiling must lie between -20 and 0 dBTP"
LEVEL_ROUNDS = 3             # a limited narration's loudness is re-read at most this often, each time correcting the gain
LEVEL_MAX_BOOST_DB = 3.0     # ...and its gain may rise at most this far above the linear one: past it the limiter eats what it adds
LEVEL_MAX_ERR_LU = 0.5       # a narration further than this from VOICE_LUFS is reported
LIMITER_ATTACK_MS = 5        # the narration limiter (alimiter): lookahead...
LIMITER_RELEASE_MS = 50      # ...and release
LIMITER_RATE = 192000        # it works at this rate, where a sample peak is the true peak (at 48 kHz bursts overshoot ~1.1 dB)
LIMITER_MARGIN_DB = 0.1      # its limit sits this far under VOICE_TP_DBTP, for rounding
assert 0.1 <= LIMITER_ATTACK_MS <= 80 and 1 <= LIMITER_RELEASE_MS <= 8000, "outside what alimiter accepts"
assert 0.0625 <= 10 ** ((VOICE_TP_DBTP - LIMITER_MARGIN_DB) / 20) <= 1, "the limiter's limit must lie between -24 and 0 dBFS"
TARGET_LUFS = -16            # final master
MASTER_TP_DBTP = -2.0        # true-peak ceiling for loudnorm; AAC adds a little, QA allows -1 (was -1.5)

# --- Visual style (P3-2) ---------------------------------------------------------
# Only the pictures change between presets; voice, pacing and music stay. Each prompt suffix
# is part of its picture's cache key (D9), so `calm` must stay byte-identical or every paid
# still, clip and Manim render is bought again. Only the look's traits go in a prompt, never
# the name of a studio or its characters. VISUAL_STYLE in .env sets STYLE, the default for a build
# that has no recorded style (STYLE_FILE); `--style` overrides both for a run.
STYLES = {
    "calm": {
        "still": (" Calm, dark, low-contrast, cinematic, deep blues and soft warm highlights, "
                  "lots of negative space, soft focus edges. No text, no letters, no watermark, "
                  "no people's faces."),
        "ai_video": (" Calm documentary footage, one continuous slow camera move, soft natural "
                     "light, muted colours, shallow depth of field. No text, no captions, no logos, "
                     "no people's faces."),
        "manim": ('- Style: background "#0f1419". Soft palette: "#7fb3d5", "#a9dfbf", "#f5cba7",\n'
                  '  "#d7bde2", "#fdfefe". No flashing, no bright full-screen colour.'),
        "manim_background": "#0f1419",
    },
    "flat": {
        "still": (" Flat vector illustration: clean geometric shapes with rounded corners, smooth flat "
                  "colour fields with soft gradients and a gentle rim light, a few small soft stars or "
                  "specks of light. Deep navy and indigo background with warm amber, coral and teal "
                  "accents. Calm and uncluttered, generous negative space, low contrast, no harsh "
                  "edges. No text, no letters, no watermark, no faces, no characters."),
        "ai_video": (" Smooth 2D flat vector animation: clean geometric shapes with soft gradients, one "
                     "continuous slow camera move, deep navy and indigo with warm amber, coral and teal "
                     "accents, calm and uncluttered. No text, no captions, no logos, no faces, "
                     "no characters."),
        "manim": ('- Style: background "#162044" (deep navy). Flat vector look: filled shapes (fill_opacity 1,\n'
                  '  thin or no stroke), rounded forms (Circle, RoundedRectangle), soft glows made by stacking 3-4\n'
                  '  concentric circles of falling opacity, a few small dim dots as distant stars.\n'
                  '  Palette: "#f4a261" amber, "#e76f51" coral, "#2a9d8f" teal, "#8ecae6" sky, "#e9c46a" gold,\n'
                  '  "#f1faee" off-white for text. No flashing, no bright full-screen colour.'),
        "manim_background": "#162044",  # luma 32.5/255 against calm's 19.3: above QA_BLACK_PIX_TH too
    },
}
# Two lists for the words of a still's or clip's description (P3-5). Under `flat` the description
# comes BEFORE the style suffix (D25), so a word of the wrong kind can outweigh the suffix. The
# test_topics checks keep both lists out of every `still` and `ai_video` description in topics/ (a
# `manim` description is a diagram, so it is exempt). Both are matched whole-word and
# case-insensitive, a space in a phrase also matching a hyphen or a line break, so "blur" does not
# catch "blurred" and the common inflections are listed; review catches the rest. A description edit
# re-keys its picture (D9): do the edit before the picture is bought.
#
# FLAT_PHOTO_CUES: words that pull the picture toward a photograph, in any style. Camera-motion
# words ("slow camera move") are not cues: Veo is prompted with them.
FLAT_PHOTO_CUES = (
    # optics and focus
    "blur", "blurred", "blurry", "blurring", "blurs", "soft focus", "out of focus", "focus", "focused",
    "unfocused", "defocused", "focusing", "depth of field", "shallow depth", "bokeh", "lens", "lenses",
    "sharp", "sharply", "sharper", "sharpness", "sharpened", "diffuse", "diffused", "diffusion", "diffusing",
    "blurrier", "focuses", "vignette",
    # air and surface texture
    "mist", "mists", "misty", "misted", "misting", "haze", "hazes", "hazy", "hazier", "fog", "fogs", "fogged",
    "foggy", "foggier",
    "dew", "dewy", "dewdrop", "dewdrops",
    # light on water, and the camera's own words
    "moonlight", "moonlit", "moonbeam", "moonbeams", "reflect", "reflects", "reflecting", "reflected",
    "reflection", "reflections",
    "photograph", "photographs", "photographed", "photographing", "photography", "photographic", "photo",
    "photos", "photorealistic", "hyperrealistic", "realistic", "lifelike", "cinematic", "footage", "film grain",
    "grainy", "exposure", "long exposure", "aperture", "macro", "telephoto", "volumetric", "god ray", "god rays",
)
# STYLE_MEDIUM_CUES: words that name the medium (vector, painting, cartoon). The medium belongs to
# the style suffix; a description names the subject, composition, light and colour only, so one
# reviewed script serves every style and `calm` stays the default (D25). Ordinary uses of "flat"
# ("flat ground", "a flat calm sea") are fine.
STYLE_MEDIUM_CUES = (
    "vector", "illustration", "illustrations", "illustrated", "drawn as", "flat shape", "flat shapes",
    "flat colour", "flat colours", "flat color", "flat colors", "cartoon", "cartoons", "painting",
    "paintings", "painterly", "watercolour", "watercolours", "watercolor", "watercolors", "oil painting",
    "oil paintings", "brushstroke", "brushstrokes",
)
# Not named STYLE in the environment: a stray one in a shell would stop every import.
STYLE = os.getenv("VISUAL_STYLE", "calm")
assert STYLE in STYLES, f"VISUAL_STYLE must be one of {sorted(STYLES)}, got {STYLE!r}"

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

# --- YouTube (metadata.py) --------------------------------------------------------
# First lines of every description; chapter timestamps follow. Change to taste.
YOUTUBE_DESCRIPTION = "A calm, slow science documentary to relax or fall asleep to."

# --- Thumbnail (thumbnail.py, P2-2) -----------------------------------------------
# The image model draws the picture and is never asked for text (it garbles letters); the title
# is drawn by us over a dark gradient, in the left of the frame. The picture's prompt is the first
# still's description + THUMBNAIL_COMPOSITION + the active style's still suffix.
THUMBNAIL_FILE = "thumbnail.jpg"      # in the build folder, next to metadata.json, which names it
THUMBNAIL_SIZE = (1280, 720)          # YouTube's recommended size
THUMBNAIL_MAX_BYTES = 2_000_000       # YouTube's limit is 2 MB
THUMBNAIL_JPEG_QUALITY = (92, 85, 78, 70, 60)  # tried in turn until the file fits
# Doesn't say what the empty space is for: naming a title could make the model draw one. {pct} is
# filled in from THUMBNAIL_COLUMN (to the nearest 5), so the picture is asked to keep clear what the title covers.
THUMBNAIL_COMPOSITION = (" Composition: calm, dark, empty space across about the left {pct}% of the frame; "
                         "the subject sits to the right of it. No text.")
THUMBNAIL_MARGIN_PX = 72              # the title's distance from the left edge...
THUMBNAIL_COLUMN = 0.46               # ...and where its right edge may reach, as a share of the width
THUMBNAIL_MAX_LINES = 3
THUMBNAIL_FONT_PX = (110, 30)         # the title starts at the first size and shrinks to fit, down to the second
THUMBNAIL_SPLIT_LINES = (2, 2)        # a title with a ": " is split there into a headline and a subtitle: their line limits
THUMBNAIL_SUBTITLE_RATIO = 0.5        # the subtitle's size as a share of the headline's (never under the smallest THUMBNAIL_FONT_PX)
THUMBNAIL_SUBTITLE_GAP = 0.25         # the space between them, as a share of the headline's size
THUMBNAIL_SUBTITLE_OPACITY = 0.85     # the subtitle's off-white, a little dimmer than the headline's
THUMBNAIL_LINE_SPACING = 1.2
THUMBNAIL_TITLE_RGB = (244, 239, 230)  # warm off-white
THUMBNAIL_SCRIM = ((8, 10, 14), 0.8, 0.7)  # colour, opacity (held across the title column) and the share of the width where it has faded out
THUMBNAIL_SHADOW = (0.1, 1.6, 0.05)   # the title's soft shadow: blur, strength, and how far it drops (blur and drop as shares of the font size)
assert 0 < THUMBNAIL_FONT_PX[1] <= THUMBNAIL_FONT_PX[0], "THUMBNAIL_FONT_PX is (largest, smallest)"
assert THUMBNAIL_MARGIN_PX < THUMBNAIL_COLUMN * THUMBNAIL_SIZE[0], "the title column would have no width"
assert THUMBNAIL_SCRIM[2] > THUMBNAIL_COLUMN, "the scrim must fade out beyond the title column"
assert 0 < THUMBNAIL_SUBTITLE_RATIO <= 1 and 0 <= THUMBNAIL_SUBTITLE_OPACITY <= 1, "ratio and opacity are shares"

# --- Pipeline ---------------------------------------------------------------
BRIEF_FILE = "brief.md"         # the research brief (--brief), kept in the build folder; metadata reads its Sources
BRIEF_KEY_FILE = "brief.key"    # which brief the script stage was written with, so a change can't go unnoticed
STYLE_FILE = "style"            # which style the build's pictures are made in, so a rerun can't quietly switch
MAX_CRITIC_ROUNDS = 2
MAX_MANIM_ATTEMPTS = 3
WORKERS = 4                   # scenes whose narration / visual are generated at the same time
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
TTS_USD_PER_1K_CHARS = {                # verify: ElevenLabs API, by TTS_MODEL (D21)
    "eleven_multilingual_v2": 0.10,
    "eleven_v4": 0.08,                  # reported list price; a launch price of 0.022 to 2026-10-12
}
TTS_USD_PER_1K_CHARS_UNLISTED = max(TTS_USD_PER_1K_CHARS.values())  # a model not listed: the priciest
IMAGE_USD_PER_IMAGE = {"1K": 0.067, "2K": 0.101, "4K": 0.151}  # verify: Gemini 3.1 Flash Image, by IMAGE_SIZE
# Veo on the Gemini API always includes audio (it can't be turned off there; we discard it),
# so these are the with-audio prices, by model and VEO_RESOLUTION. Verify (D20).
VEO_USD_PER_SECOND = {
    "veo-3.1-lite-generate-preview": {"720p": 0.05, "1080p": 0.08},
    "veo-3.1-generate-preview": {"720p": 0.40, "1080p": 0.40},
}
VEO_USD_PER_SECOND_UNLISTED = 0.40      # a model or resolution not listed: assume the priciest
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
