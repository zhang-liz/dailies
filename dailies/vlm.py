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

SYSTEM = (
    "You are a strict QC inspector for AI-generated video. You are shown "
    "frames sampled from one clip, each labeled with its timestamp in "
    "seconds. Judge only the rule you are given. Respond with JSON only, "
    'no prose: {"defects": [{"t": <timestamp>, "severity": <1-5>, '
    '"note": "<short reason>"}]}. An empty defects list means the clip '
    "passes this rule. Severity 5 is unusable, 1 is barely visible."
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


def _request(endpoint, model, api_key, messages):
    body = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": 0,
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
        else:
            out.append(dict(d))
    out.sort(key=lambda d: d["t"])
    return out


def screen(clip, take, rules, endpoint, model, api_key=None):
    """Run every applicable rubric rule over the clip's candidate frames.
    Returns the take.json "vlm" block."""
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
              "unparsed": []}
    for name, rule in sorted(rules.items()):
        context = rubric_mod.context_for(rule, take)
        if context is None:
            result["skipped"].append(name)
            continue
        prompt = rule["prompt"]
        if context:
            prompt = prompt.replace("{prompt}", str(context))
        content = [{"type": "text",
                    "text": "Rule %r: %s\n%s" % (name, prompt, stamps)}]
        content.extend(images)
        text = _request(endpoint, model, api_key, [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": content}])
        defects = _parse_defects(text)
        if defects is None:
            result["unparsed"].append(name)
            continue
        for d in defects:
            d["rule"] = name
        result["defects"].extend(defects)

    result["defects"] = _merge(result["defects"])
    return result


def kill_reasons(vlm_block, rules):
    """Which defects cross their rule's fail_at threshold."""
    reasons = []
    for d in vlm_block.get("defects", []):
        fail_at = (rules.get(d["rule"]) or {}).get("fail_at")
        if fail_at is not None and d["severity"] >= fail_at:
            when = ("%s-%ss" % (d["t"], d["t_end"])
                    if d.get("t_end") else "%ss" % d["t"])
            reasons.append("%s severity %d at %s: %s" % (
                d["rule"], d["severity"], when, d["note"]))
    return reasons
