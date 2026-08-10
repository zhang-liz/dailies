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


def _rule_features(take, rule_names):
    """Per-rule evidence vector for one take: max severity times
    confidence of that rule's defects, scaled to 0..1."""
    per = {}
    defects = ((take.get("review") or {}).get("vlm") or {}).get(
        "defects") or []
    for d in defects:
        s = d["severity"] * (d.get("confidence") or 1.0)
        if s > per.get(d["rule"], 0.0):
            per[d["rule"]] = s
    return [per.get(r, 0.0) / 5.0 for r in rule_names]


def _sigmoid(z):
    if z < -60:
        return 0.0
    if z > 60:
        return 1.0
    return 1.0 / (1.0 + math.exp(-z))


def fit(root, iters=2000, lr=0.5, l2=1e-3):
    """Fit per-rule weights to the user's own verdicts: logistic
    regression from rule evidence to the gold kill label, plain
    gradient descent, deterministic (zero init). The judge has fixed
    opinions; the user's history says which rules actually predict
    their kills (EvalCrafter's calibration step, arXiv:2310.11440)."""
    data = [(t["gold"]["label"] == "kill", t)
            for _, t in gold.collect(root)
            if ((t.get("review") or {}).get("vlm"))]
    if len(data) < 4:
        raise RuntimeError(
            "need at least 4 gold-labeled takes with VLM reviews to "
            "fit; have %d" % len(data))
    rule_names = sorted({d["rule"] for _, t in data
                         for d in t["review"]["vlm"]["defects"]})
    if not rule_names:
        raise RuntimeError("no defects in the gold set; nothing to fit")
    X = [_rule_features(t, rule_names) for _, t in data]
    y = [1.0 if killed else 0.0 for killed, _ in data]
    n, m = len(X), len(rule_names)
    w, b = [0.0] * m, 0.0
    for _ in range(iters):
        gw, gb = [0.0] * m, 0.0
        for xi, yi in zip(X, y):
            err = _sigmoid(b + sum(wj * xj for wj, xj in zip(w, xi))) - yi
            gb += err
            for j in range(m):
                gw[j] += err * xi[j]
        b -= lr * gb / n
        for j in range(m):
            w[j] -= lr * (gw[j] / n + l2 * w[j])
    correct = sum(
        1 for xi, yi in zip(X, y)
        if (_sigmoid(b + sum(wj * xj for wj, xj in zip(w, xi))) >= 0.5)
        == (yi == 1.0))
    return {
        "weights": {r: round(wj, 4) for r, wj in zip(rule_names, w)},
        "bias": round(b, 4),
        "n_fit": n,
        "fit_accuracy": round(correct / n, 3),
    }


def rank_score(take, calibration):
    """Learned kill probability for ranking, or None without weights."""
    weights = (calibration or {}).get("weights")
    if not weights:
        return None
    rule_names = sorted(weights)
    x = _rule_features(take, rule_names)
    z = calibration.get("bias", 0.0) + sum(
        weights[r] * xi for r, xi in zip(rule_names, x))
    return _sigmoid(z)


def save(calibration, path):
    with open(path, "w") as f:
        json.dump(calibration, f, indent=2)
        f.write("\n")
    return path


def load(path):
    with open(path) as f:
        return json.load(f)
