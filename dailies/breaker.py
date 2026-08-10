"""Doomed-shot circuit breaker: a per-shot sequential monitor.

The biggest overnight cost is a configuration that was never going to
work burning takes until breakfast. Mechanical stats are free, so the
monitor watches only them: a Beta posterior on the shot's mechanical-kill
fraction, doomed when the posterior puts usable yield below a floor.
With the defaults, eight straight mechanical kills decide; one passing
take buys several more.

Pure functions over review dicts, no I/O: state rebuilds from sidecars
on every call, so a watcher restart loses nothing. It catches only
mechanically doomed prompts; a shot can pass mechanics and still die at
the VLM stage.
"""

import math

# Doomed when this much posterior mass says usable yield is below FLOOR.
# Mechanical kills are the cheap deaths; a healthy config passes
# mechanics most of the time, so a shot credibly under 25% usable is
# broken, not unlucky.
FLOOR = 0.25
CONFIDENCE = 0.90
PRIOR = (1.0, 1.0)  # uniform: no opinion before the first take


def _betacf(a, b, x):
    """Continued fraction for the incomplete beta (Lentz's method)."""
    tiny = 1e-30
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, 201):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-12:
            break
    return h


def betainc(a, b, x):
    """Regularized incomplete beta I_x(a, b): the Beta(a, b) CDF at x.
    Stdlib has no betainc, and the monitor needs exact posterior mass,
    not a normal approximation that lies at n=8."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                     + a * math.log(x) + b * math.log(1.0 - x))
    # The continued fraction converges fast only on its own side of the
    # mean; use the symmetry for the other side.
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def doom_probability(kills, passes, floor=FLOOR, prior=PRIOR):
    """Posterior probability that usable yield is below floor, after
    observing this many mechanical kills and passes."""
    a = prior[0] + kills
    b = prior[1] + passes
    # usable = 1 - p, so P(usable < floor) = P(p > 1 - floor).
    return 1.0 - betainc(a, b, 1.0 - floor)


def is_mechanical_kill(review):
    """Mechanically killed takes never reach the VLM stage (the pipeline
    skips it on kill), so kill with no vlm block means mechanics did it."""
    return review.get("verdict") == "kill" and not review.get("vlm")


def assess(reviews, floor=FLOOR, confidence=CONFIDENCE, prior=PRIOR):
    """One shot's breaker state from its review dicts."""
    kills = sum(1 for r in reviews if is_mechanical_kill(r))
    n = len(reviews)
    p = doom_probability(kills, n - kills, floor=floor, prior=prior)
    return {"takes": n, "mechanical_kills": kills,
            "doom_probability": p, "doomed": p >= confidence}


def worst(pairs):
    """Deepest-ranked mechanical kill among (clip, review) pairs: the
    take a human should see first when the breaker trips."""
    kills = [(clip, r) for clip, r in pairs if is_mechanical_kill(r)]
    if not kills:
        return None
    clip, _ = max(kills, key=lambda cr: cr[1].get("rank_in_shot") or 0)
    return clip


def states(takes, floor=FLOOR, confidence=CONFIDENCE, prior=PRIOR):
    """Breaker state per shot from clip-keyed takes (rerank's shape),
    each with the worst kill attached for the hook."""
    by_shot = {}
    for clip, t in takes.items():
        shot = t.get("shot")
        r = t.get("review")
        if shot and r:
            by_shot.setdefault(shot, []).append((clip, r))
    out = {}
    for shot, pairs in by_shot.items():
        st = assess([r for _, r in pairs], floor=floor,
                    confidence=confidence, prior=prior)
        st["worst"] = worst(pairs)
        out[shot] = st
    return out
