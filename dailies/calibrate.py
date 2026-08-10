"""Conformal calibration: a kill threshold with a stated guarantee.

Hand-picked fail_at thresholds cannot say how often they kill a good
take. Split conformal calibration can (Conformal Risk Control,
arXiv:2208.02814): rank the judge's kill scores on the user's own
gold-pass takes and set the threshold at the ceil((n+1)(1-alpha))-th
smallest. Under exchangeability, a fresh good take then crosses the
threshold with probability at most alpha: "at most 5% of kills are
wrong" becomes a statement, not a hope.

The guarantee is only as good as its distribution: swap the judge
model, the rubric, or the video generator and the calibration must be
rerun. `dailies judge-check` exists to notice exactly that.

Pure stdlib: sorting and one quantile.
"""

import datetime
import json
import math

from . import gold

# Fewer gold-pass takes than this and the conformal quantile does not
# exist at alpha=0.05; calibrate says so instead of guessing.
DEFAULT_ALPHA = 0.05


def kill_score(take):
    """One number per take: how hard the judge wants it dead.

    Max over VLM defects of severity times confidence (unsampled
    defects count full). 0..5, continuous once sampling is on.
    Mechanical kills are not scored; they are deterministic and cheap
    to verify, and the guarantee is about the judge."""
    defects = ((take.get("review") or {}).get("vlm") or {}).get(
        "defects") or []
    return max((d["severity"] * (d.get("confidence") or 1.0)
                for d in defects), default=0.0)


def calibrate(root, alpha=DEFAULT_ALPHA):
    """Conformal threshold from the gold-labeled, VLM-reviewed takes
    under root. Returns the calibration dict; raises RuntimeError when
    the gold set cannot support the guarantee."""
    scored = [(t["gold"]["label"], kill_score(t))
              for _, t in gold.collect(root)
              if ((t.get("review") or {}).get("vlm"))]
    pass_scores = sorted(s for label, s in scored if label == "pass")
    kill_scores = [s for label, s in scored if label == "kill"]
    n = len(pass_scores)
    if n == 0:
        raise RuntimeError(
            "no gold-pass takes with VLM reviews under %r; label takes "
            "with `dailies gold add` and review them first" % root)
    k = math.ceil((n + 1) * (1 - alpha))
    lam = pass_scores[k - 1] if k <= n else None
    out = {
        "alpha": alpha,
        "lambda": lam,
        "n_pass": n,
        "n_kill": len(kill_scores),
        "created": datetime.datetime.now(datetime.timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if lam is not None and kill_scores:
        out["kill_recall"] = round(
            sum(1 for s in kill_scores if s > lam) / len(kill_scores), 3)
    if lam is None:
        out["needed"] = math.ceil((1 - alpha) / alpha)
    return out


def save(calibration, path):
    with open(path, "w") as f:
        json.dump(calibration, f, indent=2)
        f.write("\n")
    return path


def load(path):
    with open(path) as f:
        return json.load(f)
