# Regen drivers

A driver is an external executable, not a Python import, on the model of git remote-helpers: the dailies core stays stdlib plus ffmpeg, backends stay arbitrary. Anything that can read JSON on stdin and land a clip in a directory can be a driver: a ComfyUI client, a hosted-API wrapper, a shell script. `dailies regen` (and, later, `watch --regen`) speaks to drivers through exactly two invocations.

## The two calls

Both must exit 0 on success. A non-zero exit, an empty job id, or unparseable status output is a driver failure; dailies raises it to the caller instead of guessing.

### `<driver> submit`

Reads one job object on stdin and prints one job id line on stdout. The id is opaque to dailies but must be unique within a run: the night ledger keys its job table by it. A reused id is stored under a suffixed key so it cannot overwrite an earlier row, and `poll` still receives the id exactly as printed. The driver only has to accept it back in `poll`. Submit should return as soon as the job is queued; the generation happens behind `poll`.

```json
{
  "clip": "/takes/shot-07/take-031-regen-9b1de6a2.mp4",
  "parent": "sha256:… take_id of the failed take",
  "shot": "shot-07",
  "recipe": { "workflow": { "…": "verbatim" }, "seeds": { "3": 918273645 }, "prompt_text": "…" }
}
```

- `clip` is the destination path, chosen by dailies before submission. The driver lands the finished clip there. dailies has already written `<clip>.take.json` with `parent` and the mutated `recipe`, so provenance is on disk even if the driver or the machine dies mid-generation.
- `recipe` is the failed take's recipe copied verbatim except `seeds`, where every entry carries a fresh seed. A parent that recorded no seed nodes gets a single seed under the key `"seed"`; the driver maps it to whatever its backend calls a seed. A parent with `recipe: null` yields a recipe holding only that seed.

### `<driver> poll <id>`

Prints one status object on stdout:

```json
{ "state": "queued | running | done | error", "output": "/takes/shot-07/take-031-regen-9b1de6a2.mp4", "error": "message" }
```

- `state` is required. `queued` and `running` mean poll again; `done` and `error` are terminal, and polling a finished job must keep answering the same terminal state.
- `output` is required once `done`: the path where the clip landed, normally the requested `clip`.
- `error` (optional) says what went wrong when `state` is `error`.
- Unknown keys are tolerated, mirroring the sidecar rule in [SPEC.md](../SPEC.md).

## Sidecar rules

The pre-written stub at `<clip>.take.json` belongs to dailies. After submit succeeds, dailies adds a `regen` block (driver command, job id, submission time) so a crashed loop can reconcile orphaned jobs by polling the recorded id. A driver that writes sidecar data of its own must read-modify-write and preserve every block it does not own, per SPEC.

## Minimal example

The fake driver the tests run against, condensed. It spools jobs to a directory and "generates" by copying a stock clip:

```python
#!/usr/bin/env python3
import json, os, shutil, sys

SPOOL, SRC = "/tmp/spool", "/tmp/stock.mp4"

if sys.argv[1] == "submit":
    job = json.load(sys.stdin)
    jid = "job-%03d" % len(os.listdir(SPOOL))
    json.dump(job, open(os.path.join(SPOOL, jid), "w"))
    print(jid)
else:
    job = json.load(open(os.path.join(SPOOL, sys.argv[2])))
    shutil.copy(SRC, job["clip"])
    print(json.dumps({"state": "done", "output": job["clip"]}))
```

`tests/test_regen.py` holds the full version plus the contract-violation cases. The reference ComfyUI driver (urllib-only: patch seeds by node id, POST `/prompt`, poll `/history`, move output into the watched directory) is planned in [ROADMAP.md](ROADMAP.md); hosted drivers follow with thinner recipes by nature.
