"""dailies watch: review takes as they land, so the morning report exists
by morning.

Polling, not inotify/FSEvents: zero dependencies, works on every platform
and over network mounts, and a generation queue producing a clip every few
minutes does not need millisecond latency. A clip is reviewed only after
its size and mtime survive a poll unchanged AND the file has gone settle
seconds without being touched; encoders write files incrementally and
reviewing a half-written mp4 kills a good take.
"""

import json
import os
import shlex
import subprocess
import sys
import threading
import time

from . import breaker, pipeline, take


def _snapshot(root):
    """Current video files under root: path to (size, mtime)."""
    state = {}
    for dirpath, _, files in os.walk(root):
        for f in files:
            if not pipeline.is_video(f):
                continue
            path = os.path.join(dirpath, f)
            try:
                st = os.stat(path)
            except OSError:
                continue
            state[path] = (st.st_size, st.st_mtime)
    return state


def loop(root, interval=5.0, settle=None, shot=None, report_path=None,
         stop=None, emit=None, on_doomed=None, doom_floor=None,
         doom_confidence=None, **review_kwargs):
    """Watch root until stop is set. Calls emit(take, clip) after each
    review. Files that already have a current sidecar are skipped, so
    restarting the watcher never re-reviews old takes.

    After each review the doomed-shot breaker reassesses every shot from
    its sidecars (restart-safe: no state beyond the fired set). The take
    passed to emit carries an ephemeral _shot_doomed; on_doomed(shot,
    state) fires once per shot per run, after the take's own emit."""
    stop = stop or threading.Event()
    if settle is None:
        settle = max(interval, 1.0)
    doom_kwargs = {}
    if doom_floor is not None:
        doom_kwargs["floor"] = doom_floor
    if doom_confidence is not None:
        doom_kwargs["confidence"] = doom_confidence
    fired = set()
    previous = {}
    while not stop.is_set():
        current = _snapshot(root)
        for path, sig in sorted(current.items()):
            if stop.is_set():
                break
            if previous.get(path) != sig:
                continue  # new or still growing; wait one more interval
            if time.time() - sig[1] < settle:
                continue  # touched too recently; let the encoder finish
            if os.path.exists(take.sidecar_path(path)):
                t = take.load(path)
                if t.get("review"):
                    continue  # reviewed in an earlier run
            t, cached = pipeline.review_clip(path, shot=shot,
                                             **review_kwargs)
            if not cached:
                takes = pipeline.rerank(list(current))
                states = breaker.states(takes, **doom_kwargs)
                if report_path:
                    from . import report
                    report.build(root, report_path)
                if emit:
                    t = take.load(path)
                    st = states.get(t.get("shot"))
                    t["_shot_doomed"] = bool(st and st["doomed"])
                    emit(t, path)
                if on_doomed:
                    for s, st in sorted(states.items()):
                        if st["doomed"] and s not in fired:
                            fired.add(s)
                            on_doomed(s, st)
        previous = current
        stop.wait(interval)


def serialize(t, clip):
    """The per-take machine line. One shape shared by watch --json,
    review --ndjson, and verdict, so orchestrators parse one contract."""
    r = t["review"]
    return {"clip": clip, "shot": t.get("shot"),
            "verdict": r["verdict"],
            "rank_in_shot": r.get("rank_in_shot"),
            "kill_reasons": r["mechanical"]["kill_reasons"]}


def run_hook(cmd, shot, sidecar):
    """Fire and forget: a hook that hangs must never stall review."""
    try:
        subprocess.Popen(shlex.split(cmd) + [shot, sidecar])
    except (OSError, ValueError) as e:
        print("on-doomed hook failed: %s" % e, file=sys.stderr)


def run(args):
    """CLI entry: watch until interrupted."""
    if not os.path.isdir(args.dir):
        print("not a directory: %s" % args.dir, file=sys.stderr)
        return 1

    def emit(t, clip):
        r = t["review"]
        if args.json:
            line = serialize(t, clip)
            line["shot_doomed"] = bool(t.get("_shot_doomed"))
            print(json.dumps(line), flush=True)
        else:
            reasons = "; ".join(r["mechanical"]["kill_reasons"])
            print("%s  %-8s %s %s" % (
                time.strftime("%H:%M:%S"), r["verdict"],
                os.path.relpath(clip, args.dir), reasons), flush=True)

    def on_doomed(shot, st):
        worst = take.sidecar_path(st["worst"]) if st["worst"] else ""
        if args.json:
            print(json.dumps({"event": "doomed", "shot": shot,
                              "takes": st["takes"],
                              "mechanical_kills": st["mechanical_kills"],
                              "doom_probability":
                                  round(st["doom_probability"], 4),
                              "worst_sidecar": worst}), flush=True)
        else:
            print("%s  DOOMED   %s: %d mechanical kills in %d takes; "
                  "change the recipe before burning more" % (
                      time.strftime("%H:%M:%S"), shot,
                      st["mechanical_kills"], st["takes"]), flush=True)
        if args.on_doomed:
            run_hook(args.on_doomed, shot, worst)

    if not args.json:
        print("watching %s every %gs; ctrl-c to stop"
              % (args.dir, args.interval), flush=True)
    try:
        loop(args.dir, interval=args.interval, shot=args.shot,
             report_path=args.report, emit=emit, on_doomed=on_doomed,
             vlm_endpoint=args.vlm, vlm_model=args.vlm_model,
             rubric_path=args.rubric,
             api_key=os.environ.get("DAILIES_VLM_KEY"),
             samples=args.samples,
             strong_endpoint=args.vlm_strong,
             strong_model=args.vlm_strong_model)
    except KeyboardInterrupt:
        pass
    return 0
