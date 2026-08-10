"""dailies brief: per-shot failure dossiers over take.json sidecars.

Deterministic aggregation, no LLM calls: the reasoning stays with the
agent or the human, brief supplies the evidence table. Counts, never
causes; a dozen takes per shot cannot support causal claims about seeds
or models. Same walk as the report, JSON target.
"""

import os
import re

from . import report

# A rule kill reason is written by vlm.kill_reasons as
# "<rule> severity <n> at <t>: <note>"; everything else in kill_reasons
# is mechanical, except the calibrated-threshold kill, which is
# judge-side and counted on the rule half under "calibrated".
_RULE_KILL = re.compile(r"^(\S+) severity \d+ at ")
_CALIBRATED = "calibrated kill score"


def _file(t):
    return ((t.get("output") or {}).get("file")
            or os.path.basename(t.get("_clip") or ""))


def _kill_histogram(group):
    """kill_reasons of the killed takes, split mechanical vs rule."""
    mech, rule = {}, {}
    for t in group:
        r = t.get("review") or {}
        if r.get("verdict") != "kill":
            continue
        for reason in (r.get("mechanical") or {}).get("kill_reasons") or []:
            m = _RULE_KILL.match(reason)
            if m:
                key, side = m.group(1), rule
            elif reason.startswith(_CALIBRATED):
                key, side = "calibrated", rule
            else:
                # Mechanical reasons lead with their kind: "black for",
                # "frozen for", "probe: ...".
                key = (reason.split(None, 1)[0] if reason
                       else "unknown").rstrip(":")
                side = mech
            side[key] = side.get(key, 0) + 1
    return {"mechanical": mech, "rule": rule}


def _rule_stats(group):
    """Per-rule defect stats across the shot, one example defect each.

    The example is the worst defect deterministically: highest severity,
    then highest confidence, then earliest, then file name."""
    per = {}
    for t in group:
        defects = ((t.get("review") or {}).get("vlm") or {}).get(
            "defects") or []
        fname = _file(t)
        seen = set()
        for d in defects:
            s = per.setdefault(d["rule"], {
                "count": 0, "takes_affected": 0,
                "_sev": [], "_conf": [], "_key": None, "example": None})
            s["count"] += 1
            if d["rule"] not in seen:
                seen.add(d["rule"])
                s["takes_affected"] += 1
            s["_sev"].append(d["severity"])
            if d.get("confidence") is not None:
                s["_conf"].append(d["confidence"])
            key = (-d["severity"], -(d.get("confidence") or 0.0),
                   d["t"], fname)
            if s["_key"] is None or key < s["_key"]:
                s["_key"] = key
                ex = {"file": fname, "t": d["t"],
                      "severity": d["severity"], "note": d.get("note", "")}
                if d.get("t_end") is not None:
                    ex["t_end"] = d["t_end"]
                if d.get("confidence") is not None:
                    ex["confidence"] = d["confidence"]
                s["example"] = ex
    out = {}
    for name in sorted(per):
        s = per[name]
        out[name] = {
            "count": s["count"],
            "takes_affected": s["takes_affected"],
            "mean_severity": round(sum(s["_sev"]) / len(s["_sev"]), 2),
            "mean_confidence": (round(sum(s["_conf"]) / len(s["_conf"]), 2)
                                if s["_conf"] else None),
            "example": s["example"],
        }
    return out


def _survivors(group):
    """Non-kill takes, ranked ones first in rank order."""
    out = []
    for t in group:
        r = t.get("review") or {}
        if r.get("verdict") == "kill":
            continue
        out.append({
            "file": _file(t),
            "take_id": t.get("take_id"),
            "rank_in_shot": r.get("rank_in_shot"),
            "verdict": r.get("verdict"),
            "defects": len(((r.get("vlm") or {}).get("defects")) or []),
        })
    out.sort(key=lambda s: (s["rank_in_shot"] is None,
                            s["rank_in_shot"] or 0, s["file"]))
    return out


def _lineage(group, by_id):
    """Rerun count and longest parent chain. A parent outside the scanned
    set still counts one hop; a visited set stops cycles."""
    reruns, max_depth = 0, 0
    for t in group:
        if not t.get("parent"):
            continue
        reruns += 1
        depth, seen, cur = 0, set(), t
        while cur is not None and cur.get("parent"):
            pid = cur["parent"]
            if pid in seen:
                break
            seen.add(pid)
            depth += 1
            cur = by_id.get(pid)
        max_depth = max(max_depth, depth)
    return {"reruns": reruns, "max_depth": max_depth}


def _recipe_deltas(group):
    """Distinct recipe values across the shot's takes, or None when no
    take carries a recipe block. Values, never survival correlations."""
    with_recipe = [t for t in group if t.get("recipe")]
    if not with_recipe:
        return None
    seeds, models, loras = {}, set(), {}
    for t in with_recipe:
        rec = t["recipe"]
        for node, seed in (rec.get("seeds") or {}).items():
            seeds.setdefault(node, set()).add(seed)
        for m in rec.get("models") or []:
            if m.get("file"):
                models.add(m["file"])
        for lo in rec.get("loras") or []:
            if lo.get("file") and lo.get("strength") is not None:
                loras.setdefault(lo["file"], set()).add(lo["strength"])
    return {
        "n_with_recipe": len(with_recipe),
        "seeds": {node: sorted(v) for node, v in sorted(seeds.items())},
        "models": sorted(models),
        "lora_strengths": {f: sorted(v)
                           for f, v in sorted(loras.items())},
    }


def build(root):
    """The dossier dict: one entry per shot, plus totals."""
    takes = report.find_takes(root)
    by_id = {t["take_id"]: t for t in takes if t.get("take_id")}
    by_shot = {}
    for t in takes:
        by_shot.setdefault(t.get("shot") or "untagged", []).append(t)

    shots = []
    for shot in sorted(by_shot):
        group = by_shot[shot]
        n = len(group)
        kills = sum(1 for t in group
                    if (t.get("review") or {}).get("verdict") == "kill")
        shots.append({
            "shot": shot,
            "n_takes": n,
            "kills": kills,
            "yield": round((n - kills) / n, 3),
            "kill_reasons": _kill_histogram(group),
            "rules": _rule_stats(group),
            "survivors": _survivors(group),
            "lineage": _lineage(group, by_id),
            "recipe": _recipe_deltas(group),
        })
    killed = sum(s["kills"] for s in shots)
    n = len(takes)
    return {"n_takes": n, "kills": killed,
            "yield": round((n - killed) / n, 3) if n else None,
            "shots": shots}


def _hist_line(h):
    return ", ".join("%s %d" % (k, h[k])
                     for k in sorted(h, key=lambda k: (-h[k], k)))


def render(data):
    """The dossier as terminal text, one block per shot."""
    lines = ["%d takes across %d shots: %d killed, yield %.0f%%" % (
        data["n_takes"], len(data["shots"]), data["kills"],
        100 * data["yield"])]
    for s in data["shots"]:
        lines.append("")
        lines.append("%s: %d takes, %d killed, yield %.0f%%" % (
            s["shot"], s["n_takes"], s["kills"], 100 * s["yield"]))
        for side in ("mechanical", "rule"):
            if s["kill_reasons"][side]:
                lines.append("  kills (%s): %s" % (
                    side, _hist_line(s["kill_reasons"][side])))
        ranked = sorted(s["rules"].items(),
                        key=lambda kv: (-kv[1]["count"], kv[0]))
        for name, st in ranked:
            conf = ("" if st["mean_confidence"] is None
                    else ", mean confidence %.2f" % st["mean_confidence"])
            lines.append("  %s: %d defects on %d takes, "
                         "mean severity %.1f%s" % (
                             name, st["count"], st["takes_affected"],
                             st["mean_severity"], conf))
            ex = st["example"]
            when = ("%s-%ss" % (ex["t"], ex["t_end"])
                    if ex.get("t_end") is not None else "%ss" % ex["t"])
            lines.append("    e.g. %s at %s: %s" % (
                ex["file"], when, ex["note"]))
        if s["survivors"]:
            lines.append("  survivors:")
            for sv in s["survivors"]:
                rank = ("#%d " % sv["rank_in_shot"]
                        if sv["rank_in_shot"] else "")
                lines.append("    %s%s %s, %d defects" % (
                    rank, sv["file"], sv["verdict"] or "unreviewed",
                    sv["defects"]))
        if s["lineage"]["reruns"]:
            lines.append("  lineage: %d reruns, max depth %d" % (
                s["lineage"]["reruns"], s["lineage"]["max_depth"]))
        r = s["recipe"]
        if r is None:
            lines.append("  recipes: none recorded")
        else:
            n_seeds = sum(len(v) for v in r["seeds"].values())
            lines.append("  recipes %d/%d: %d distinct seeds, "
                         "%d models, %d loras" % (
                             r["n_with_recipe"], s["n_takes"], n_seeds,
                             len(r["models"]), len(r["lora_strengths"])))
            for f, strengths in r["lora_strengths"].items():
                if len(strengths) > 1:
                    lines.append("    lora %s strengths %s" % (
                        f, ", ".join(str(x) for x in strengths)))
    return "\n".join(lines)
