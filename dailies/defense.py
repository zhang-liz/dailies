"""Regen-to-green defense: extra scrutiny for takes born of the loop.

A loop that mutates a take until the judge says yes converges on takes
that fool the judge, exactly where VLMs hallucinate worst; a fake keep
in the cut is worse than a kill. Three layers guard acceptance: a
passing take with ancestors is re-judged on the rules that killed them,
at higher sample count and with forced escalation to the strong judge;
a deterministic slice of auto-passed regen takes is routed to the human
review pile with an audit badge; and the loop refuses to start on a
judge with no healthy check history. Plus the intent guard: adherence
is always judged against the chain's original prompt, so the loop
cannot pass a take by deleting the hard part of the prompt.
"""

import copy
import hashlib
import json
import os

from . import brief, vlm

# A regen survivor is never accepted at single-shot cheap-judge
# confidence; the rules that killed its ancestors are re-asked at least
# this many times.
SCRUTINY_SAMPLES = 3
# Share of auto-passed regen takes badged for human audit. Rising
# disagreement on audited takes is the alarm that the loop found a
# judge blind spot.
AUDIT_RATE = 0.15
# Lowest last-run judge-check kappa an unattended regen loop may build
# on. 0.6 is the conventional floor for substantial agreement.
MIN_KAPPA = 0.6


def lineage_index(dirpath):
    """take_id-keyed sidecars from one directory's *.take.json files.

    Read straight from JSON, never through clips: killed ancestors keep
    their sidecars after their clips purge, and the chain must render
    from data alone. One directory, no recursion, because regen lands
    children next to their parents."""
    out = {}
    try:
        names = sorted(os.listdir(dirpath))
    except OSError:
        return out
    for name in names:
        if not name.endswith(".take.json"):
            continue
        try:
            with open(os.path.join(dirpath, name)) as f:
                t = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(t, dict) and t.get("take_id"):
            out[t["take_id"]] = t
    return out


def ancestors(t, by_id):
    """Parent chain of t, nearest first. A cycle or a parent missing
    from the index ends the walk instead of hanging the loop."""
    seen = set()
    pid = t.get("parent")
    while pid and pid not in seen:
        seen.add(pid)
        a = by_id.get(pid)
        if a is None:
            return
        yield a
        pid = a.get("parent")


def parent_kill_rules(t, by_id):
    """Rubric rules that killed any ancestor of t, sorted. Mechanical
    deaths need no re-judging (the child ran the same deterministic
    checks), and a calibrated-threshold kill names no single rule."""
    names = set()
    for a in ancestors(t, by_id):
        r = a.get("review") or {}
        if r.get("verdict") != "kill":
            continue
        for reason in (r.get("mechanical") or {}).get("kill_reasons") or []:
            side, key = brief.kill_class(reason)
            if side == "rule" and key != "calibrated":
                names.add(key)
    return sorted(names)


def root_prompt_text(t, by_id):
    """The prompt_text recorded deepest in t's chain, or None. That is
    the original creative intent; anything nearer may carry patches."""
    text = None
    for a in ancestors(t, by_id):
        pt = (a.get("recipe") or {}).get("prompt_text")
        if pt is not None:
            text = pt
    return text


def intent_guard(t, by_id):
    """(judged_take, guarded): t itself, or a copy whose recipe carries
    the root ancestor's prompt_text for the judge to hold it against.
    The sidecar on disk keeps its own recipe verbatim; only the judging
    context is swapped."""
    root = root_prompt_text(t, by_id)
    if root is None or root == (t.get("recipe") or {}).get("prompt_text"):
        return t, False
    judged = copy.deepcopy(t)
    if not isinstance(judged.get("recipe"), dict):
        judged["recipe"] = {}
    judged["recipe"]["prompt_text"] = root
    return judged, True


def scrutinize(clip, t, rules, names, endpoint, model, api_key=None,
               samples=1, strong_endpoint=None, strong_model=None):
    """Re-judge the named rules on a passing regen take, or None when
    no named rule is in the rubric. At least SCRUTINY_SAMPLES asks per
    rule; with a strong endpoint every scrutinized rule escalates,
    confident cheap answers included, because the cheap judge already
    missed this failure once on an ancestor."""
    subset = {n: rules[n] for n in names if n in rules}
    if not subset:
        return None
    block = vlm.screen(clip, t, subset, endpoint, model, api_key=api_key,
                       samples=max(samples, SCRUTINY_SAMPLES))
    if strong_endpoint:
        block["uncertain"] = sorted(set(block.get("uncertain") or [])
                                    | set(subset))
        block = vlm.escalate(clip, t, subset, block, strong_endpoint,
                             strong_model or model, api_key=api_key)
    block["scrutinized"] = sorted(subset)
    return block


def audit_pick(take_id, rate=AUDIT_RATE):
    """Whether this take falls in the audit slice. Hash-based, not
    random: the same take always answers the same, so re-reviews and
    tests are stable and the rate needs no state file."""
    if not take_id or not rate or rate <= 0:
        return False
    digest = hashlib.sha256(take_id.encode()).hexdigest()
    return int(digest[:8], 16) / float(1 << 32) < rate


def judge_gate(paths, min_kappa=MIN_KAPPA):
    """(ok, why): may an unattended regen loop trust this judge? Reads
    the first existing judge-check history among paths; no history, no
    runs, or a last kappa under the floor refuses with the fix named."""
    override = "fix the judge or pass --allow-unchecked-judge"
    for path in paths:
        if not os.path.exists(path):
            continue
        try:
            with open(path) as f:
                runs = (json.load(f) or {}).get("runs") or []
        except ValueError:
            return False, ("judge-check history %s is not JSON; %s"
                           % (path, override))
        if not runs:
            break
        last = runs[-1]
        kappa = last.get("kappa")
        if kappa is None:
            return False, ("last judge-check run in %s records no "
                           "kappa; rerun `dailies judge-check` or pass "
                           "--allow-unchecked-judge" % path)
        if kappa < min_kappa:
            return False, ("judge kappa %.2f (checked %s) is below the "
                           "%.2f floor; %s"
                           % (kappa, last.get("created"), min_kappa,
                              override))
        return True, None
    return False, ("no judge-check runs found (looked for %s); run "
                   "`dailies judge-check --vlm URL` first or pass "
                   "--allow-unchecked-judge" % ", ".join(paths))
