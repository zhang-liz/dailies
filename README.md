# dailies

Triage for AI-generated video takes. Batch-generate overnight, wake up to a ranked shortlist: the mechanical funnel kills the dead takes (decode errors, black, frozen) with timestamped reasons and ranks the survivors for review. It never claims a take is good; it claims most of them are definitely dead.

Built so far: stage 1 of the funnel (mechanical, CPU only), stage 2 (VLM screening), and the morning report. Results live in per-clip sidecar files; the format is specified in [SPEC.md](SPEC.md) and is the contract for companion tooling (take lineage and recipe capture are a separate, upcoming tool).

## Requirements

Python 3.9+, ffmpeg and ffprobe on PATH. No Python dependencies; `blake3` is picked up if installed, otherwise hashes are `sha256:`-prefixed.

## Install

```sh
pip install git+https://github.com/zhang-liz/dailies
```

Or run straight from a checkout with `python3 -m dailies`; there is nothing to build.

## Use

```sh
dailies review ./takes                       # or globs, or single files
dailies review shot-07/ --shot shot-07 --json
dailies report ./takes -o report.html
```

Stage 2 screens the survivors with a vision model. Point `--vlm` at any OpenAI-compatible endpoint (llama.cpp, vLLM, or hosted; key read from `DAILIES_VLM_KEY`):

```sh
dailies review ./takes --vlm http://localhost:8000/v1 --vlm-model qwen3-vl
```

Frames are sampled at the mechanical stage's difference peaks plus a sparse uniform strip, so likely artifact moments get attention and no stretch of the clip is invisible to the judge (2 fps is the VideoScore2 sampling optimum, arXiv:2509.22799).

The default rules are checklists: yes/no evidence questions, each carrying the severity a yes implies. VLM judges answer binary questions consistently and pick numbers on a scale badly (VisionReward, arXiv:2412.21059), so the model only says yes or no and where; severity stays in the rubric. Question text follows the published defect taxonomies (GeneVA, arXiv:2509.08818; VideoPhy-2 physics rules, arXiv:2503.06800). Custom rubrics (`--rubric film.json`, or `.yaml` with PyYAML installed) can use questions or a legacy free `prompt` with model-chosen severity; project-specific checks (a prop's continuity, a wardrobe color, no watermarks) need zero code. Defects land in the sidecar with rule, timestamp, severity, and note; a rule kills a take only past its `fail_at`.

### Confidence, cascades

```sh
dailies review ./takes --vlm URL --samples 3
dailies review ./takes --vlm CHEAP_URL --samples 3 --vlm-strong STRONG_URL --vlm-strong-model big-vlm
```

`--samples K` asks the judge every checklist K times; the yes fraction becomes per-defect confidence, and a defect below two-thirds agreement cannot kill, only flag for review (self-consistency, arXiv:2203.11171). With `--vlm-strong`, rules the cheap judge split on are re-judged once by the stronger model, and only those: the cascade spends the expensive model where it earns its price (RouteLLM, arXiv:2406.18665).

### Your verdicts as ground truth

Label takes with the call you actually made, then let the tool learn from you:

```sh
dailies gold add shot-07/take-031.mp4 --label kill
dailies gold add keepers/ --label pass
dailies calibrate ./takes --alpha 0.05      # conformal kill threshold
dailies fit ./takes                          # per-rule weights, your taste
dailies review ./takes --vlm URL --calibration dailies-calibration.json
```

`calibrate` sets the kill threshold by split conformal calibration over your gold-pass takes (Conformal Risk Control, arXiv:2208.02814): under exchangeability, at most an `alpha` fraction of auto-kills are wrong, and the command refuses to print a guarantee it cannot back (19 gold-pass takes minimum at alpha 0.05). `fit` runs a stdlib logistic regression from rule evidence to your kill labels, so ranking follows your taste, not raw severity sums. Both live in `dailies-calibration.json`; recalibrate after changing the judge model, the rubric, or the video generator.

```sh
dailies judge-check ./takes --vlm URL --fail-below 0.6
```

`judge-check` re-judges the frozen gold set without touching sidecars and appends agreement, Cohen's kappa, and false/missed kills to a history file, with the delta against the last run. Run it after every judge or rubric change; it is the answer to "did the new model silently change my kills" (EvalGen, arXiv:2404.12272).

The report is one static HTML file: survivors ranked first per shot, hover a clip to scrub, defect spans marked on a timeline, kill reasons one click away.

### The failure dossier

```sh
dailies brief ./takes
dailies brief ./takes --json
```

`brief` answers "why does shot-07 keep dying" from the sidecars alone: per shot, take and kill counts with yield, a kill-reason histogram split mechanical vs rule, per-rule stats (defect count, takes affected, mean severity and confidence, one example defect with its file and timestamp), the ranked survivors, lineage depth from `parent` chains, and the distinct seeds, models, and lora strengths across takes that carry `recipe` blocks. Pure deterministic aggregation, no LLM calls, and no causal claims: it reports counts, the reasoning stays with you or your agent. Shots without recipes still get a full dossier.

### What a usable take costs

Every judged request's token usage is recorded in the sidecar, total and per rule. Point `--prices` at a price file you maintain and each sidecar gains `review.cost`; the report header, shot headings, and take cards then show spend, ending in the number a night optimizes: dollars per usable (non-kill) take. `report --json` carries the same numbers.

```sh
dailies review ./takes --vlm URL --prices prices.json
```

```json
{"models": {"qwen3-vl": {"input": 0.20, "output": 0.80},
            "big-vlm": {"input": 3.00, "output": 15.00}},
 "clip": 0.05}
```

Model rates are dollars per million input/output tokens; `clip` is an optional flat dollar cost per generated clip (a hosted per-clip rate, or your own $/GPU-hour guess folded down). Prices are data, never code: hosted rates change too often to pin in a release, so the file is yours to edit. A model with usage but no listed price is named in `cost.unpriced_models`, never silently priced at zero. Supplying `--prices` later re-prices recorded usage without re-judging anything.

## The rough cut

After triage, watch the survivors as one file instead of a folder:

```sh
dailies assemble ./takes -o cut.mp4
dailies assemble ./takes -o cut.mp4 --shots reel.txt --alts 1
```

The best non-kill take per shot is normalized to one frame rate and size (the first cut take's, or `--fps`/`--scale`), slated with shot id, short take id, verdict, and the top defect rule when the verdict is review, then joined with the concat demuxer. Shots cut in name order; a `--shots` file (one shot id per line, `#` comments) sets the order instead, with unlisted shots following in name order. `--alts N` appends the next N ranked takes per shot after the best one. A CSV next to the cut maps each segment's record in/out timecodes back to its source file and take id, so acting on what you just watched is one lookup. Concat and slates only, no trims, no audio. Slates need an ffmpeg built with drawtext (libfreetype); without it the cut still assembles, unslated.

## Local judges

Any OpenAI-compatible endpoint works, so open-weight judges trained specifically for generated video plug in with no code: serve [VideoScore2](https://arxiv.org/abs/2509.22799) or [VideoPhy-AutoEval](https://github.com/Hritikbansal/videophy) behind vLLM's OpenAI server and point `--vlm` at it. A GPU-poor setup can run the mechanical funnel alone; it still kills the cheap deaths.

## Honest limits

Automated judgment of generated video tops out around rank correlation 0.66-0.77 against human raters in 2025 evaluations, and VLMs hallucinate worst exactly on synthetic-video physics (VideoHallu, arXiv:2505.01481). dailies is triage that saves review time, not automated quality judgment: it never says a take is good, and the `review` pile exists because a judge that cannot decide should say so. Distribution-level metrics (FVD and successors) are deliberately absent; they compare sets of videos against a reference distribution and say nothing about one take on one morning.

For overnight batches, watch the output directory instead of reviewing after the fact:

```sh
dailies watch ~/ComfyUI/output --report report.html --vlm http://localhost:8000/v1
```

New clips are reviewed as they land (after a settle period so half-written files are left alone), ranks update per shot, and the report is rebuilt after every take, so the morning report exists by morning. Restarting the watcher skips everything already reviewed. Same flags as `review`; `--json` emits one JSON line per take for piping into anything else.

The watcher also runs a doomed-shot circuit breaker: a Beta posterior on each shot's mechanical-kill fraction (mechanical stats only, zero VLM cost) flags a shot as doomed when the posterior puts usable yield below a floor. Eight straight mechanical kills decide fast; one passing take buys several more. Doomed shots are badged in the report header, marked `"shot_doomed"` in `--json` lines (plus one `"event": "doomed"` line when a shot trips), and `--on-doomed CMD` runs `CMD SHOT WORST_SIDECAR` once per shot, for cancelling a queue or paging yourself. Without the hook the flag is report-only, on purpose: it catches only mechanically doomed prompts, and a shot can pass mechanics and still die at the VLM stage.

Each clip gets a `<clip>.take.json` sidecar: content-hash take id, probe info, black/freeze spans, scene cuts, flicker score (motion-masked, so intended action does not read as flicker), motion smoothness (interpolation-reconstruction, the VBench construct on plain ffmpeg), candidate frames for the VLM stage, verdict (`kill` or `review`), rank within the shot. Reviews are cached by content hash; `--force` re-runs. Sidecar blocks owned by other tools (slate's `recipe`) are preserved.

## Machine interface

Every command takes `--json`. For orchestrators that act per take instead of per batch:

```sh
dailies review ./takes --ndjson                  # stream, don't buffer
dailies verdict shot-07/take-031.mp4 --vlm URL   # one clip, one decision
dailies schema take                              # the published contract
```

`review --ndjson` prints one JSON line per clip as it is reviewed, then a final summary line (`{"reviewed": N, "killed": K}`). Per-clip lines share one shape with `watch --json` and `verdict`: `clip`, `shot`, `verdict`, `rank_in_shot`, `kill_reasons`. The summary line has no `clip` key; that is how consumers tell them apart.

`verdict` reviews a single clip under the usual flags (`--vlm`, `--rubric`, `--samples`, `--calibration`), prints that same line, and answers in the exit code, so a regen loop or shell `if` can branch without parsing.

`schema` prints JSON Schema documents checked into `dailies/schemas/` and shipped with the package: `take` (the sidecar, see [SPEC.md](SPEC.md)), `calibration`, `judge-history`.

### Exit codes

| code | meaning |
|------|---------|
| 0 | success; for `verdict`, the take is keep or review |
| 1 | nothing to do or a check failed: no clips found, calibration lacks gold, kappa under `--fail-below` |
| 2 | error: bad usage, ffmpeg or ffprobe missing, VLM endpoint failure |
| 3 | `verdict` only: the take is a kill |

3 is deliberately clear of the shell's conventional 1 (generic failure) and 2 (usage error), so a kill is never confused with a crash.

## Tests

```sh
python3 -m unittest discover tests
```

Tests generate synthetic clips with ffmpeg and assert the funnel's verdicts.
