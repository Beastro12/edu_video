"""Orchestrator. Every step caches to build/<slug>/, so a crash never re-bills finished work.

    python pipeline.py "What happens inside a neutron star" --minutes 15
    python pipeline.py "What happens inside a neutron star" --script-only   # review before spending
"""
import argparse
import sys
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from pathlib import Path

import config
import failures as failure_log
import ledger
import metadata
import qa
from agents import ai_video, critic, image_agent, manim_agent, music, script_agent, voice
from assembly import add_music, build_scene_clip, crossfade_concat
from failures import FailedEarlier, Failures
from utils import content_key, duration, load_json, log, save_json, slugify, video_duration


def _chapter_sources(work: Path) -> str | None:
    """Hash of outline.json and every chapter file it lists; None if any is missing."""
    outline_path = work / "outline.json"
    if not outline_path.exists():
        return None
    outline = load_json(outline_path)
    parts = [outline]
    for ch in outline["chapters"]:
        ch_path = work / f"chapter_{ch['number']:02d}.json"
        if not ch_path.exists():
            return None
        parts.append(load_json(ch_path))
    return content_key("chapters", parts)


def get_script(topic: str, minutes: float, work: Path, skip_critic: bool) -> dict:
    """script.json is assembled from outline.json + chapter_XX.json. Both can be edited:
    hand edits to script.json are kept; edits to a chapter file rebuild script.json;
    edits to both refuse to guess which one should win."""
    final, meta_path = work / "script.json", work / "script.meta.json"
    if final.exists():
        script = load_json(final)
        sources = _chapter_sources(work)
        meta = load_json(meta_path) if meta_path.exists() else None
        if sources is None or meta is None or meta["chapters"] == sources:
            if meta is None:  # written before this check existed: adopt it as the baseline
                save_json(meta_path, {"chapters": sources, "script": content_key("script", script)})
            print("• script: using cached script.json (edit it freely)")
            return script
        if meta["script"] != content_key("script", script):
            raise RuntimeError(
                f"Both {final} and a chapter file were edited since the script was assembled. "
                "Keep one: delete script.json to rebuild it from the chapters, "
                "or undo the chapter edit to keep your script.json edits.")
        print("• script: chapter files changed; rebuilding script.json from them")

    outline_path = work / "outline.json"
    if outline_path.exists():
        outline = load_json(outline_path)
    else:
        print("• outline agent: planning chapters")
        outline = script_agent.plan_outline(topic, minutes)
        save_json(outline_path, outline)
    for ch in outline["chapters"]:
        print(f"    {ch['number']}. {ch['title']} ({ch['minutes']} min)")

    scenes, tail = [], []
    for ch in outline["chapters"]:
        ch_path = work / f"chapter_{ch['number']:02d}.json"
        if ch_path.exists():
            ch_scenes = load_json(ch_path)
        else:
            print(f"• writer: chapter {ch['number']}")
            ch_scenes = script_agent.write_chapter(outline, ch, tail)
            if not skip_critic:
                for _ in range(config.MAX_CRITIC_ROUNDS):
                    result = critic.review_chapter(outline, ch, ch_scenes)
                    for issue in result["issues"]:
                        print(f"    critic: {issue}")
                    ch_scenes = result["revised_scenes"]
                    if result["approved"]:
                        break
            save_json(ch_path, ch_scenes)
        for s in ch_scenes:
            s["chapter"] = ch["number"]
        scenes += ch_scenes
        tail = [s["narration"] for s in ch_scenes[-2:]]

    for i, s in enumerate(scenes, start=1):
        s["id"] = i
    script = {"title": outline["title"], "scenes": scenes}
    save_json(final, script)
    save_json(meta_path, {"chapters": _chapter_sources(work), "script": content_key("script", script)})
    words = sum(len(s["narration"].split()) for s in scenes)
    print(f"• script: {len(scenes)} scenes, {words} words (~{words / config.WORDS_PER_MIN:.0f} min)")
    return script


def make_visual(scene: dict, target_s: float, out_dir: Path, allow_veo: bool,
                failures: Failures | None = None) -> tuple[Path, str]:
    """Returns (path, kind). Falls back video -> still -> Manim, so one failed
    generation never stops the whole film. Failures are recorded in `failures` and, on
    later runs, skipped straight to the fallback (P1-7)."""
    vt = scene["visual_type"]
    if vt == "ai_video" and allow_veo:
        try:
            return ai_video.render_scene(scene, out_dir, failures=failures), "ai"
        except ledger.BudgetExceeded:
            raise  # out of money is not a failed generation: stop the run
        except ai_video.DailyCapReached as e:
            log(f"    scene {scene['id']}: Veo daily cap reached ({e}); using a still")
        except FailedEarlier as e:
            log(f"    scene {scene['id']}: Veo {e}; using a still")
        except Exception as e:  # noqa: BLE001
            log(f"    scene {scene['id']}: Veo failed ({str(e)[:100]}); trying a still")
    if vt in ("ai_video", "still") and image_agent.available():
        try:
            return image_agent.reviewed_still(scene, out_dir, failures), "still"
        except ledger.BudgetExceeded:
            raise
        except image_agent.StillRejected as e:
            log(f"    scene {scene['id']}: still failed review ({str(e)[:120]}); falling back to Manim")
        except FailedEarlier as e:
            log(f"    scene {scene['id']}: image model {e}; falling back to Manim")
        except Exception as e:  # noqa: BLE001
            log(f"    scene {scene['id']}: image model failed ({str(e)[:100]}); falling back to Manim")
    atmospheric = vt != "manim"
    return manim_agent.render_scene(scene, target_s, out_dir, atmospheric), "manim"


def _planned_scenes(minutes: float, work: Path) -> tuple[list[dict], list[tuple[str, int, int]]]:
    """Scenes this run will voice and illustrate, and the script-stage Claude calls still to
    make as (label, input_tokens, output_tokens). Real text where it exists; a synthetic
    scene mix following D3 where it doesn't."""
    if (work / "script.json").exists():
        return load_json(work / "script.json")["scenes"], []
    calls = []
    outline_path = work / "outline.json"
    if outline_path.exists():
        chapters = load_json(outline_path)["chapters"]
    else:
        chapters = [{"number": i + 1, "minutes": minutes / config.EST_CHAPTERS}
                    for i in range(config.EST_CHAPTERS)]
        calls.append(("outline", config.EST_PROMPT_TOKENS, config.EST_OUTLINE_TOKENS_PER_CHAPTER * config.EST_CHAPTERS))
    scenes = []
    for ch in chapters:
        ch_path = work / f"chapter_{ch['number']:02d}.json"
        if ch_path.exists():
            scenes += load_json(ch_path)
            continue
        words = ch["minutes"] * config.WORDS_PER_MIN
        out_tokens = int(words * config.EST_SCRIPT_TOKENS_PER_WORD)
        calls.append(("chapter", config.EST_PROMPT_TOKENS, out_tokens))
        calls.append(("review", config.EST_PROMPT_TOKENS + out_tokens, out_tokens))
        n = max(1, round(words / config.EST_WORDS_PER_SCENE))
        n_manim = round(config.EST_MANIM_SHARE * (n - 1))
        types = ["ai_video"] + ["manim"] * n_manim + ["still"] * (n - 1 - n_manim)
        chars = int(words * config.EST_CHARS_PER_WORD / n)
        scenes += [{"concept": "planned", "visual_type": vt, "narration": "x" * chars,
                    "visual_description": f"planned {ch['number']}.{i}"} for i, vt in enumerate(types)]
    return scenes, calls


def _still_rejected_for_good(scene: dict, visuals: Path) -> bool:
    return image_agent.is_decided(scene, visuals) and image_agent.review_log(scene, visuals)["accepted"] is None


def estimate_cost(minutes: float, work: Path, allow_veo: bool, skip_critic: bool,
                  worst_case: bool = False, retry_failed: bool = False) -> dict[str, float]:
    """Projected spend in EUR for what this run would still have to buy. Cached work costs
    nothing, and a generation that failed before costs its fallback (P1-7). Calls no paid API.
    Typical: one critic round, one Manim attempt; worst case: every critic round and every
    Manim retry."""
    scenes, calls = _planned_scenes(minutes, work)
    failed = Failures(failure_log.path(work), retry=retry_failed).earlier
    model = config.CLAUDE_MODEL
    reviews = 0 if skip_critic else (config.MAX_CRITIC_ROUNDS if worst_case else 1)
    manim_tries = config.MAX_MANIM_ATTEMPTS + 1 if worst_case else 1  # worst: every fix round, then a retime
    est = {"claude": sum(ledger.claude_eur(model, i, o) * (reviews if label == "review" else 1)
                         for label, i, o in calls),
           "elevenlabs": 0.0, "images": 0.0, "veo": 0.0}
    veo_left = max(0, config.VEO_MAX_PER_DAY - ledger.veo_clips_today())
    for scene in scenes:
        audio = voice.cache_path(scene["narration"], work / "audio")
        if not audio.exists():
            est["elevenlabs"] += ledger.tts_eur(len(scene["narration"]))
        vt = scene["visual_type"]
        veo = ai_video.cache_path(scene, work / "visuals")
        veo_cached = vt == "ai_video" and allow_veo and veo.exists()
        if vt == "ai_video" and allow_veo and (veo_cached or (veo_left > 0 and not failed(veo.stem))):
            if not veo_cached:
                est["veo"] += ledger.video_eur(config.VEO_CLIP_S)
                veo_left -= 1
        elif (vt in ("ai_video", "still") and image_agent.available()
              and not _still_rejected_for_good(scene, work / "visuals")
              and not failed(image_agent.next_still(scene, work / "visuals").stem)):
            if not image_agent.is_decided(scene, work / "visuals"):
                tries = 1 + config.STILL_REVIEW_RETRIES if worst_case else 1
                est["images"] += tries * ledger.image_eur()
                est["claude"] += tries * ledger.claude_eur(model, *config.EST_STILL_REVIEW_TOKENS)
                if worst_case:  # every attempt rejected: the scene then needs Manim as well
                    est["claude"] += manim_tries * ledger.claude_eur(model, *config.EST_MANIM_TOKENS)
        else:
            cached = audio.exists() and manim_agent.cache_path(
                scene, config.LEAD_IN_S + duration(audio) + config.TAIL_S, work / "visuals",
                atmospheric=vt != "manim").exists()
            if not cached:
                est["claude"] += manim_tries * ledger.claude_eur(model, *config.EST_MANIM_TOKENS)
    return est


def print_estimate(topic: str, est: dict[str, float], worst: dict[str, float]) -> None:
    total, worst_total, spent = sum(est.values()), sum(worst.values()), ledger.spent_eur()
    print(f'Projected cost for "{topic}" (estimate; prices marked "verify" in config.py; '
          "cached work excluded):")
    for name, eur in est.items():
        print(f"  {name:<11} €{eur:.2f}")
    print(f"  {'total':<11} €{total:.2f}   (worst case, every critic round and Manim retry: €{worst_total:.2f})")
    veo_left = max(0, config.VEO_MAX_PER_DAY - ledger.veo_clips_today())
    print(f"  Veo clips left today: {veo_left} of {config.VEO_MAX_PER_DAY}")
    verdict = "fits" if spent + worst_total <= config.BUDGET_EUR else "may EXCEED"
    print(f"Spent so far €{spent:.2f} of the €{config.BUDGET_EUR:.2f} budget: this run {verdict} it.")
    if not image_agent.available():
        print("  (no GOOGLE_API_KEY: stills and Veo scenes are counted as Manim)")

def in_parallel(fn, items: list) -> list:
    """fn over items on WORKERS threads, results in the items' order. The first failure (say
    BudgetExceeded) stops the run at once: calls not yet started are cancelled and, until the
    calls in flight have finished, no new paid call may be reserved (ledger.stopping)."""
    if config.WORKERS <= 1 or len(items) <= 1:
        return [fn(x) for x in items]
    pool = ThreadPoolExecutor(config.WORKERS)
    futures = [pool.submit(fn, x) for x in items]
    try:
        done, _ = wait(futures, return_when=FIRST_EXCEPTION)
        failed = next((f for f in futures if f in done and f.exception() is not None), None)
        if failed is not None:
            raise failed.exception()
        return [f.result() for f in futures]
    except KeyboardInterrupt:
        ledger.interrupted.set()  # even a Veo job in flight gives up (it's recorded as billed)
        ledger.stopping.set()
        raise
    except BaseException:  # jobs in flight finish and are cached; nothing new starts
        ledger.stopping.set()
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
        ledger.stopping.clear()  # nothing is in flight any more: the next run starts clean
        ledger.interrupted.clear()


def generate_assets(script: dict, work: Path, allow_veo: bool, retry_failed: bool = False) -> list[dict]:
    """Everything paid for, per scene: narration first (its length sets the scene's), then the
    visual. Independent scenes run concurrently (P1-5). Visuals that failed on an earlier run
    go straight to their fallback unless `retry_failed` (P1-7)."""
    scenes = script["scenes"]
    failures = Failures(failure_log.path(work), retry=retry_failed)
    print(f"• narration: {len(scenes)} scenes, {config.WORKERS} at a time")
    audio = in_parallel(lambda s: voice.narrate(s["narration"], work / "audio"), scenes)
    targets = [config.LEAD_IN_S + duration(a) + config.TAIL_S for a in audio]
    print("• visuals")
    visuals = in_parallel(lambda i: make_visual(scenes[i], targets[i], work / "visuals", allow_veo, failures),
                          list(range(len(scenes))))
    return [{"scene": s, "audio": a, "visual": v, "kind": k}
            for s, a, (v, k) in zip(scenes, audio, visuals, strict=True)]


def render_film(script: dict, work: Path, allow_veo: bool, seed: str, retry_failed: bool = False) -> Path:
    """Per-scene files live in audio/, visuals/ and clips/, named by a hash of their inputs.
    manifest.json maps each scene id to the files it used."""
    clips, manifest = [], []
    for asset in generate_assets(script, work, allow_veo, retry_failed):
        scene, narration, visual, kind = asset["scene"], asset["audio"], asset["visual"], asset["kind"]
        print(f"• scene {scene['id']}/{len(script['scenes'])}: {scene['concept']} [{kind}]")
        clip = build_scene_clip(visual, narration, work / "clips", kind, scene["id"])
        clips.append(clip)
        review = image_agent.review_path(scene, work / "visuals")
        manifest.append({"id": scene["id"], "concept": scene["concept"], "kind": kind,
                         "audio": str(narration.relative_to(work)),
                         "visual": str(visual.relative_to(work)),
                         "clip": str(clip.relative_to(work)),
                         "still_review": str(review.relative_to(work)) if kind != "ai" and review.exists() else None})
    save_json(work / "manifest.json", {"title": script["title"], "scenes": manifest})

    print("• assembling (crossfades)")
    narrated = crossfade_concat(clips, work / "narrated.mp4")
    total = video_duration(narrated)  # the container also counts AAC's end padding
    tracks = music.list_tracks()
    if tracks:
        bed = music.build_bed(tracks, total, work / "music_bed.wav", seed)
    else:
        print("    no files in music/ — using a synthetic placeholder pad (don't publish with it)")
        bed = music.build_bed([music.placeholder_pad(work / "placeholder_pad.wav")],
                              total, work / "music_bed.wav", seed)
    final = add_music(narrated, bed, work / f"{slugify(script['title'])}.mp4")
    print(f"Done: {final}  ({total / 60:.1f} min)")
    return final


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("topic")
    ap.add_argument("--minutes", type=float, default=config.DEFAULT_MINUTES)
    ap.add_argument("--script-only", action="store_true", help="stop after the reviewed script")
    ap.add_argument("--no-ai-video", action="store_true", help="skip Veo; use stills instead")
    ap.add_argument("--skip-critic", action="store_true")
    ap.add_argument("--estimate", action="store_true", help="print the projected cost and exit; calls nothing")
    ap.add_argument("--retry-failed", action="store_true",
                    help="ask Veo / the image model again for scenes whose generation failed on an earlier run")
    args = ap.parse_args()

    work = Path(config.BUILD_DIR) / slugify(args.topic)
    allow_veo = ai_video.available() and not args.no_ai_video
    if args.estimate:
        print_estimate(args.topic, estimate_cost(args.minutes, work, allow_veo, args.skip_critic,
                                                 retry_failed=args.retry_failed),
                       estimate_cost(args.minutes, work, allow_veo, args.skip_critic, worst_case=True,
                                     retry_failed=args.retry_failed))
        return
    work.mkdir(parents=True, exist_ok=True)

    script = get_script(args.topic, args.minutes, work, args.skip_critic)
    if args.script_only:
        print(f"Script saved to {work / 'script.json'}. Edit it, then rerun without --script-only.")
        return

    final = render_film(script, work, allow_veo, args.topic, args.retry_failed)
    metadata.write_metadata(work)  # metadata.json + subtitles.srt for YouTube
    print(f"• YouTube metadata: {work / 'metadata.json'}, {work / 'subtitles.srt'}")
    report = qa.run_qa(final)  # writes qa.json next to the film
    qa.print_report(report)
    if not report["passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
