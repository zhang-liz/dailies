"""Cost telemetry: recorded token usage to dollars.

Prices are data, never code: hosted rates change too often to ship in
a release. The user maintains one JSON price file mapping model names
to dollars per million input/output tokens, plus an optional flat
dollar price per generated clip (a hosted per-clip rate, or a local
$/GPU-hour guess folded down to one clip):

    {"models": {"qwen3-vl": {"input": 0.20, "output": 0.80},
                "big-vlm": {"input": 3.00, "output": 15.00}},
     "clip": 0.05}
"""

import json

MTOK = 1e6


def load(path):
    """Price file to dict, validated now so a typo fails the run
    loudly instead of pricing the night at zero."""
    with open(path) as f:
        prices = json.load(f)
    if not isinstance(prices, dict):
        raise RuntimeError("price file %r must be a JSON object" % path)
    models = prices.get("models")
    if models is not None and not isinstance(models, dict):
        raise RuntimeError(
            'price file %r: "models" must map model name to '
            '{"input": $, "output": $} per million tokens' % path)
    for name, rate in (models or {}).items():
        if not isinstance(rate, dict) or not any(
                isinstance(rate.get(k), (int, float))
                for k in ("input", "output")):
            raise RuntimeError(
                'price file %r: model %r needs numeric "input" and/or '
                '"output" dollars per million tokens' % (path, name))
    clip = prices.get("clip")
    if clip is not None and not isinstance(clip, (int, float)):
        raise RuntimeError(
            'price file %r: "clip" must be dollars per clip' % path)
    return prices


def _usd(usage, rate):
    return (usage.get("prompt_tokens", 0) * (rate.get("input") or 0)
            + usage.get("completion_tokens", 0)
            * (rate.get("output") or 0)) / MTOK


def block(take, prices):
    """The review.cost block for one take: judge spend from recorded
    usage, generation spend from the flat per-clip price. A model with
    usage but no price is named in unpriced_models, never silently
    priced at zero."""
    v = ((take.get("review") or {}).get("vlm")) or {}
    models = prices.get("models") or {}
    vlm_usd = 0.0
    unpriced = set()
    for engine, usage in ((v.get("engine"), v.get("usage")),
                          (v.get("strong_engine"),
                           v.get("strong_usage"))):
        if not usage or not usage.get("calls"):
            continue
        rate = models.get(engine)
        if rate is None:
            unpriced.add(engine)
        else:
            vlm_usd += _usd(usage, rate)
    out = {"vlm_usd": round(vlm_usd, 6)}
    clip_usd = prices.get("clip")
    if clip_usd is not None:
        out["clip_usd"] = clip_usd
    out["total_usd"] = round(vlm_usd + (clip_usd or 0), 6)
    if unpriced:
        out["unpriced_models"] = sorted(e for e in unpriced if e)
    return out
