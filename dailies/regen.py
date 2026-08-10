"""Regen drivers: resubmit a failed take through an external executable.

A driver is a subprocess honoring a two-call contract, not an import,
on the git remote-helper model: the core stays stdlib plus ffmpeg while
backends stay arbitrary. `<driver> submit` reads a job JSON on stdin
and prints a job id; `<driver> poll <id>` prints a status JSON with a
state and, once done, the landed clip's path. docs/DRIVERS.md is the
contract; this module is its reference client.

The mutation surface is one thing, a fresh seed. Anything smarter is
the defect-conditioned policy on the roadmap, and a policy fitted on
logged outcomes beats one guessed at now.
"""

import copy
import datetime
import json
import os
import random
import shlex
import subprocess
import time

from . import take

STATES = ("queued", "running", "done", "error")
TERMINAL = ("done", "error")
DRIVER_TIMEOUT = 60.0


class DriverError(RuntimeError):
    """A driver broke the contract; the caller decides what to do."""


def _utcnow():
    return (datetime.datetime.now(datetime.timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ"))


def mutate(recipe):
    """The failed recipe with every seed replaced. Everything else is
    copied verbatim so provenance stays honest. A parent recording no
    seed nodes gets one seed under the key "seed" for the driver to
    map, because rerunning the same seed reproduces the corpse."""
    out = copy.deepcopy(recipe) if recipe else {}
    seeds = out.get("seeds") or {"seed": None}
    fresh = {}
    for node, old in seeds.items():
        new = random.getrandbits(32)
        while new == old:
            new = random.getrandbits(32)
        fresh[node] = new
    out["seeds"] = fresh
    return out


def new_clip_path(failed_clip, out_dir=None):
    """Destination for the regenerated clip, chosen before submission
    so the sidecar stub can exist before the pixels do."""
    stem, ext = os.path.splitext(os.path.basename(failed_clip))
    out_dir = out_dir or os.path.dirname(os.path.abspath(failed_clip))
    while True:
        path = os.path.join(out_dir, "%s-regen-%08x%s"
                            % (stem, random.getrandbits(32),
                               ext or ".mp4"))
        if not os.path.exists(path) \
                and not os.path.exists(take.sidecar_path(path)):
            return path


def write_stub(clip_path, parent_id, recipe, shot=None):
    """Provenance before pixels: parent and mutated recipe hit disk
    before the driver runs, so a crash mid-generation loses nothing.
    Read-modify-write per SPEC: blocks owned by other tools survive."""
    t = take.load(clip_path)
    t["parent"] = parent_id
    t["recipe"] = recipe
    if shot:
        t["shot"] = shot
    if not t.get("created"):
        t["created"] = _utcnow()
    take.save(clip_path, t)
    return t


def _run(driver, argv, stdin=None):
    cmd = shlex.split(driver) + argv
    try:
        proc = subprocess.run(cmd, input=stdin, capture_output=True,
                              text=True, timeout=DRIVER_TIMEOUT)
    except OSError as e:
        raise DriverError("driver %r failed to start: %s" % (driver, e))
    except subprocess.TimeoutExpired:
        raise DriverError("driver %r %s hung past %gs"
                          % (driver, argv[0], DRIVER_TIMEOUT))
    if proc.returncode != 0:
        raise DriverError("driver %r %s exited %d: %s"
                          % (driver, argv[0], proc.returncode,
                             proc.stderr.strip()))
    return proc.stdout


def submit(driver, job):
    """Hand the driver a job, get back its opaque id. The stub sidecar
    must already exist; resubmit() handles the whole sequence."""
    out = _run(driver, ["submit"], stdin=json.dumps(job))
    job_id = out.strip()
    if not job_id or "\n" in job_id:
        raise DriverError("driver %r submit printed %r, want one "
                          "job id line" % (driver, out))
    return job_id


def poll(driver, job_id):
    """One status check, validated now so a driver drifting off the
    contract fails loudly instead of stalling the loop."""
    out = _run(driver, ["poll", job_id])
    try:
        status = json.loads(out)
    except ValueError:
        raise DriverError("driver %r poll printed %r, want a status "
                          "JSON object" % (driver, out))
    if not isinstance(status, dict) or status.get("state") not in STATES:
        raise DriverError("driver %r poll state %r, want one of %s"
                          % (driver,
                             status.get("state")
                             if isinstance(status, dict) else status,
                             "/".join(STATES)))
    if status["state"] == "done" and not status.get("output"):
        raise DriverError("driver %r reported done without an output "
                          "path" % driver)
    return status


def wait(driver, job_id, interval=2.0, timeout=600.0, stop=None):
    """Poll until a terminal state. Raises DriverError when the timeout
    passes first, because a silent hang would strand the regen loop; a
    set stop event returns the last status instead."""
    deadline = time.time() + timeout
    while True:
        status = poll(driver, job_id)
        if status["state"] in TERMINAL:
            return status
        if stop is not None and stop.is_set():
            return status
        if time.time() >= deadline:
            raise DriverError("driver %r job %s still %r after %gs"
                              % (driver, job_id, status["state"],
                                 timeout))
        time.sleep(interval)


def resubmit(driver, failed_clip, out_dir=None, shot=None):
    """The one call per kill: mutate the failed take's recipe, pre-write
    the new clip's stub, submit, record the job id in the stub so a
    crashed loop can reconcile against `<driver> poll`. Returns the job
    record: clip, job, parent, recipe."""
    t = take.load(failed_clip)
    parent = t.get("take_id") or take.hash_file(failed_clip)
    clip = new_clip_path(failed_clip, out_dir)
    recipe = mutate(t.get("recipe"))
    stub_shot = shot or t.get("shot")
    write_stub(clip, parent, recipe, shot=stub_shot)
    job_id = submit(driver, {"clip": clip, "parent": parent,
                             "shot": stub_shot, "recipe": recipe})
    stub = take.load(clip)
    stub["regen"] = {"driver": driver, "job": job_id,
                     "submitted": _utcnow()}
    take.save(clip, stub)
    return {"clip": clip, "job": job_id, "parent": parent,
            "recipe": recipe}
