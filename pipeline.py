"""Orchestrator. Every step caches to build/<slug>/, so a crash never re-bills finished work.

    python pipeline.py "What happens inside a neutron star" --minutes 15
    python pipeline.py "What happens inside a neutron star" --script-only   # review before spending
"""
import argparse
from pathlib import Path

import config
from agents import ai_video, critic, image_agent, manim_agent, music, script_agent, voice
from assembly import add_music, build_scene_clip, crossfade_concat
from utils import content_key, duration, load_json, save_json, slugify


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


def make_visual(scene: dict, target_s: float, out_dir: Path, allow_veo: bool) -> tuple[Path, str]:
    """Returns (path, kind). Falls back video -> still -> Manim, so one failed
    generation never stops the whole film."""
    vt = scene["visual_type"]
    if vt == "ai_video" and allow_veo:
        try:
            return ai_video.render_scene(scene, out_dir), "ai"
        except Exception as e:  # noqa: BLE001
            print(f"    Veo failed ({str(e)[:100]}); trying a still")
    if vt in ("ai_video", "still") and image_agent.available():
        try:
            return image_agent.render_still(scene, out_dir), "still"
        except Exception as e:  # noqa: BLE001
            print(f"    Imagen failed ({str(e)[:100]}); falling back to Manim")
    atmospheric = vt != "manim"
    return manim_agent.render_scene(scene, target_s, out_dir, atmospheric), "manim"


def render_film(script: dict, work: Path, allow_veo: bool, seed: str) -> Path:
    """Per-scene files live in audio/, visuals/ and clips/, named by a hash of their inputs.
    manifest.json maps each scene id to the files it used."""
    clips, manifest = [], []
    for scene in script["scenes"]:
        print(f"• scene {scene['id']}/{len(script['scenes'])}: {scene['concept']} [{scene['visual_type']}]")
        # Audio first: the narration length decides everything else
        narration = voice.narrate(scene["narration"], work / "audio")
        target = config.LEAD_IN_S + duration(narration) + config.TAIL_S
        visual, kind = make_visual(scene, target, work / "visuals", allow_veo)
        clip = build_scene_clip(visual, narration, work / "clips", kind, scene["id"])
        clips.append(clip)
        manifest.append({"id": scene["id"], "concept": scene["concept"], "kind": kind,
                         "audio": str(narration.relative_to(work)),
                         "visual": str(visual.relative_to(work)),
                         "clip": str(clip.relative_to(work))})
    save_json(work / "manifest.json", {"title": script["title"], "scenes": manifest})

    print("• assembling (crossfades)")
    narrated = crossfade_concat(clips, work / "narrated.mp4")
    total = duration(narrated)
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
    args = ap.parse_args()

    work = Path(config.BUILD_DIR) / slugify(args.topic)
    work.mkdir(parents=True, exist_ok=True)

    script = get_script(args.topic, args.minutes, work, args.skip_critic)
    if args.script_only:
        print(f"Script saved to {work / 'script.json'}. Edit it, then rerun without --script-only.")
        return

    allow_veo = ai_video.available() and not args.no_ai_video
    render_film(script, work, allow_veo, args.topic)


if __name__ == "__main__":
    main()
