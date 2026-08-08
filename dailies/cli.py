"""dailies CLI. Every command has --json for agent consumption."""

import argparse
import datetime
import glob
import json
import os
import sys

from . import __version__, mechanical, take

VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".avi"}


def _clips(paths):
    out = []
    for p in paths:
        if os.path.isdir(p):
            for root, _, files in os.walk(p):
                out.extend(
                    os.path.join(root, f) for f in files
                    if os.path.splitext(f)[1].lower() in VIDEO_EXTS)
        else:
            matched = glob.glob(p) or [p]
            out.extend(m for m in matched
                       if os.path.splitext(m)[1].lower() in VIDEO_EXTS)
    return sorted(set(out))


def _shot_for(clip, override):
    if override:
        return override
    parent = os.path.basename(os.path.dirname(os.path.abspath(clip)))
    return parent or None


def _rank(takes):
    """Rank surviving takes within each shot: fewest defects, then least
    flicker. Kills rank last."""
    by_shot = {}
    for t in takes:
        by_shot.setdefault(t["shot"], []).append(t)
    for group in by_shot.values():
        def key(t):
            r = t["review"]
            m = r["mechanical"]
            defects = (len(m["black_frames"]) + len(m["freeze"])
                       + len(m["scene_cuts"]))
            return (r["verdict"] == "kill", defects, m["flicker_score"] or 0)
        for i, t in enumerate(sorted(group, key=key), start=1):
            t["review"]["rank_in_shot"] = i


def cmd_review(args):
    clips = _clips(args.paths)
    if not clips:
        print("no clips found", file=sys.stderr)
        return 1
    results = []
    for clip in clips:
        t = take.load(clip)
        take_id = take.hash_file(clip)
        cached = (not args.force and t.get("take_id") == take_id
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
            t["shot"] = _shot_for(clip, args.shot)
        t["_clip"] = clip
        t["_cached"] = bool(cached)
        results.append(t)

    _rank(results)
    for t in results:
        clip = t.pop("_clip")
        t.pop("_cached")
        take.save(clip, t)

    kills = [t for t in results if t["review"]["verdict"] == "kill"]
    if args.json:
        json.dump({"reviewed": len(results), "killed": len(kills),
                   "takes": results}, sys.stdout, indent=2)
        print()
    else:
        print("reviewed %d takes, killed %d" % (len(results), len(kills)))
        for t in sorted(results,
                        key=lambda t: (t["shot"] or "",
                                       t["review"]["rank_in_shot"] or 0)):
            r = t["review"]
            reasons = "; ".join(r["mechanical"]["kill_reasons"])
            print("  %-8s #%s  %-40s %s" % (
                r["verdict"], r["rank_in_shot"], t["output"]["file"], reasons))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="dailies",
        description="Triage AI-generated video takes: kill the dead, "
                    "rank the survivors.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)

    rv = sub.add_parser("review", help="run the mechanical funnel on clips")
    rv.add_argument("paths", nargs="+", help="clips, globs, or directories")
    rv.add_argument("--shot", help="tag all reviewed takes with this shot id")
    rv.add_argument("--force", action="store_true",
                    help="re-review even when the cached take_id matches")
    rv.add_argument("--json", action="store_true")
    rv.set_defaults(func=cmd_review)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except mechanical.FfmpegMissing as e:
        print("error: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
