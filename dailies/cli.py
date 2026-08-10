"""dailies CLI. Every command has --json for agent consumption."""

import argparse
import json
import os
import sys

from . import __version__, mechanical, pipeline, vlm


def _vlm_kwargs(args):
    kwargs = {
        "vlm_endpoint": args.vlm,
        "vlm_model": args.vlm_model,
        "rubric_path": args.rubric,
        "api_key": os.environ.get("DAILIES_VLM_KEY"),
        "samples": args.samples,
        "strong_endpoint": args.vlm_strong,
        "strong_model": args.vlm_strong_model,
    }
    if getattr(args, "calibration", None):
        from . import calibrate
        kwargs["calibration"] = calibrate.load(args.calibration)
    return kwargs


def cmd_review(args):
    clips = pipeline.find_clips(args.paths)
    if not clips:
        print("no clips found", file=sys.stderr)
        return 1
    kwargs = _vlm_kwargs(args)
    for clip in clips:
        pipeline.review_clip(clip, shot=args.shot, force=args.force,
                             **kwargs)
    takes = list(pipeline.rerank(
        clips, calibration=kwargs.get("calibration")).values())

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


def cmd_gold(args):
    from . import gold
    if args.gold_command == "add":
        labeled = gold.add_paths(args.paths, args.label)
        if not labeled:
            print("no clips found", file=sys.stderr)
            return 1
        if args.json:
            json.dump({"labeled": [c for c, _ in labeled],
                       "label": args.label}, sys.stdout, indent=2)
            print()
        else:
            for clip, _ in labeled:
                print("%s  %s" % (args.label, clip))
        return 0
    takes = gold.collect(args.dir)
    if args.json:
        json.dump({"gold": [{"clip": c, "label": t["gold"]["label"]}
                            for c, t in takes]}, sys.stdout, indent=2)
        print()
    else:
        for clip, t in takes:
            print("%-5s %s" % (t["gold"]["label"], clip))
        print("%d labeled takes" % len(takes))
    return 0


def cmd_calibrate(args):
    from . import calibrate
    cal = calibrate.calibrate(args.dir, alpha=args.alpha)
    calibrate.save(cal, args.output)
    if args.json:
        print(json.dumps(cal, indent=2))
        return 0
    if cal["lambda"] is None:
        print("not enough gold-pass takes: %d labeled, %d needed for "
              "alpha=%s" % (cal["n_pass"], cal["needed"], cal["alpha"]))
        print("wrote %s (threshold disabled until recalibrated)"
              % args.output)
        return 1
    print("kill threshold %.2f: false-kill rate <= %s on %d gold-pass "
          "takes" % (cal["lambda"], cal["alpha"], cal["n_pass"]))
    if "kill_recall" in cal:
        print("catches %.0f%% of your %d gold kills"
              % (100 * cal["kill_recall"], cal["n_kill"]))
    print("wrote %s; use it with review --calibration" % args.output)
    return 0


def cmd_fit(args):
    from . import calibrate
    fitted = calibrate.fit(args.dir)
    cal = {}
    if os.path.exists(args.output):
        cal = calibrate.load(args.output)
    cal.update(fitted)
    calibrate.save(cal, args.output)
    if args.json:
        print(json.dumps(cal, indent=2))
        return 0
    print("fitted %d rule weights on %d takes, training accuracy %.0f%%"
          % (len(fitted["weights"]), fitted["n_fit"],
             100 * fitted["fit_accuracy"]))
    top = sorted(fitted["weights"].items(), key=lambda kv: -abs(kv[1]))
    for rule, w in top[:5]:
        print("  %-24s %+0.3f" % (rule, w))
    print("wrote %s; review --calibration ranks by your own taste now"
          % args.output)
    return 0


def cmd_judge_check(args):
    from . import judgecheck, rubric
    if not args.vlm:
        print("judge-check needs --vlm", file=sys.stderr)
        return 2
    kwargs = _vlm_kwargs(args)
    kwargs.pop("rubric_path", None)
    record = judgecheck.run(args.dir, rubric.load(args.rubric),
                            history_path=args.history, **kwargs)
    previous = record.pop("previous", None)
    if args.json:
        print(json.dumps(record, indent=2))
    else:
        print("judge %s on %d gold takes: agreement %.0f%%, "
              "kappa %.2f" % (record["engine"], record["n"],
                              100 * record["agreement"],
                              record["kappa"]))
        print("  %d false kills, %d missed kills"
              % (record["false_kills"], record["missed_kills"]))
        if previous:
            print("  last run (%s, %s): agreement %.0f%%, kappa %.2f"
                  % (previous["engine"], previous["created"],
                     100 * previous["agreement"], previous["kappa"]))
    if args.fail_below is not None and record["kappa"] < args.fail_below:
        print("kappa %.2f below --fail-below %s" % (
            record["kappa"], args.fail_below), file=sys.stderr)
        return 1
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
        sp.add_argument("--samples", type=int, default=1, metavar="K",
                        help="ask the judge K times per rule; "
                             "disagreement becomes confidence, and an "
                             "unagreed defect cannot kill (default 1)")
        sp.add_argument("--vlm-strong", metavar="URL",
                        help="stronger endpoint for the cascade: rules "
                             "the first judge was unsure about are "
                             "re-judged here, and only those")
        sp.add_argument("--vlm-strong-model",
                        help="model for --vlm-strong (default: "
                             "--vlm-model)")
        sp.add_argument("--calibration", metavar="FILE",
                        help="calibration from `dailies calibrate`; "
                             "its conformal threshold replaces the "
                             "rubric's fail_at kills")

    rv = sub.add_parser("review", help="run the funnel on clips")
    rv.add_argument("paths", nargs="+", help="clips, globs, or directories")
    rv.add_argument("--shot", help="tag all reviewed takes with this shot id")
    rv.add_argument("--force", action="store_true",
                    help="re-review even when the cached take_id matches")
    vlm_flags(rv)
    rv.add_argument("--json", action="store_true")
    rv.set_defaults(func=cmd_review)

    g = sub.add_parser("gold", help="record human pass/kill verdicts")
    gsub = g.add_subparsers(dest="gold_command", required=True)
    ga = gsub.add_parser("add", help="label takes with your verdict")
    ga.add_argument("paths", nargs="+", help="clips, globs, or directories")
    ga.add_argument("--label", required=True, choices=["pass", "kill"])
    ga.add_argument("--json", action="store_true")
    gl = gsub.add_parser("list", help="show the gold set")
    gl.add_argument("dir", nargs="?", default=".")
    gl.add_argument("--json", action="store_true")
    g.set_defaults(func=cmd_gold)

    cal = sub.add_parser(
        "calibrate",
        help="set the kill threshold from your gold labels, with a "
             "conformal false-kill guarantee")
    cal.add_argument("dir", nargs="?", default=".",
                     help="directory of gold-labeled, reviewed takes")
    cal.add_argument("--alpha", type=float, default=0.05,
                     help="max false-kill rate to guarantee (default 0.05)")
    cal.add_argument("-o", "--output", default="dailies-calibration.json")
    cal.add_argument("--json", action="store_true")
    cal.set_defaults(func=cmd_calibrate)

    ft = sub.add_parser(
        "fit", help="fit per-rule weights to your own gold verdicts")
    ft.add_argument("dir", nargs="?", default=".",
                    help="directory of gold-labeled, reviewed takes")
    ft.add_argument("-o", "--output", default="dailies-calibration.json")
    ft.add_argument("--json", action="store_true")
    ft.set_defaults(func=cmd_fit)

    jc = sub.add_parser(
        "judge-check",
        help="re-judge the gold set and report agreement drift")
    jc.add_argument("dir", nargs="?", default=".",
                    help="directory of gold-labeled takes")
    vlm_flags(jc)
    jc.add_argument("--history", default="dailies-judge-check.json",
                    help="run history file (default "
                         "dailies-judge-check.json)")
    jc.add_argument("--fail-below", type=float, metavar="KAPPA",
                    help="exit 1 when kappa lands below this")
    jc.add_argument("--json", action="store_true")
    jc.set_defaults(func=cmd_judge_check)

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
