"""Gold labels: the user's own pass/kill verdicts, stored in sidecars.

`dailies gold add` records the call a human actually made on a take.
One labeling session funds three features: conformal calibration of the
kill threshold, fitting per-rule weights to the user's taste, and
regression-checking the judge after a model or prompt change. The label
lives in the sidecar's "gold" block, so it survives file moves with the
take and needs no separate database.
"""

import datetime
import os

from . import pipeline, take

LABELS = ("pass", "kill")


def add(clip, label):
    """Record a human verdict for one clip. Returns the sidecar path."""
    if label not in LABELS:
        raise ValueError("gold label must be one of %s" % (LABELS,))
    t = take.load(clip)
    if not t.get("take_id"):
        t["take_id"] = take.hash_file(clip)
    t["gold"] = {
        "label": label,
        "labeled": datetime.datetime.now(datetime.timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return take.save(clip, t)


def collect(root):
    """All gold-labeled takes under root: list of (clip_path, take)."""
    out = []
    for dirpath, _, files in os.walk(root):
        for f in files:
            if not f.endswith(".take.json"):
                continue
            clip = os.path.join(dirpath, f)[: -len(".take.json")]
            t = take.load(clip)
            if (t.get("gold") or {}).get("label") in LABELS:
                out.append((clip, t))
    return sorted(out)


def add_paths(paths, label):
    """Label every clip in paths (files, globs, directories)."""
    clips = pipeline.find_clips(paths)
    return [(clip, add(clip, label)) for clip in clips]
