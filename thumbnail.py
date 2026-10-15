"""The YouTube thumbnail (P2-2): a 1280x720 JPEG under YouTube's 2 MB, next to metadata.json.

The picture is one image from the image model, through image_agent's own request, cache, ledger
reservation and failure record. The model is never asked for text: the title is drawn here with
Pillow, over a dark gradient on the left, where the prompt asks for empty space: a title with a
": " as a large headline over a smaller subtitle, any other as one block. Without a Google
key, or when the image model can't be used, the picture is a frame of the finished film (free).
The film is already made when this runs, so a thumbnail never fails the run: whatever goes wrong
is logged, returned as an Outcome for the run report, and the film goes on to QA."""
import functools
import io
import tempfile
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

import PIL
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

import config
import ledger
import metadata
from agents import image_agent
from failures import FailedEarlier, Failures, lasting
from utils import (
    content_key,
    is_fresh,
    load_json,
    log,
    redact,
    remove_output,
    run,
    stamped_output,
    video_duration,
)

RETRY, AGAIN = "`--retry-failed` asks again", "a rerun asks again (the error looks transient, so none is recorded)"


class Outcome(NamedTuple):
    """What became of the thumbnail, for the run report (P2-3). `source` is "picture" (the image
    model's), "frame" (of the film) or "none"; `reason` says why it is not the picture."""
    source: str
    reason: str | None
    hint: str | None
    path: Path | None


class Picture(NamedTuple):
    """The picture a thumbnail is drawn on: `identity` names it, so that a different one redraws
    the thumbnail; `load` opens it."""
    identity: str
    source: str
    load: Callable[[], Image.Image]
    reason: str | None = None
    hint: str | None = None


def _composition() -> str:
    pct = round(config.THUMBNAIL_COLUMN * 20) * 5  # what the title covers, to the nearest 5%
    return config.THUMBNAIL_COMPOSITION.format(pct=pct)


def _rejected_for_good(scene: dict, visuals: Path) -> bool:
    return image_agent.is_decided(scene, visuals) and image_agent.review_log(scene, visuals)["accepted"] is None


def picture_scene(scenes: list[dict], visuals: Path) -> dict | None:
    """What the image model is asked to draw: the first still's description, else the first Veo
    scene's, with the composition note. A still's description is already written for the image
    model and ends in "No text", which a chapter summary (prose about ideas, so diagrams and
    lettering) does not; a still the review turned down for good (text, wrong physics, unsettling:
    `visuals` has its log) is no subject; a film of diagrams only has none, and gets a frame."""
    for kind in ("still", "ai_video"):
        for scene in scenes:
            if scene["visual_type"] == kind and not (kind == "still" and _rejected_for_good(scene, visuals)):
                return {"id": "thumbnail",
                        "visual_description": scene["visual_description"].rstrip() + _composition()}
    return None


def _key(title: str, identity: str) -> str:
    """What the thumbnail is drawn from: the title, the picture, the style, every THUMBNAIL_* setting,
    the stand-in characters and the Pillow version (its bundled font)."""
    settings = {k: v for k, v in vars(config).items() if k.startswith("THUMBNAIL_")}
    return content_key("thumbnail", title, identity, image_agent.style(), settings, STAND_INS, PIL.__version__)


def _current(work: Path, title: str, scene: dict) -> bool:
    """True if thumbnail.jpg was drawn from this scene's picture and this title: nothing to buy or draw,
    whether or not the picture itself is still on disk."""
    picture = image_agent.cache_path(scene, work / "visuals")
    return is_fresh(work / config.THUMBNAIL_FILE, _key(title, picture.name))


def estimate(scenes: list[dict], work: Path, failed: Callable[[str], dict | None]) -> float:
    """EUR for the thumbnail's picture on a run now: one image (no still review: the picture is a
    background and the title is ours), unless it is already made, the thumbnail is up to date, it
    failed before (the frame is used then) or can't be asked for."""
    scene = picture_scene(scenes, work / "visuals")
    if scene is None or not image_agent.available():
        return 0.0
    out = image_agent.cache_path(scene, work / "visuals")
    try:
        title = load_json(work / "metadata.json")["title"]
    except (OSError, ValueError, KeyError):
        title = None
    if out.exists() or failed(out.stem) or (title is not None and _current(work, title, scene)):
        return 0.0
    return ledger.image_eur()


@functools.cache
def _font(px: int) -> ImageFont.FreeTypeFont:
    """Pillow's bundled font, scalable since 10.1 (Aileron Regular): no system font path to depend on."""
    return ImageFont.load_default(size=px)


_GREEK = "αβγδεζηθικλμνξοπρστυφχψω"
_GREEK_NAMES = ("alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron pi rho sigma "
                "tau upsilon phi chi psi omega").split()
# What to draw for a character the bundled font lacks, which has none of these. A character that
# NFKD takes apart (accents, ², µ, ½) is drawn as its parts, each through this table again.
STAND_INS = {
    **dict.fromkeys("—–−‐‑‒―", "-"), "⁄": "/", "×": "x", "′": "'", "″": '"', "„": '"', "•": "-",
    "→": "->", "←": "<-", "↔": "<->", "≈": "~", "≠": "!=", "≤": "<=", "≥": ">=",
    "∞": "infinity", "√": "sqrt", "∑": "sum", "∂": "d", "∇": "nabla", "∫": "integral", "∆": "Delta", "ħ": "hbar",
    "ß": "ss", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE", "ø": "o", "Ø": "O", "ð": "d", "þ": "th", "€": "EUR",
    **dict(zip(_GREEK, _GREEK_NAMES, strict=True)), "ς": "sigma",
    **{g.upper(): n.capitalize() for g, n in zip(_GREEK, _GREEK_NAMES, strict=True)},
}


def _stand_in(ch: str) -> str:
    """Plain characters for one the font lacks, "" if there are none."""
    if ch in STAND_INS:
        return STAND_INS[ch]
    parts = [c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c)]
    return "".join(STAND_INS.get(c, c) for c in parts if c in STAND_INS or ord(c) < 128)


def _drawable(text: str) -> str:
    """The text with each character the bundled font lacks swapped for its stand-in: FreeType would
    draw an empty box. One with no stand-in is left out, and logged. metadata.json keeps the title
    as it is."""
    font = _font(config.THUMBNAIL_FONT_PX[0])
    box = bytes(font.getmask(""))  # what the font draws for a character it lacks
    out, left_out = [], set()
    for ch in text:
        if ch.isspace() or bytes(font.getmask(ch)) != box:
            out.append(ch)
        elif plain := _stand_in(ch):
            out.append(plain)
        else:
            left_out.add(ch)
    if left_out:
        log(f"    thumbnail: no way to draw {' '.join(sorted(left_out))} in the title; left out of the thumbnail")
    return "".join(out)


def _wrap(text: str, font: ImageFont.FreeTypeFont, width: float) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and font.getbbox(trial)[2] > width:
            lines.append(line)
            line = word
        else:
            line = trial
    return [*lines, line] if line else lines


class Block(NamedTuple):
    """Lines of the title set in one font: the whole title, or its headline or its subtitle."""
    font: ImageFont.FreeTypeFont
    lines: list[str]
    opacity: float
    above: int  # the gap above the block, in pixels


def _line_height(px: int) -> int:
    return round(px * config.THUMBNAIL_LINE_SPACING)


def _column() -> float:
    """The width of the title column, in pixels."""
    return config.THUMBNAIL_COLUMN * config.THUMBNAIL_SIZE[0] - config.THUMBNAIL_MARGIN_PX


def _balanced(text: str, font: ImageFont.FreeTypeFont, width: float) -> list[str]:
    """The text in as many lines as it needs at this width, but in the narrowest column that
    still gives that many, so a line of one word isn't left under a long one."""
    lines = _wrap(text, font, width)
    low, high = 0.0, width
    for _ in range(12):
        middle = (low + high) / 2
        low, high = (low, middle) if len(_wrap(text, font, middle)) <= len(lines) else (middle, high)
    return _wrap(text, font, high)


def _set(text: str, max_lines: int, px: int, width: float, balance: bool = False) -> list[str] | None:
    """The text wrapped at this size (evenly, if `balance`), or None if it needs more than
    max_lines lines or a line is wider than the column."""
    font = _font(px)
    lines = _wrap(text, font, width)
    if len(lines) > max_lines or any(font.getbbox(x)[2] > width for x in lines):
        return None
    return _balanced(text, font, width) if balance and len(lines) > 1 else lines


def _blocks(parts: list[list], px: int, width: float) -> list[Block] | None:
    """The parts (text, max lines, size ratio, opacity) set with the first at `px` and each later
    one at its ratio of that, never under the smallest size; None if one doesn't fit or they are
    too tall for the frame together."""
    blocks = []
    for i, (text, max_lines, ratio, opacity) in enumerate(parts):
        size = max(round(px * ratio), config.THUMBNAIL_FONT_PX[1])
        lines = _set(text, max_lines, size, width, balance=len(parts) > 1)  # a headline reads better even
        if lines is None:
            return None
        blocks.append(Block(_font(size), lines, opacity, round(px * config.THUMBNAIL_SUBTITLE_GAP) if i else 0))
    tall = sum(b.above + len(b.lines) * _line_height(b.font.size) for b in blocks)
    return blocks if tall <= config.THUMBNAIL_SIZE[1] - 2 * config.THUMBNAIL_MARGIN_PX else None


def _shorter(text: str) -> str:
    """One word less and an ellipsis (one character less, for a single long word); a text that is
    only the ellipsis comes back as it is."""
    text = text.removesuffix("…")
    return (text.rsplit(" ", 1)[0] if " " in text else text[:-1]).rstrip(" ,:;-") + "…"


def _parts(title: str) -> list[list]:
    """The title's parts as [text, max lines, size ratio, opacity]: a headline and a subtitle when it
    has a ": " with text on both sides (split at the first, the colon not drawn), else the whole."""
    text = _drawable(" ".join(title.split()))
    head, colon, sub = text.partition(": ")
    if colon and head and sub:
        return [[head, config.THUMBNAIL_SPLIT_LINES[0], 1.0, 1.0],
                [sub, config.THUMBNAIL_SPLIT_LINES[1], config.THUMBNAIL_SUBTITLE_RATIO,
                 config.THUMBNAIL_SUBTITLE_OPACITY]]
    return [[text, config.THUMBNAIL_MAX_LINES, 1.0, 1.0]]


def fit_title(title: str) -> list[Block]:
    """The title as it is drawn: everything set at the largest size (a whole pixel at a time) at
    which it fits the title column, its line limits and the frame's height. A part that doesn't fit
    even at the smallest size is cut short with an ellipsis, and so is the tallest while the whole
    is too tall for the frame, so what is drawn always fits. ValueError if even "…" does not."""
    largest_px, smallest_px = config.THUMBNAIL_FONT_PX
    width = _column()
    parts = _parts(title)
    no_fit = ValueError("the title can't be made to fit the frame: check the THUMBNAIL_* settings")
    for part in parts:  # what can't fit even at the smallest size is cut first (one size to try, not all)
        while _set(part[0], part[1], smallest_px, width) is None:
            if _shorter(part[0]) == part[0]:
                raise no_fit
            part[0] = _shorter(part[0])
    while True:
        for px in range(largest_px, smallest_px - 1, -1):
            blocks = _blocks(parts, px, width)
            if blocks is not None:
                return blocks
        # all of it fits the column, but not the frame's height: the part with the most lines gives
        cuttable = [p for p in parts if _shorter(p[0]) != p[0]]
        if not cuttable:
            raise no_fit
        tallest = max(reversed(cuttable), key=lambda p: len(_set(p[0], p[1], smallest_px, width) or []))
        tallest[0] = _shorter(tallest[0])


def _scrim(size: tuple[int, int]) -> Image.Image:
    """A mask at full strength across the title column, then fading out by THUMBNAIL_SCRIM's share
    of the width: the letters sit on the strongest of it."""
    w, h = size
    _, opacity, reach = config.THUMBNAIL_SCRIM
    hold, end = config.THUMBNAIL_COLUMN * w, reach * w
    strip = Image.new("L", (w, 1))
    strip.putdata([round(255 * opacity * min(1.0, max(0.0, (end - x) / (end - hold)))) for x in range(w)])
    return strip.resize((w, h), Image.Resampling.NEAREST)


def compose(picture: Image.Image, title: str) -> Image.Image:
    """The picture cropped to the thumbnail's size, darkened on the left, with the title drawn on it."""
    size = config.THUMBNAIL_SIZE
    image = ImageOps.fit(picture.convert("RGB"), size, Image.Resampling.LANCZOS)
    image.paste(config.THUMBNAIL_SCRIM[0], mask=_scrim(size))
    blocks = fit_title(title)
    y = (size[1] - sum(b.above + len(b.lines) * _line_height(b.font.size) for b in blocks)) // 2
    letters = []  # each block's letters alone: a mask to blur into a shadow, then to paint through
    for block in blocks:
        y += block.above
        mask = Image.new("L", size, 0)
        draw = ImageDraw.Draw(mask)
        for i, line in enumerate(block.lines):
            draw.text((config.THUMBNAIL_MARGIN_PX, y + i * _line_height(block.font.size)), line,
                      font=block.font, fill=255)
        letters.append(mask)
        y += len(block.lines) * _line_height(block.font.size)
    blur, strength, drop = config.THUMBNAIL_SHADOW
    for block, mask in zip(blocks, letters, strict=True):  # every shadow before any letters
        shadow = mask.filter(ImageFilter.GaussianBlur(blur * block.font.size)).point(
            lambda v: min(255, round(v * strength)))
        image.paste((0, 0, 0), (0, round(block.font.size * drop)), shadow)  # a little below the letters
    for block, mask in zip(blocks, letters, strict=True):
        image.paste(config.THUMBNAIL_TITLE_RGB, mask=mask.point(lambda v, o=block.opacity: round(v * o)))
    return image


def encode(image: Image.Image) -> bytes:
    """A JPEG under THUMBNAIL_MAX_BYTES: the first quality that fits."""
    for quality in config.THUMBNAIL_JPEG_QUALITY:
        data = io.BytesIO()
        image.save(data, "JPEG", quality=quality, optimize=True)
        if data.tell() <= config.THUMBNAIL_MAX_BYTES:
            return data.getvalue()
    raise ValueError(f"over YouTube's {config.THUMBNAIL_MAX_BYTES} bytes even at JPEG quality {quality}")


def _frame(clip: Path) -> Image.Image:
    """The middle of a scene clip, as FFmpeg decodes it."""
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "frame.png"
        run(["ffmpeg", "-y", "-v", "error", "-ss", f"{video_duration(clip) / 2:.3f}", "-i", str(clip),
             "-frames:v", "1", str(out)])
        return Image.open(out).convert("RGB")  # read before the folder is removed


def _frame_clip(work: Path) -> Path:
    """The first still or Manim scene's clip (a Veo clip's first scene only when there is no other)."""
    scenes = load_json(work / "manifest.json")["scenes"]
    return work / next((m for m in scenes if m["kind"] in ("still", "manim")), scenes[0])["clip"]


def _stopped() -> bool:
    """True while the run is being stopped (a failure elsewhere, Ctrl-C): that is not for the
    thumbnail to absorb."""
    return ledger.stopping.is_set() or ledger.interrupted.is_set()


def _picture(work: Path, scene: dict | None, failures: Failures | None) -> Picture:
    """The image model's picture when it can be had, else a frame of the film, with why not. Nothing
    paid is bought twice, and a failure the model gave for good is not asked again
    (image_agent.render_still does both)."""
    reason = hint = None
    if scene is None:
        reason = "the film has no still or Veo scene to take a subject from"
    elif not image_agent.available():
        reason = "no GOOGLE_API_KEY"
    else:
        try:
            png = image_agent.render_still(scene, work / "visuals", failures)
            return Picture(png.name, "picture", lambda: Image.open(png))
        except Exception as e:  # noqa: BLE001
            if _stopped():
                raise
            if isinstance(e, ledger.BudgetExceeded):  # the film is made: an extra picture isn't worth stopping for
                reason, hint = f"budget spent ({redact(str(e))[:100]})", "a rerun with budget left draws it"
            elif isinstance(e, FailedEarlier):
                earlier = failures.earlier(image_agent.cache_path(scene, work / "visuals").stem) or {"error": "?"}
                reason, hint = f"image model failed earlier ({earlier['error'][:100]})", RETRY
            else:
                reason, hint = f"image model failed ({redact(str(e))[:100]})", RETRY if lasting(e) else AGAIN
    log(f"    thumbnail: using a frame of the film instead ({reason}{'; ' + hint if hint else ''})")
    clip = _frame_clip(work)
    return Picture(f"{clip.name}@middle", "frame", lambda: _frame(clip), reason, hint)


def make(work: Path, failures: Failures | None = None) -> Outcome:
    """Write thumbnail.jpg in `work` and name it in metadata.json (which must exist: the title is
    its). Rebuilt only when its title, picture, style or settings change, and a thumbnail that is
    current costs no request even if its picture was deleted. Never raises, except for the
    run being stopped or Ctrl-C: if it can't be made, the Outcome says why, and a thumbnail left from
    other inputs is removed, with metadata.json's mention of it, so that nothing is uploaded with
    an old title."""
    out = work / config.THUMBNAIL_FILE
    current = False  # thumbnail.jpg is for the inputs of this run
    try:
        title = load_json(work / "metadata.json")["title"]
        scene = picture_scene(load_json(work / "script.json")["scenes"], work / "visuals")
        if scene is not None and image_agent.available() and _current(work, title, scene):
            picture = None  # nothing to ask for, and nothing to draw
        else:
            picture = _picture(work, scene, failures)
        if picture is None or is_fresh(out, _key(title, picture.identity)):
            log(f"• thumbnail: up to date ({out})")
        else:
            data = encode(compose(picture.load(), title))
            with stamped_output(out, _key(title, picture.identity)) as tmp:
                tmp.write_bytes(data)
            log(f"• thumbnail: {out} ({'image model' if picture.source == 'picture' else 'frame'}, "
                f"{len(data) // 1000} KB)")
        current = True
        metadata.add_thumbnail(work)
        return Outcome(picture.source if picture else "picture", picture and picture.reason,
                       picture and picture.hint, out)
    except Exception as e:  # noqa: BLE001
        if _stopped():
            raise
        reason = f"{type(e).__name__}: {redact(str(e))[:200]}"
        log(f"• thumbnail: skipped ({reason})")
        if not current and out.exists():
            remove_output(out)
            log(f"    thumbnail: removed the old {out.name}, which was for another title or picture")
        if not current:
            metadata.drop_thumbnail(work)
        return Outcome("none", reason, "see the console log", None)
