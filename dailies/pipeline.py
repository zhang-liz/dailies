"""The review pipeline, shared by the review command and the watcher.

One clip in, one sidecar out: mechanical funnel, optional VLM screening,
verdict. Ranking is separate because it is a property of a shot, not of
a clip; rerank() rewrites rank_in_shot across a group of sidecars.
"""

import datetime
import glob
import os

from . import mechanical, rubric as rubric_mod, take, vlm

VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".avi"}


def is_video(path):
    return os.path.splitext(path)[1].lower() in VIDEO_EXTS


def find_clips(paths):
    out = []
    for p in paths:
        if os.path.isdir(p):
            for root, _, files in os.walk(p):
                out.extend(os.path.join(root, f) for f in files
                           if is_video(f))
        else:
            matched = glob.glob(p) or [p]
            out.extend(m for m in matched if is_video(m))
    return sorted(set(out))


def shot_for(clip, override=None):
    if override:
        return override
    parent = os.path.basename(os.path.dirname(os.path.abspath(clip)))
    return parent or None


def review_clip(clip, shot=None, force=False, vlm_endpoint=None,
                vlm_model="qwen3-vl", rubric_path=None, api_key=None,
                samples=1, calibration=None, strong_endpoint=None,
                strong_model=None, prices=None):
    """Run the funnel on one clip and save its sidecar. Returns
    (take, cached): cached is True when the content hash matched an
    existing review and nothing was recomputed."""
    t = take.load(clip)
    take_id = take.hash_file(clip)
    cached = bool(not force and t.get("take_id") == take_id
                  and t.get("review"))
    if not cached:
        t["take_id"] = take_id
        if not t.get("created"):
            t["created"] = (datetime.datetime.now(datetime.timezone.utc)
                            .strftime("%Y-%m-%dT%H:%M:%SZ"))
        t["review"] = mechanical.review(clip)
        info = t["review"]["mechanical"]["probe"]
        t["output"].update({k: info[k] for k in
                            ("fps", "frames", "width", "height")
                            if info.get(k) is not None})
    if not t.get("shot"):
        t["shot"] = shot_for(clip, shot)

    r = t["review"]
    if (vlm_endpoint and r["verdict"] != "kill"
            and (force or not r.get("vlm"))):
        rules = rubric_mod.load(rubric_path)
        r["vlm"] = vlm.screen(clip, t, rules, vlm_endpoint, vlm_model,
                              api_key=api_key, samples=samples)
        if strong_endpoint and (r["vlm"].get("uncertain")
                                or r["vlm"].get("unparsed")):
            r["vlm"] = vlm.escalate(clip, t, rules, r["vlm"],
                                    strong_endpoint,
                                    strong_model or vlm_model,
                                    api_key=api_key)
        if calibration is not None:
            # Calibrated mode: the conformal threshold replaces fail_at,
            # and the false-kill guarantee replaces judgment calls.
            from . import calibrate as calibrate_mod
            s = calibrate_mod.kill_score(t)
            lam = calibration.get("lambda")
            if lam is not None and s > lam:
                r["mechanical"]["kill_reasons"].append(
                    "calibrated kill score %.2f > %.2f "
                    "(false-kill rate <= %s)" % (
                        s, lam, calibration.get("alpha")))
                r["verdict"] = "kill"
        else:
            reasons = vlm.kill_reasons(r["vlm"], rules)
            if reasons:
                r["mechanical"]["kill_reasons"].extend(reasons)
                r["verdict"] = "kill"
        cached = False

    if prices is not None:
        # Recomputed even on cached reviews, so a price file supplied
        # after the fact prices the existing usage without re-judging.
        from . import cost
        r["cost"] = cost.block(t, prices)

    take.save(clip, t)
    return t, cached


def rerank(clips, calibration=None):
    """Recompute rank_in_shot across the sidecars of these clips and save
    the ones whose rank moved. Returns the loaded takes, clip-keyed.
    With fitted weights in the calibration, the user's own learned kill
    probability leads the ordering."""
    takes = {}
    for clip in clips:
        if os.path.exists(take.sidecar_path(clip)):
            t = take.load(clip)
            if t.get("review"):
                takes[clip] = t

    by_shot = {}
    for clip, t in takes.items():
        by_shot.setdefault(t.get("shot"), []).append((clip, t))

    def key(item):
        r = item[1]["review"]
        m = r["mechanical"]
        defects = (len(m["black_frames"]) + len(m["freeze"])
                   + len(m["scene_cuts"]))
        severity = sum(d["severity"] for d in
                       (r.get("vlm") or {}).get("defects", []))
        if calibration is not None:
            from . import calibrate as calibrate_mod
            learned = calibrate_mod.rank_score(item[1], calibration)
        else:
            learned = None
        return (r["verdict"] == "kill",
                learned if learned is not None else 0.0,
                severity, defects,
                m["flicker_score"] or 0,
                -(m.get("motion_smoothness") or 0))

    for group in by_shot.values():
        for i, (clip, t) in enumerate(sorted(group, key=key), start=1):
            if t["review"].get("rank_in_shot") != i:
                t["review"]["rank_in_shot"] = i
                take.save(clip, t)
    return takes
