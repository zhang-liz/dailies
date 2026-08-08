# dailies

Triage for AI-generated video takes. Batch-generate overnight, wake up to a ranked shortlist: the mechanical funnel kills the dead takes (decode errors, black, frozen) with timestamped reasons and ranks the survivors for review. It never claims a take is good; it claims most of them are definitely dead.

Design: `docs/ideas-dailies-slate-mvp.md` at the repo root. Implemented so far: stage 1 of the funnel (mechanical, CPU only), stage 2 (VLM screening), and the morning report.

## Requirements

Python 3.9+, ffmpeg and ffprobe on PATH. No Python dependencies; `blake3` is picked up if installed, otherwise hashes are `sha256:`-prefixed.

## Use

```sh
cd tools/dailies
python3 -m dailies review ./takes            # or globs, or single files
python3 -m dailies review shot-07/ --shot shot-07 --json
python3 -m dailies report ./takes -o report.html
```

Stage 2 screens the survivors with a vision model. Point `--vlm` at any OpenAI-compatible endpoint (llama.cpp, vLLM, or hosted; key read from `DAILIES_VLM_KEY`):

```sh
python3 -m dailies review ./takes --vlm http://localhost:8000/v1 --vlm-model qwen3-vl
```

Frames are sampled at the mechanical stage's difference peaks, not uniformly: artifact frames are frame-difference outliers. Rules come from a rubric (`--rubric film.json`, or `.yaml` with PyYAML installed); each rule is a prompt plus an optional `fail_at` severity, so project-specific checks (a prop's continuity, a wardrobe color, no watermarks) need zero code. Defects land in the sidecar with rule, timestamp, severity, and note; a rule kills a take only past its `fail_at`.

The report is one static HTML file: survivors ranked first per shot, hover a clip to scrub, defect spans marked on a timeline, kill reasons one click away.

Each clip gets a `<clip>.take.json` sidecar: content-hash take id, probe info, black/freeze spans, scene cuts, flicker score, candidate frames for the VLM stage, verdict (`kill` or `review`), rank within the shot. Reviews are cached by content hash; `--force` re-runs. Sidecar blocks owned by other tools (slate's `recipe`) are preserved.

## Tests

```sh
cd tools/dailies && python3 -m unittest discover tests
```

Tests generate synthetic clips with ffmpeg and assert the funnel's verdicts.
