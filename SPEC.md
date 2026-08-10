# take.json

One sidecar file per generated clip, named `<clip>.take.json`, next to the clip. It is the contract between generation tooling (which records how a take was made) and review tooling (which records what is wrong with it). Any tool can read or write its own block and must preserve the blocks of others.

## Shape

```json
{
  "take_id": "sha256:… content hash of the clip file",
  "shot": "shot-07",
  "parent": "take_id this was rerun or derived from, or null",
  "created": "2026-08-04T21:14:00Z",
  "output": { "file": "shot-07/take-031.mp4", "fps": 24, "frames": 121, "width": 1280, "height": 720 },
  "recipe": {
    "workflow": { "…": "resolved generation graph, verbatim" },
    "models": [ { "file": "wan2.2_t2v_14b_fp8.safetensors", "sha256": "…", "source_hint": "huggingface:Wan-AI/Wan2.2-T2V" } ],
    "loras": [ { "file": "myrna_v3.safetensors", "sha256": "…", "strength": 0.85 } ],
    "conditioning": [ { "role": "reference_image", "sha256": "…" } ],
    "seeds": { "3": 424242 },
    "prompt_text": "flattened positive prompt for cheap access",
    "env": { "comfyui": "0.9.4", "torch": "2.9.1+cu126", "gpu": "RTX 4090" }
  },
  "review": {
    "mechanical": { "black_frames": [], "freeze": [], "scene_cuts": [], "flicker_score": 0.12, "motion_smoothness": 0.94, "candidate_frames": [0.0, 3.2], "kill_reasons": [] },
    "vlm": { "engine": "qwen3-vl", "samples": 3, "defects": [ { "t": 3.2, "t_end": 4.1, "rule": "anatomy.hands", "severity": 3, "confidence": 0.67, "note": "left hand 6 fingers during cup grab" } ], "skipped": [], "unparsed": [], "uncertain": [], "escalated": [], "strong_engine": "big-vlm", "usage": { "calls": 27, "prompt_tokens": 48210, "completion_tokens": 1930, "rules": { "anatomy.hands": { "calls": 3, "prompt_tokens": 5360, "completion_tokens": 214 } } }, "strong_usage": { "calls": 1, "prompt_tokens": 1790, "completion_tokens": 70, "rules": { "anatomy.hands": { "calls": 1, "prompt_tokens": 1790, "completion_tokens": 70 } } } },
    "scrutiny": { "engine": "qwen3-vl", "samples": 3, "scrutinized": [ "anatomy.hands" ], "defects": [], "skipped": [], "unparsed": [], "uncertain": [], "escalated": [ "anatomy.hands" ], "strong_engine": "big-vlm" },
    "verdict": "keep | kill | review",
    "rank_in_shot": 2,
    "cost": { "vlm_usd": 0.0105, "clip_usd": 0.05, "total_usd": 0.0605, "unpriced_models": [] }
  },
  "gold": { "label": "pass | kill", "labeled": "2026-08-09T08:00:00Z" },
  "regen": { "driver": "comfy-driver --host 127.0.0.1:8188", "job": "9b1de6", "submitted": "2026-08-10T02:14:00Z" }
}
```

## Rules

- **Hashes carry an algorithm prefix** (`sha256:`, `blake3:`) so producers and consumers never have to agree on an installed library.
- **`take_id` is the content hash of the output file.** Rename the file and the identity survives; regenerate it and the identity changes. Review results cache on it.
- **`recipe` belongs to generation tooling, `review` to review tooling.** Either block may be null; each tool works alone. A reviewer on a bare folder of clips writes sidecars with `recipe: null`; a recorder without a reviewer leaves `review: null`.
- **Unknown keys are preserved, never stripped.** Tools rewrite a sidecar by reading it, replacing their own block, and writing the rest back untouched.
- **`parent` links a rerun to its source take.** Lineage is the chain of parent pointers; no separate database is required.
- **A defect is one finding, not one frame.** Per-frame repeats of the same rule at contiguous timestamps merge into a single defect; `t_end` (optional) marks the span.
- **Verdicts are honest.** `kill` requires stated reasons. Mechanical checks alone never produce `keep`; that requires eyes or a screening model.
- **Regen survivors face a harder judge.** `review.scrutiny` (optional) appears on a take with a non-empty `parent` chain that passed review: the rubric rules that killed its ancestors, re-judged with at least 3 samples and, when a strong endpoint is configured, forced escalation of every scrutinized rule, confident cheap answers included. `scrutinized` names the rules; the block otherwise matches `vlm`'s shape. A scrutiny defect crossing its rule's `fail_at` kills, and the reason lands in `kill_reasons` in the ordinary rule form so futility counting sees it. Scrutiny uses `fail_at` even in calibrated mode; the conformal false-kill guarantee is stated for first-generation takes only.
- **Adherence is judged against the chain's original prompt.** For a take with a non-empty `parent` chain, prompt-context rules take their `{prompt}` from the root ancestor's `recipe.prompt_text` whenever it differs from the take's own; `vlm.root_prompt: true` (optional) records that the swap happened. The take's `recipe` stays verbatim. A regeneration loop must not be able to pass adherence by patching the hard part of the prompt away.
- **Severity is the rubric's, confidence is the sampler's.** Checklist rules fix each question's severity in the rubric; the judge only answers yes or no. `confidence` (optional) is the yes fraction across repeat samples; a defect below two-thirds agreement may not kill. `uncertain` lists rules whose samples split; `escalated` lists rules re-judged by a stronger model (`strong_engine`).
- **Usage is measured, never estimated.** `vlm.usage` (optional) records what the judge actually consumed: request count and endpoint-reported prompt/completion tokens, total and per rule under `rules`. Tokens are zero when the endpoint reports no usage. `strong_usage` is the same record for the escalation model, kept separate because the two engines are priced differently.
- **Prices are data, dollars are derived.** `review.cost` (optional) is usage priced through a user-supplied price file (per-model dollars per million input/output tokens, optional flat dollars per clip): `vlm_usd` from recorded usage, `clip_usd` copied from the file, `total_usd` their sum. A model with usage but no listed price lands in `unpriced_models` instead of being priced at zero. Rerunning with a new price file rewrites `cost` from the recorded usage; nothing is re-judged.
- **`regen` belongs to resubmission tooling.** (optional) Written on a machine-resubmitted take: the driver command, the driver's opaque job id, and the submission time. The stub sidecar, carrying `parent` and the mutated `recipe`, is written before the clip lands, so a crash mid-generation loses no provenance and a restarted loop reconciles the recorded job id against the driver. The driver contract is [docs/DRIVERS.md](docs/DRIVERS.md).
- **`gold` belongs to the human.** It records the verdict the user actually made (`pass` or `kill`), is written only by explicit labeling (`dailies gold add`), and is the calibration and regression substrate. Review tooling must never write or infer it.

## Status

Draft, versioned by this repository's history. The reference producer and consumer of the `review` block is [dailies](README.md). A JSON Schema for this file is checked in at [dailies/schemas/take.schema.json](dailies/schemas/take.schema.json); `dailies schema take` prints it. This prose stays the contract; the schema mirrors it for machines.
