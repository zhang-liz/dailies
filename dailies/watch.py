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
import sys
import threading
import time

from . import pipeline, take


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
         stop=None, emit=None, **review_kwargs):
    """Watch root until stop is set. Calls emit(take, clip) after each
    review. Files that already have a current sidecar are skipped, so
    restarting the watcher never re-reviews old takes."""
    stop = stop or threading.Event()
    if settle is None:
        settle = max(interval, 1.0)
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
                pipeline.rerank(list(current))
                if report_path:
                    from . import report
                    report.build(root, report_path)
                if emit:
                    emit(take.load(path), path)
        previous = current
        stop.wait(interval)


def run(args):
    """CLI entry: watch until interrupted."""
    if not os.path.isdir(args.dir):
        print("not a directory: %s" % args.dir, file=sys.stderr)
        return 1

    def emit(t, clip):
        r = t["review"]
        if args.json:
            print(json.dumps({"clip": clip, "shot": t["shot"],
                              "verdict": r["verdict"],
                              "rank_in_shot": r["rank_in_shot"],
                              "kill_reasons":
                                  r["mechanical"]["kill_reasons"]}),
                  flush=True)
        else:
            reasons = "; ".join(r["mechanical"]["kill_reasons"])
            print("%s  %-8s %s %s" % (
                time.strftime("%H:%M:%S"), r["verdict"],
                os.path.relpath(clip, args.dir), reasons), flush=True)

    if not args.json:
        print("watching %s every %gs; ctrl-c to stop"
              % (args.dir, args.interval), flush=True)
    try:
        loop(args.dir, interval=args.interval, shot=args.shot,
             report_path=args.report, emit=emit,
             vlm_endpoint=args.vlm, vlm_model=args.vlm_model,
             rubric_path=args.rubric,
             api_key=os.environ.get("DAILIES_VLM_KEY"))
    except KeyboardInterrupt:
        pass
    return 0
