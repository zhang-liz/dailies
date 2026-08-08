"""dailies CLI. Every command has --json for agent consumption."""

import argparse
import json
import os
import sys

from . import __version__, mechanical, pipeline, vlm


def _vlm_kwargs(args):
    return {
        "vlm_endpoint": args.vlm,
        "vlm_model": args.vlm_model,
        "rubric_path": args.rubric,
        "api_key": os.environ.get("DAILIES_VLM_KEY"),
    }


def cmd_review(args):
    clips = pipeline.find_clips(args.paths)
    if not clips:
        print("no clips found", file=sys.stderr)
        return 1
    for clip in clips:
        pipeline.review_clip(clip, shot=args.shot, force=args.force,
                             **_vlm_kwargs(args))
    takes = list(pipeline.rerank(clips).values())

    kills = [t for t in takes if t["review"]["verdict"] == "kill"]
    if args.json:
        json.dump({"reviewed": len(takes), "killed": len(kills),
                   "takes": takes}, sys.stdout, indent=2)
        print()
    else:
        print("reviewed %d takes, killed %d" % (len(takes), len(kills)))
        for t in sorted(takes, key=lambda t: (t["shot"] or "",
                        t["review"]["rank_in_shot"] or 0)):
            r = t["review"]
            reasons = "; ".join(r["mechanical"]["kill_reasons"])
            print("  %-8s #%s  %-40s %s" % (
                r["verdict"], r["rank_in_shot"], t["output"]["file"],
                reasons))
    return 0


def cmd_report(args):
    from . import report
    out = report.build(args.dir, args.output)
    print(json.dumps({"report": out}) if args.json else out)
    return 0


def cmd_watch(args):
    from . import watch
    return watch.run(args)


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="dailies",
        description="Triage AI-generated video takes: kill the dead, "
                    "rank the survivors.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)

    def vlm_flags(sp):
        sp.add_argument("--vlm", metavar="URL",
                        help="OpenAI-compatible endpoint base, e.g. "
                             "http://localhost:8000/v1; enables stage 2. "
                             "API key read from DAILIES_VLM_KEY if set")
        sp.add_argument("--vlm-model", default="qwen3-vl",
                        help="model name passed to the endpoint")
        sp.add_argument("--rubric", metavar="FILE",
                        help="rubric file (.json, or .yaml with PyYAML); "
                             "default: built-in rules")

    rv = sub.add_parser("review", help="run the funnel on clips")
    rv.add_argument("paths", nargs="+", help="clips, globs, or directories")
    rv.add_argument("--shot", help="tag all reviewed takes with this shot id")
    rv.add_argument("--force", action="store_true",
                    help="re-review even when the cached take_id matches")
    vlm_flags(rv)
    rv.add_argument("--json", action="store_true")
    rv.set_defaults(func=cmd_review)

    rp = sub.add_parser("report", help="write the static HTML morning report")
    rp.add_argument("dir", nargs="?", default=".",
                    help="directory to scan for take.json sidecars")
    rp.add_argument("-o", "--output", default="dailies-report.html")
    rp.add_argument("--json", action="store_true")
    rp.set_defaults(func=cmd_report)

    w = sub.add_parser(
        "watch", help="review takes as they land in a directory")
    w.add_argument("dir", help="directory to watch, recursively")
    w.add_argument("--interval", type=float, default=5.0,
                   help="poll interval in seconds (default 5)")
    w.add_argument("--shot", help="tag new takes with this shot id "
                                  "(default: parent directory name)")
    w.add_argument("--report", metavar="FILE",
                   help="rebuild this HTML report after every review")
    vlm_flags(w)
    w.add_argument("--json", action="store_true",
                   help="emit one JSON line per reviewed take")
    w.set_defaults(func=cmd_watch)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except (mechanical.FfmpegMissing, vlm.VlmError, RuntimeError) as e:
        print("error: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
