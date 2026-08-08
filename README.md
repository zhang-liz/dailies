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

Frames are sampled at the mechanical stage's difference peaks, not uniformly: artifact frames are frame-difference outliers. Rules come from a rubric (`--rubric film.json`, or `.yaml` with PyYAML installed); each rule is a prompt plus an optional `fail_at` severity, so project-specific checks (a prop's continuity, a wardrobe color, no watermarks) need zero code. Defects land in the sidecar with rule, timestamp, severity, and note; a rule kills a take only past its `fail_at`.

The report is one static HTML file: survivors ranked first per shot, hover a clip to scrub, defect spans marked on a timeline, kill reasons one click away.

Each clip gets a `<clip>.take.json` sidecar: content-hash take id, probe info, black/freeze spans, scene cuts, flicker score, candidate frames for the VLM stage, verdict (`kill` or `review`), rank within the shot. Reviews are cached by content hash; `--force` re-runs. Sidecar blocks owned by other tools (slate's `recipe`) are preserved.

## Tests

```sh
python3 -m unittest discover tests
```

Tests generate synthetic clips with ffmpeg and assert the funnel's verdicts.
