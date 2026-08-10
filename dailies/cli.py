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
    ndjson = getattr(args, "ndjson", False)
    if ndjson:
        from . import watch
    for clip in clips:
        t, _ = pipeline.review_clip(clip, shot=args.shot,
                                    force=args.force, **kwargs)
        if ndjson:
            print(json.dumps(watch.serialize(t, clip)), flush=True)
    takes = list(pipeline.rerank(
        clips, calibration=kwargs.get("calibration")).values())

    kills = [t for t in takes if t["review"]["verdict"] == "kill"]
    if ndjson:
        # The summary line has no "clip" key; that is how consumers
        # tell it from the per-take lines.
        print(json.dumps({"reviewed": len(takes),
                          "killed": len(kills)}), flush=True)
    elif args.json:
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


def cmd_verdict(args):
    """One clip in, one JSON line out; the exit code carries the call
    so a shell or orchestrator can branch without parsing."""
    from . import watch
    if not os.path.isfile(args.clip):
        print("not a file: %s" % args.clip, file=sys.stderr)
        return 2
    kwargs = _vlm_kwargs(args)
    t, _ = pipeline.review_clip(args.clip, shot=args.shot,
                                force=args.force, **kwargs)
    print(json.dumps(watch.serialize(t, args.clip)))
    return 3 if t["review"]["verdict"] == "kill" else 0


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


def cmd_schema(args):
    """Print a published schema verbatim, so orchestrators pin the file
    that ships with the version they run against."""
    path = os.path.join(os.path.dirname(__file__), "schemas",
                        args.name + ".schema.json")
    with open(path) as f:
        sys.stdout.write(f.read())
    return 0


def cmd_brief(args):
    from . import brief
    data = brief.build(args.dir)
    if not data["n_takes"]:
        print("no take sidecars found", file=sys.stderr)
        return 1
    if args.json:
        json.dump(data, sys.stdout, indent=2)
        print()
    else:
        print(brief.render(data))
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
    fmt = rv.add_mutually_exclusive_group()
    fmt.add_argument("--json", action="store_true")
    fmt.add_argument("--ndjson", action="store_true",
                     help="stream one JSON line per clip as it is "
                          "reviewed, then a summary line")
    rv.set_defaults(func=cmd_review)

    vd = sub.add_parser(
        "verdict",
        help="review one clip and answer in the exit code: 0 keep or "
             "review, 3 kill, 2 error")
    vd.add_argument("clip", help="one clip file")
    vd.add_argument("--shot", help="tag the take with this shot id")
    vd.add_argument("--force", action="store_true",
                    help="re-review even when the cached take_id matches")
    vlm_flags(vd)
    vd.set_defaults(func=cmd_verdict)

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

    sc = sub.add_parser(
        "schema",
        help="print a published JSON Schema for a dailies file")
    sc.add_argument("name",
                    choices=["take", "calibration", "judge-history"],
                    help="which contract: the take.json sidecar, the "
                         "calibration file, or the judge-check history")
    sc.set_defaults(func=cmd_schema)

    rp = sub.add_parser("report", help="write the static HTML morning report")
    rp.add_argument("dir", nargs="?", default=".",
                    help="directory to scan for take.json sidecars")
    rp.add_argument("-o", "--output", default="dailies-report.html")
    rp.add_argument("--json", action="store_true")
    rp.set_defaults(func=cmd_report)

    br = sub.add_parser(
        "brief",
        help="per-shot failure dossier from sidecars: kill histograms, "
             "rule stats, survivors, lineage, recipe deltas. No LLM")
    br.add_argument("dir", nargs="?", default=".",
                    help="directory to scan for take.json sidecars")
    br.add_argument("--json", action="store_true")
    br.set_defaults(func=cmd_brief)

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
