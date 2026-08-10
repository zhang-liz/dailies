"""The night ledger: crash-safe state for an unattended regen run.

One dailies-night.json per run, written atomically like sidecars. An
uncapped regen loop is a money fire, so every stopping rule lives here
behind one gate, should_submit(): shot completion at k passing takes,
a lineage cap per parent chain, the three-distinct-seeds futility test,
and global attempt and spend caps.

The sidecars stay the truth: per-shot state is recomputed from them on
every refresh, never trusted across restarts. The ledger persists only
what the filesystem cannot know yet, the job table of submissions whose
clips have not landed, which reconcile() settles on restart against the
watched directory first and the driver's poll answers second.
"""

import datetime
import json
import os

from . import breaker, brief, regen

# A chain may hold this many takes, the original included: with the
# default, a root and three regens. Three fresh seeds that all die is
# the point where reseeding stops being evidence-gathering.
LINEAGE_CAP = 4
# One rule killing this many takes under distinct seeds proves the
# failure is not seed luck; the shot blocks and waits for a human.
FUTILITY_KILLS = 3
STATUSES = ("active", "complete", "blocked", "doomed")


def _utcnow():
    return (datetime.datetime.now(datetime.timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ"))


def fresh(want=None, want_default=1, lineage_cap=LINEAGE_CAP,
          attempt_cap=None, spend_cap=None):
    """A new run's ledger. want maps shot id to wanted passing takes;
    want_default of one means the loop fights for a single usable take
    per shot and stops, the survival bar for an overnight batch."""
    return {"version": 1,
            "created": _utcnow(),
            "updated": None,
            "want_default": want_default,
            "want": dict(want or {}),
            "caps": {"lineage": lineage_cap, "attempts": attempt_cap,
                     "spend_usd": spend_cap},
            "attempts": 0,
            "spend_usd": 0.0,
            "shots": {},
            "jobs": {}}


def load(path):
    """The ledger at path, or a fresh one: a loop restarted after a
    crash and a loop starting the night look the same to callers."""
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return fresh()


def save(path, ledger):
    """Atomic write, same tmp-then-replace as sidecars: a crash mid-save
    leaves the previous ledger, never half a JSON file."""
    ledger["updated"] = _utcnow()
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(ledger, f, indent=2, sort_keys=False)
        f.write("\n")
    os.replace(tmp, path)
    return path


def _seed_sig(t):
    """A take's seeds as a hashable signature, or None when unrecorded.
    Only recorded seeds can prove two kills were independent draws."""
    seeds = (t.get("recipe") or {}).get("seeds")
    if not seeds:
        return None
    return tuple(sorted(seeds.items()))


def futility(group):
    """The kill class that killed FUTILITY_KILLS takes under distinct
    seeds, or None. Seedless kills prove nothing about seed luck and do
    not count; ties break alphabetically so the answer is stable."""
    by_class = {}
    for t in group:
        r = t.get("review") or {}
        if r.get("verdict") != "kill":
            continue
        sig = _seed_sig(t)
        if sig is None:
            continue
        for reason in (r.get("mechanical") or {}).get("kill_reasons") or []:
            _, key = brief.kill_class(reason)
            by_class.setdefault(key, set()).add(sig)
    hits = sorted(k for k, sigs in by_class.items()
                  if len(sigs) >= FUTILITY_KILLS)
    return hits[0] if hits else None


def by_take_id(takes):
    """take_id index over clip-keyed takes, for parent-chain walks."""
    return {t["take_id"]: t for t in takes.values() if t.get("take_id")}


def chain_length(t, by_id):
    """Takes in the parent chain ending at t, t included. An ancestor
    outside the index still counts one hop, mirroring brief's lineage;
    a cycle stops the walk instead of hanging the loop."""
    n, seen = 1, set()
    while True:
        tid = t.get("take_id")
        if tid:
            if tid in seen:
                return n
            seen.add(tid)
        pid = t.get("parent")
        if not pid:
            return n
        n += 1
        t = by_id.get(pid)
        if t is None:
            return n


def refresh(ledger, takes):
    """Recompute per-shot state from clip-keyed sidecar dicts (the shape
    rerank returns) plus the job table. Status precedence: complete
    beats blocked beats doomed, because enough passing takes ends the
    argument and a named futile rule is more actionable than a Beta
    posterior. Returns and stores the shots table."""
    by_shot = {}
    for t in takes.values():
        if t.get("shot"):
            by_shot.setdefault(t["shot"], []).append(t)
    jobs_by_shot = {}
    for job in ledger["jobs"].values():
        if job.get("shot"):
            jobs_by_shot[job["shot"]] = jobs_by_shot.get(job["shot"], 0) + 1

    shots = {}
    total_spend = 0.0
    for shot in sorted(set(by_shot) | set(jobs_by_shot)):
        group = by_shot.get(shot, [])
        reviews = [t["review"] for t in group if t.get("review")]
        passing = sum(1 for r in reviews if r.get("verdict") != "kill")
        spend = sum((r.get("cost") or {}).get("total_usd") or 0
                    for r in reviews)
        want = ledger["want"].get(shot, ledger["want_default"])
        rule = futility(group)
        monitor = breaker.assess(reviews) if reviews else None
        if passing >= want:
            status = "complete"
        elif rule:
            status = "blocked"
        elif monitor and monitor["doomed"]:
            status = "doomed"
        else:
            status = "active"
        shots[shot] = {"want": want,
                       "takes": len(reviews),
                       "passing": passing,
                       "mechanical_kills": (monitor["mechanical_kills"]
                                            if monitor else 0),
                       "attempts": jobs_by_shot.get(shot, 0),
                       "spend_usd": round(spend, 6),
                       "status": status,
                       "blocked_by": rule if status == "blocked" else None}
        total_spend += spend
    ledger["shots"] = shots
    ledger["attempts"] = len(ledger["jobs"])
    ledger["spend_usd"] = round(total_spend, 6)
    return shots


def should_submit(ledger, takes, parent_clip):
    """The one gate before every resubmission. Returns (True, None) or
    (False, reason); the reason names the stopping rule so a refused
    regen is auditable in the morning, not a silent no-op."""
    shots = refresh(ledger, takes)
    caps = ledger["caps"]
    if caps.get("attempts") is not None \
            and ledger["attempts"] >= caps["attempts"]:
        return False, ("global attempt cap %d reached"
                       % caps["attempts"])
    if caps.get("spend_usd") is not None \
            and ledger["spend_usd"] >= caps["spend_usd"]:
        return False, ("spend $%.2f at the $%.2f cap"
                       % (ledger["spend_usd"], caps["spend_usd"]))
    t = takes.get(parent_clip)
    if t is None:
        return False, "parent %s not in the scanned takes" % parent_clip
    st = shots.get(t.get("shot") or "")
    if st and st["status"] == "complete":
        return False, ("shot %s complete: %d passing of %d wanted"
                       % (t["shot"], st["passing"], st["want"]))
    if st and st["status"] == "blocked":
        return False, ("shot %s blocked: %s kills under distinct seeds; "
                       "needs a human, not a reseed"
                       % (t["shot"], st["blocked_by"]))
    if st and st["status"] == "doomed":
        return False, ("shot %s doomed: %d mechanical kills in %d takes"
                       % (t["shot"], st["mechanical_kills"],
                          st["takes"]))
    cap = caps.get("lineage") or LINEAGE_CAP
    if chain_length(t, by_take_id(takes)) >= cap:
        return False, ("lineage cap %d reached for the chain ending at "
                       "%s" % (cap, parent_clip))
    return True, None


def record_submit(ledger, driver, shot, job):
    """One job-table row per submission, from resubmit()'s return. Saved
    before anything else can fail, the row is what reconcile() polls
    after a crash. The mutation is the seeds alone; the full recipe
    already lives in the stub sidecar.

    Job ids must be unique within a run (docs/DRIVERS.md); a driver
    reusing one gets a suffixed table key so the earlier row is never
    overwritten. The row's "job" keeps the driver's own id, and
    reconcile() polls with that, never the key."""
    jid = job["job"]
    key, n = jid, 1
    while key in ledger["jobs"]:
        n += 1
        key = "%s-dup%d" % (jid, n)
    ledger["jobs"][key] = {
        "driver": driver,
        "job": jid,
        "clip": job["clip"],
        "parent": job["parent"],
        "shot": shot,
        "seeds": (job.get("recipe") or {}).get("seeds"),
        "submitted": _utcnow(),
        "state": "queued"}
    ledger["attempts"] = len(ledger["jobs"])
    return ledger["jobs"][key]


def record_result(ledger, job_id, state, error=None):
    """Mark a job's driver outcome; terminal states get a resolved stamp
    so the morning report can tell finished jobs from stranded ones."""
    job = ledger["jobs"][job_id]
    job["state"] = state
    if error:
        job["error"] = error
    if state in regen.TERMINAL:
        job["resolved"] = _utcnow()
    return job


def adopt_stubs(ledger, root):
    """Adopt jobs the table never recorded: a crash between the driver
    submit (which stamps the stub sidecar's regen.job) and record_submit
    leaves the id only on disk, where reconcile() cannot see it. Walks
    root for *.take.json stubs carrying a regen job id absent from the
    job table and files each as a pending row for reconcile() to
    settle. Returns the adopted job ids."""
    adopted = []
    for dirpath, _, files in os.walk(root):
        for name in sorted(files):
            if not name.endswith(".take.json"):
                continue
            path = os.path.join(dirpath, name)
            try:
                with open(path) as f:
                    t = json.load(f)
            except (OSError, ValueError):
                continue
            if not isinstance(t, dict):
                continue
            reg = t.get("regen") or {}
            jid = reg.get("job")
            if not jid or jid in ledger["jobs"]:
                continue
            ledger["jobs"][jid] = {
                "driver": reg.get("driver"),
                "job": jid,
                "clip": path[:-len(".take.json")],
                "parent": t.get("parent"),
                "shot": t.get("shot"),
                "seeds": (t.get("recipe") or {}).get("seeds"),
                "submitted": reg.get("submitted"),
                "state": "queued",
                "adopted": _utcnow()}
            adopted.append(jid)
    if adopted:
        ledger["attempts"] = len(ledger["jobs"])
    return adopted


def reconcile(ledger, poll=None):
    """Restart recovery over the pending jobs. The filesystem outranks
    the driver: a landed clip is done no matter what poll would say, and
    the sidecar stub written before submit already carries provenance.
    The driver answers for the rest; a driver that errors or forgot the
    job marks it error instead of stranding it queued forever. Returns
    job id to observed state for every job that was pending."""
    if poll is None:
        poll = regen.poll
    observed = {}
    for jid in sorted(ledger["jobs"]):
        job = ledger["jobs"][jid]
        if job["state"] in regen.TERMINAL:
            continue
        if os.path.exists(job["clip"]):
            record_result(ledger, jid, "done")
            observed[jid] = "done"
            continue
        try:
            status = poll(job["driver"], job.get("job", jid))
        except regen.DriverError as e:
            record_result(ledger, jid, "error", str(e))
            observed[jid] = "error"
            continue
        state = status["state"]
        if state in regen.TERMINAL:
            record_result(ledger, jid, state, status.get("error"))
            if (state == "done" and status.get("output")
                    and status["output"] != job["clip"]):
                job["landed"] = status["output"]
        else:
            job["state"] = state
        observed[jid] = state
    return observed
