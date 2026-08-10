"""Stage 2 of the funnel: VLM screening over candidate frames.

Interface is any OpenAI-compatible chat completions endpoint, so llama.cpp,
vLLM, or a hosted API all work without code changes. One request per rubric
rule; frames are extracted once at the mechanical stage's candidate
timestamps and shared across rules.

Same posture as the mechanical stage: findings and severities, never taste.
A rule kills only when the rubric gives it a fail_at threshold.
"""

import base64
import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.request

from . import rubric as rubric_mod

FRAME_WIDTH = 512
TIMEOUT = 120
# Sampling the judge k times turns disagreement into a confidence
# signal (self-consistency, arXiv:2203.11171). Temperature must be
# nonzero for repeat samples to disagree at all; a defect needs
# CONF_KILL agreement before it may kill.
SAMPLE_TEMPERATURE = 0.7
CONF_KILL = 0.67

SYSTEM = (
    "You are a strict QC inspector for AI-generated video. You are shown "
    "frames sampled from one clip, each labeled with its timestamp in "
    "seconds. Judge only the rule you are given. Respond with JSON only, "
    'no prose: {"defects": [{"t": <timestamp>, "severity": <1-5>, '
    '"note": "<short reason>"}]}. An empty defects list means the clip '
    "passes this rule. Severity 5 is unusable, 1 is barely visible."
)

CHECKLIST_SYSTEM = (
    "You are a strict QC inspector for AI-generated video. You are shown "
    "frames sampled from one clip, each labeled with its timestamp in "
    "seconds. Answer every numbered yes/no question strictly from what "
    "is visible in the frames. Respond with JSON only, no prose: "
    '{"answers": [{"q": <question number>, "yes": true|false, '
    '"t": <timestamp of the clearest evidence>, "note": "<short '
    'reason>"}]}. One entry per question. When the answer is no, t and '
    "note may be omitted. Answer yes only when you can point at the "
    "evidence in a specific frame."
)


class VlmError(RuntimeError):
    pass


def extract_frames(clip, times):
    """JPEG bytes per candidate timestamp, downscaled for token economy."""
    frames = []
    for t in times:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            tmp = f.name
        try:
            r = subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-ss", str(t), "-i", clip,
                 "-frames:v", "1", "-vf", "scale=%d:-2" % FRAME_WIDTH, tmp],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            if r.returncode == 0 and os.path.getsize(tmp) > 0:
                with open(tmp, "rb") as fh:
                    frames.append((t, fh.read()))
        finally:
            os.unlink(tmp)
    return frames


def _request(endpoint, model, api_key, messages, temperature=0):
    body = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }).encode()
    from . import __version__
    req = urllib.request.Request(
        endpoint.rstrip("/") + "/chat/completions",
        data=body, method="POST",
        headers={"Content-Type": "application/json",
                 # Some gateways reject urllib's default agent string.
                 "User-Agent": "dailies/" + __version__})
    if api_key:
        req.add_header("Authorization", "Bearer " + api_key)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.load(resp)
    except urllib.error.URLError as e:
        raise VlmError("VLM endpoint unreachable: %s" % e)
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        raise VlmError("unexpected response shape: %s" % str(data)[:200])


def _parse_defects(text):
    """Model output to defect list. Tolerates code fences and stray prose."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    defects = data.get("defects")
    if not isinstance(defects, list):
        return None
    out = []
    for d in defects:
        try:
            out.append({"t": round(float(d["t"]), 3),
                        "severity": max(1, min(5, int(d["severity"]))),
                        "note": str(d.get("note", ""))[:300]})
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _parse_answers(text):
    """Model output to a yes/no answer list. Tolerates code fences,
    stray prose, and "yes"/"no" strings where booleans belong."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    answers = data.get("answers")
    if not isinstance(answers, list):
        return None
    out = []
    for a in answers:
        if not isinstance(a, dict):
            continue
        try:
            q = int(a["q"])
        except (KeyError, TypeError, ValueError):
            continue
        yes = a.get("yes")
        if not isinstance(yes, bool):
            yes = str(yes).strip().lower() in ("yes", "true", "1")
        try:
            t = round(float(a["t"]), 3)
        except (KeyError, TypeError, ValueError):
            t = None
        out.append({"q": q, "yes": yes, "t": t,
                    "note": str(a.get("note", ""))[:300]})
    return out


def _aggregate_answers(votes, questions, first_t):
    """Answer lists from k samples to (defects, uncertain).

    Severity comes from the rubric's question, never from the model:
    the judge only says yes or no and where. A sample that omits a
    question voted no. A question splits the samples: the rule is
    uncertain. With more than one sample, defects carry the yes
    fraction as confidence."""
    n = len(votes)
    defects, uncertain = [], False
    for qi, q in enumerate(questions, 1):
        yes_votes = [v for v in votes
                     if any(a["q"] == qi and a["yes"] for a in v)]
        p = len(yes_votes) / n
        if 0 < p < 1:
            uncertain = True
        if p < 0.5:
            continue
        first = next(a for a in yes_votes[0]
                     if a["q"] == qi and a["yes"])
        d = {"t": first["t"] if first["t"] is not None else first_t,
             "severity": max(1, min(5, int(q.get("severity", 3)))),
             "note": (first["note"]
                      or str(q["ask"]).split("\n")[-1][:200])}
        if n > 1:
            d["confidence"] = round(p, 2)
        defects.append(d)
    return defects, uncertain


def _defects_from_answers(answers, questions, first_t):
    return _aggregate_answers([answers], questions, first_t)[0]


MERGE_GAP = 0.8


def _merge(defects):
    """Collapse per-frame repeats of one underlying defect into a single
    entry with a time range. Same rule, near-contiguous timestamps: one
    defect. Keeps the highest-severity note; records the span as t_end."""
    defects = sorted(defects, key=lambda d: (d["rule"], d["t"]))
    out = []
    for d in defects:
        last = out[-1] if out else None
        if (last is not None and last["rule"] == d["rule"]
                and d["t"] - last.get("t_end", last["t"]) <= MERGE_GAP):
            last["t_end"] = d["t"]
            if d["severity"] > last["severity"]:
                last["severity"] = d["severity"]
                last["note"] = d["note"]
            if d.get("confidence") is not None:
                last["confidence"] = max(d["confidence"],
                                         last.get("confidence") or 0)
        else:
            out.append(dict(d))
    out.sort(key=lambda d: d["t"])
    return out


def screen(clip, take, rules, endpoint, model, api_key=None, samples=1):
    """Run every applicable rubric rule over the clip's candidate frames.
    Returns the take.json "vlm" block. With samples > 1, checklist rules
    are asked that many times and disagreement becomes confidence."""
    mech = ((take.get("review") or {}).get("mechanical") or {})
    times = mech.get("candidate_frames") or [0.0]
    frames = extract_frames(clip, times)
    if not frames:
        raise VlmError("could not extract any frames from %s" % clip)

    images = [
        {"type": "image_url", "image_url": {"url":
            "data:image/jpeg;base64," + base64.b64encode(jpg).decode()}}
        for _, jpg in frames]
    stamps = "Frame timestamps in order: %s seconds." % (
        ", ".join(str(t) for t, _ in frames))

    result = {"engine": model, "defects": [], "skipped": [],
              "unparsed": [], "uncertain": []}
    if samples > 1:
        result["samples"] = samples
    for name, rule in sorted(rules.items()):
        context = rubric_mod.context_for(rule, take)
        if context is None:
            result["skipped"].append(name)
            continue
        questions = rule.get("questions")
        if questions:
            lines = ["Rule %r. Answer every question from the frames."
                     % name]
            for i, q in enumerate(questions, 1):
                ask = str(q["ask"])
                if context:
                    ask = ask.replace("{prompt}", str(context))
                lines.append("%d. %s" % (i, ask))
            system = CHECKLIST_SYSTEM
            text = "%s\n%s" % ("\n".join(lines), stamps)
        else:
            prompt = rule["prompt"]
            if context:
                prompt = prompt.replace("{prompt}", str(context))
            system = SYSTEM
            text = "Rule %r: %s\n%s" % (name, prompt, stamps)
        content = [{"type": "text", "text": text}]
        content.extend(images)
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": content}]

        if questions:
            k = max(1, samples)
            temperature = 0 if k == 1 else SAMPLE_TEMPERATURE
            votes = []
            for _ in range(k):
                answers = _parse_answers(_request(
                    endpoint, model, api_key, messages,
                    temperature=temperature))
                if answers is not None:
                    votes.append(answers)
            if not votes:
                result["unparsed"].append(name)
                continue
            defects, uncertain = _aggregate_answers(
                votes, questions, frames[0][0])
            if uncertain:
                result["uncertain"].append(name)
        else:
            # Legacy severity rules stay single-shot; their model-chosen
            # severities have no clean vote to aggregate.
            defects = _parse_defects(_request(
                endpoint, model, api_key, messages))
            if defects is None:
                result["unparsed"].append(name)
                continue
        for d in defects:
            d["rule"] = name
        result["defects"].extend(defects)

    result["defects"] = _merge(result["defects"])
    return result


def kill_reasons(vlm_block, rules):
    """Which defects cross their rule's fail_at threshold. A defect the
    samples disagreed on (confidence below CONF_KILL) may not kill; it
    stays a finding for the review pile."""
    reasons = []
    for d in vlm_block.get("defects", []):
        fail_at = (rules.get(d["rule"]) or {}).get("fail_at")
        if fail_at is None or d["severity"] < fail_at:
            continue
        if (d.get("confidence") is not None
                and d["confidence"] < CONF_KILL):
            continue
        when = ("%s-%ss" % (d["t"], d["t_end"])
                if d.get("t_end") else "%ss" % d["t"])
        reasons.append("%s severity %d at %s: %s" % (
            d["rule"], d["severity"], when, d["note"]))
    return reasons
