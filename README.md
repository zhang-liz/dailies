# dailies

Triage for AI-generated video takes. Batch-generate overnight, wake up to a ranked shortlist: the mechanical funnel kills the dead takes (decode errors, black, frozen) with timestamped reasons and ranks the survivors for review. It never claims a take is good; it claims most of them are definitely dead.

Design: `docs/ideas-dailies-slate-mvp.md` at the repo root. This is stage 1 (mechanical, CPU only) of the three-stage funnel. VLM screening and the morning report come next.

## Requirements

Python 3.9+, ffmpeg and ffprobe on PATH. No Python dependencies; `blake3` is picked up if installed, otherwise hashes are `sha256:`-prefixed.

## Use

```sh
cd tools/dailies
python3 -m dailies review ./takes            # or globs, or single files
python3 -m dailies review shot-07/ --shot shot-07 --json
```

Each clip gets a `<clip>.take.json` sidecar: content-hash take id, probe info, black/freeze spans, scene cuts, flicker score, candidate frames for the VLM stage, verdict (`kill` or `review`), rank within the shot. Reviews are cached by content hash; `--force` re-runs. Sidecar blocks owned by other tools (slate's `recipe`) are preserved.

## Tests

```sh
cd tools/dailies && python3 -m unittest discover tests
```

Tests generate synthetic clips with ffmpeg and assert the funnel's verdicts.
