"""Judge regression checks: notice when a judge change moves your kills.

Swap the judge model, tweak a prompt, edit the rubric: behavior changes
silently, and the first symptom is a week of bad triage. The fix is the
one the meta-evaluation literature agrees on (EvalGen, arXiv:2404.12272;
JudgeBench, arXiv:2410.12784): a frozen gold set, re-judged after every
change, with agreement and Cohen's kappa tracked run over run.

`dailies judge-check` re-runs the configured judging stack over the
gold-labeled takes, writes nothing into their sidecars, appends the
result to a history file, and reports the delta against the last run.
"""

import copy
import datetime
import json
import os

from . import calibrate as calibrate_mod
from . import gold, mechanical, vlm

HISTORY = "dailies-judge-check.json"


def kappa(pairs):
    """Cohen's kappa over (gold, predicted) label pairs."""
    n = len(pairs)
    if not n:
        return None
    po = sum(1 for g, p in pairs if g == p) / n
    gk = sum(1 for g, _ in pairs if g == "kill") / n
    pk = sum(1 for _, p in pairs if p == "kill") / n
    pe = gk * pk + (1 - gk) * (1 - pk)
    if pe == 1:
        return 1.0 if po == 1 else 0.0
    return (po - pe) / (1 - pe)


def _judge(clip, t, rules, vlm_endpoint, vlm_model, api_key=None,
           samples=1, calibration=None, strong_endpoint=None,
           strong_model=None):
    """Predicted verdict for one take under the configured stack,
    without touching its sidecar."""
    t = copy.deepcopy(t)
    if not t.get("review"):
        t["review"] = mechanical.review(clip)
    r = t["review"]
    if r["mechanical"]["kill_reasons"] and not r.get("vlm"):
        return "kill"  # a mechanical death needs no judge
    block = vlm.screen(clip, t, rules, vlm_endpoint, vlm_model,
                       api_key=api_key, samples=samples)
    if strong_endpoint and (block.get("uncertain")
                            or block.get("unparsed")):
        block = vlm.escalate(clip, t, rules, block, strong_endpoint,
                             strong_model or vlm_model, api_key=api_key)
    if calibration is not None:
        r["vlm"] = block
        lam = calibration.get("lambda")
        s = calibrate_mod.kill_score(t)
        return "kill" if lam is not None and s > lam else "pass"
    return "kill" if vlm.kill_reasons(block, rules) else "pass"


def run(root, rules, history_path=None, **judge_kwargs):
    """Re-judge every gold take under root. Returns the run record,
    already appended to history."""
    pairs = []
    missing = 0
    for clip, t in gold.collect(root):
        if not os.path.exists(clip):
            missing += 1
            continue
        pairs.append((t["gold"]["label"],
                      _judge(clip, t, rules, **judge_kwargs)))
    if not pairs:
        raise RuntimeError(
            "no gold-labeled clips under %r; label takes with "
            "`dailies gold add` first" % root)
    record = {
        "engine": judge_kwargs.get("vlm_model"),
        "samples": judge_kwargs.get("samples", 1),
        "n": len(pairs),
        "missing_clips": missing,
        "agreement": round(
            sum(1 for g, p in pairs if g == p) / len(pairs), 3),
        "kappa": round(kappa(pairs), 3),
        "false_kills": sum(1 for g, p in pairs
                           if g == "pass" and p == "kill"),
        "missed_kills": sum(1 for g, p in pairs
                            if g == "kill" and p == "pass"),
        "created": datetime.datetime.now(datetime.timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    previous = None
    if history_path:
        history = {"runs": []}
        if os.path.exists(history_path):
            with open(history_path) as f:
                history = json.load(f)
        if history["runs"]:
            previous = history["runs"][-1]
        history["runs"].append(record)
        with open(history_path, "w") as f:
            json.dump(history, f, indent=2)
            f.write("\n")
    record["previous"] = previous
    return record
