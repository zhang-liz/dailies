"""dailies watch: review takes as they land, so the morning report exists
by morning.

Polling, not inotify/FSEvents: zero dependencies, works on every platform
and over network mounts, and a generation queue producing a clip every few
minutes does not need millisecond latency. A clip is reviewed only after
its size and mtime survive a poll unchanged AND the file has gone settle
seconds without being touched; encoders write files incrementally and
reviewing a half-written mp4 kills a good take.
"""

import collections
import json
import os
import shlex
import subprocess
import sys
import threading
import time

from . import breaker, ledger, pipeline, regen, take

# Submits per minute across all shots: a fast driver feeding a spinning
# loop is a money fire, so the ceiling is global and low by default.
REGEN_RATE = 6
# Consecutive driver failures that stop a shot's resubmissions for the
# rest of the run; a driver failing this often is misconfigured.
REGEN_FAIL_BLOCK = 3


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
         doom_confidence=None, on_kill=None, on_tick=None,
         **review_kwargs):
    """Watch root until stop is set. Calls emit(take, clip) after each
    review. Files that already have a current sidecar are skipped, so
    restarting the watcher never re-reviews old takes.

    After each review the doomed-shot breaker reassesses every shot from
    its sidecars (restart-safe: no state beyond the fired set). The take
    passed to emit carries an ephemeral _shot_doomed; on_doomed(shot,
    state) fires once per shot per run, after the take's own emit.
    on_kill(take, clip, takes) fires last on each fresh kill verdict, so
    a regen hook acts after the human-facing lines have printed.
    on_tick() fires once per poll iteration, after the reviews; the
    regen sweep retries deferred kills there."""
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
                if on_kill and t["review"]["verdict"] == "kill":
                    on_kill(t, path, takes)
        if on_tick and not stop.is_set():
            on_tick()
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


def parse_want(pairs):
    """--want SHOT=K pairs as the ledger's want map. Splits on the last
    "=" so shot ids containing one still parse."""
    want = {}
    for pair in pairs or []:
        shot, _, k = pair.rpartition("=")
        if not shot or not k.isdigit() or int(k) < 1:
            raise ValueError("bad --want %r, want SHOT=K with K >= 1"
                             % pair)
        want[shot] = int(k)
    return want


class Regenerator:
    """The watch-side regen policy: one on_kill() per fresh kill.

    Every stopping rule that sidecars can prove lives in the ledger's
    should_submit gate; this class adds only what the loop alone knows:
    a global submits-per-minute cap and a consecutive-driver-failure
    block, both defenses against a misconfigured driver spinning
    submit-fail loops. Failed submits count against the rate cap too,
    so an instantly-erroring driver cannot spin faster than a working
    one. Failure counts are per-run memory on purpose: a restart gives
    the driver one more chance, and the rate cap bounds the damage if
    it is still broken."""

    def __init__(self, driver, ledger_path, want=None, dry_run=False,
                 rate=REGEN_RATE, fail_block=REGEN_FAIL_BLOCK,
                 now=time.time):
        self.driver = driver
        self.path = ledger_path
        self.dry_run = dry_run
        self.rate = rate
        self.fail_block = fail_block
        self.now = now
        self.submits = collections.deque()
        self.failures = {}
        self.led = ledger.load(ledger_path)
        if want:
            self.led["want"].update(want)
        if not dry_run:
            # Restart recovery: adopt jobs a crash left only in stub
            # sidecars, then settle everything pending, filesystem
            # first, driver poll second.
            ledger.adopt_stubs(
                self.led,
                os.path.dirname(os.path.abspath(ledger_path)) or ".")
            if any(j["state"] not in regen.TERMINAL
                   for j in self.led["jobs"].values()):
                ledger.reconcile(self.led)
            ledger.save(self.path, self.led)

    def _sweep(self):
        """Mark pending jobs whose clips landed. Filesystem only, no
        polling: mid-run, a missing clip just means still rendering."""
        changed = False
        for jid in sorted(self.led["jobs"]):
            job = self.led["jobs"][jid]
            if job["state"] not in regen.TERMINAL \
                    and os.path.exists(job["clip"]):
                ledger.record_result(self.led, jid, "done")
                changed = True
        return changed

    def sweep_kills(self, root=None):
        """Retry orphaned kills: killed sidecars with no child stub and
        no ledger job for their take. Run once per poll tick (the first
        tick doubles as restart recovery), so a kill whose submit was
        skipped by the rate cap, or lost to a crash between review and
        submit, is deferred to a later tick, never dropped for the
        night. Returns the events for kills acted on; refusals stay
        silent because they repeat every tick."""
        if self.dry_run:
            return []
        if root is None:
            root = os.path.dirname(os.path.abspath(self.path)) or "."
        takes = {}
        for dirpath, _, files in os.walk(root):
            for f in files:
                if f.endswith(".take.json"):
                    clip = os.path.join(dirpath, f)[:-len(".take.json")]
                    takes[clip] = take.load(clip)
        submitted = set()
        for t in takes.values():
            if t.get("parent"):
                submitted.add(t["parent"])
        for job in self.led["jobs"].values():
            if job.get("parent"):
                submitted.add(job["parent"])
        events = []
        for clip in sorted(takes):
            t = takes[clip]
            if (t.get("review") or {}).get("verdict") != "kill":
                continue
            if t.get("take_id") in submitted:
                continue
            if not os.path.exists(clip):
                continue  # the corpse was purged; nothing to reseed
            ev = self.on_kill(t, clip, takes)
            if ev["action"] != "skipped":
                events.append(ev)
        return events

    def on_kill(self, t, clip, takes):
        """Decide and act on one killed take. Returns the event dict the
        caller prints: action submitted, dry-run, skipped (reason names
        the stopping rule), or driver-error."""
        shot = t.get("shot")
        event = {"event": "regen", "shot": shot, "parent": clip}
        changed = self._sweep()
        cutoff = self.now() - 60.0
        while self.submits and self.submits[0] < cutoff:
            self.submits.popleft()
        if self.failures.get(shot, 0) >= self.fail_block:
            event.update(action="skipped",
                         reason="shot %s blocked: %d straight driver "
                                "failures" % (shot, self.failures[shot]))
        elif len(self.submits) >= self.rate:
            event.update(action="skipped",
                         reason="submit rate cap %d/min reached"
                                % self.rate)
        else:
            ok, why = ledger.should_submit(self.led, takes, clip)
            changed = True  # should_submit refreshed the shots table
            if not ok:
                event.update(action="skipped", reason=why)
            elif self.dry_run:
                event.update(action="dry-run",
                             seeds=regen.mutate(t.get("recipe"))["seeds"])
            else:
                self.submits.append(self.now())
                try:
                    job = regen.resubmit(self.driver, clip)
                except regen.DriverError as e:
                    n = self.failures.get(shot, 0) + 1
                    self.failures[shot] = n
                    event.update(action="driver-error", error=str(e),
                                 failures=n, fail_block=self.fail_block)
                else:
                    self.failures[shot] = 0
                    ledger.record_submit(self.led, self.driver, shot,
                                         job)
                    event.update(action="submitted", job=job["job"],
                                 clip=job["clip"],
                                 seeds=(job["recipe"] or {}).get("seeds"))
        if changed and not self.dry_run:
            ledger.save(self.path, self.led)
        return event


def judge_gate_error(args):
    """The refusal that stops watch --regen on an unmeasured judge, or
    None when the loop may start. A dry run submits nothing, so only a
    live loop needs the gate; --allow-unchecked-judge is the explicit
    override. The history is looked for in the watched directory first,
    then the working directory, where judge-check writes by default."""
    if not args.regen or args.dry_run or args.allow_unchecked_judge:
        return None
    from . import defense, judgecheck
    paths = [os.path.join(args.dir, judgecheck.HISTORY),
             judgecheck.HISTORY]
    if os.path.abspath(paths[0]) == os.path.abspath(paths[1]):
        paths = paths[:1]
    ok, why = defense.judge_gate(paths, min_kappa=args.min_kappa)
    return None if ok else why


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
    try:
        want = parse_want(args.want)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    if not args.regen and (args.dry_run or want):
        print("%s needs --regen"
              % ("--dry-run" if args.dry_run else "--want"),
              file=sys.stderr)
        return 2
    gate = judge_gate_error(args)
    if gate:
        print("refusing to start --regen: %s" % gate, file=sys.stderr)
        return 2

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

    on_kill = None
    on_tick = None
    if args.regen:
        regenerator = Regenerator(
            args.regen, os.path.join(args.dir, "dailies-night.json"),
            want=want, dry_run=args.dry_run, rate=args.regen_rate)

        def print_regen(ev):
            if args.json:
                print(json.dumps(ev), flush=True)
                return
            rel = os.path.relpath(ev["parent"], args.dir)
            if ev["action"] == "submitted":
                msg = "submitted %s for %s; lands at %s" % (
                    ev["job"], rel,
                    os.path.relpath(ev["clip"], args.dir))
            elif ev["action"] == "dry-run":
                msg = "would resubmit %s with seeds %s" % (
                    rel, json.dumps(ev["seeds"]))
            elif ev["action"] == "driver-error":
                msg = "driver failed (%d of %d): %s" % (
                    ev["failures"], ev["fail_block"], ev["error"])
            else:
                msg = "skipped %s: %s" % (rel, ev["reason"])
            print("%s  REGEN    %s" % (time.strftime("%H:%M:%S"), msg),
                  flush=True)

        def on_kill(t, clip, takes):
            print_regen(regenerator.on_kill(t, clip, takes))

        if not args.dry_run:
            def on_tick():
                # Deferred and orphaned kills get another chance each
                # poll tick; the first tick is restart recovery.
                for ev in regenerator.sweep_kills(args.dir):
                    print_regen(ev)

    prices = None
    if getattr(args, "prices", None):
        from . import cost
        prices = cost.load(args.prices)

    if not args.json:
        print("watching %s every %gs; ctrl-c to stop"
              % (args.dir, args.interval), flush=True)
    try:
        loop(args.dir, interval=args.interval, shot=args.shot,
             report_path=args.report, emit=emit, on_doomed=on_doomed,
             on_kill=on_kill, on_tick=on_tick,
             vlm_endpoint=args.vlm, vlm_model=args.vlm_model,
             rubric_path=args.rubric,
             api_key=os.environ.get("DAILIES_VLM_KEY"),
             samples=args.samples,
             strong_endpoint=args.vlm_strong,
             strong_model=args.vlm_strong_model,
             prices=prices,
             audit_rate=args.audit_rate)
    except KeyboardInterrupt:
        pass
    return 0
