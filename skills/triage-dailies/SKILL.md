---
name: triage-dailies
description: Drive the dailies morning triage ritual over an overnight batch of AI-generated video takes. Summarize survivors and kills per shot, look at the defect frame behind every uncertain verdict, record the human's rulings as gold labels, recalibrate when the gold set can carry it, and turn recurring misses into rubric rules. Use when the user says /morning-triage, "morning triage", "triage the overnight batch", "review last night's takes", or "run dailies on the batch".
---

# triage-dailies

Drive the dailies morning ritual: summarize the batch, look at the evidence behind every doubtful verdict, get the human's ruling, bank it as gold, and keep the judge calibrated.

## Ground rules

- You recommend; the human rules. **Never run `dailies gold add` without an explicit human ruling on that specific take. Never derive a label from the judge's verdict, from your own recommendation, or from the user's silence. The `gold` block belongs to the human (SPEC.md); review tooling and agents must never write or infer it.**
- The JSON shapes below are hard-coded from `cli.py` and pinned by `tests/test_skill_envelope.py`. Trust them over any prose.
- All state lives in `<clip>.take.json` sidecars next to the clips (shape in SPEC.md). Write through the CLI, never by editing sidecars; sidecar tools must preserve keys they do not own.

## 1. Get the batch

If an overnight `dailies watch` already reviewed everything, read the existing `*.take.json` sidecars under the takes directory. Otherwise run the review (add the VLM flags the user normally uses):

    dailies review <dir> --json
    dailies review <dir> --json --vlm URL --vlm-model M --samples 3 --vlm-strong URL2
    dailies review <dir> --json --vlm URL --calibration dailies-calibration.json

Envelope, from `cmd_review` in cli.py:

    {"reviewed": <int>, "killed": <int>, "takes": [<take>, ...]}

Each entry in `takes` is a full take.json sidecar. The paths this ritual reads:

    take["shot"]                                      shot id, may be null
    take["output"]["file"]                            clip filename
    take["review"]["verdict"]                         "keep" | "kill" | "review"
    take["review"]["rank_in_shot"]                    1 is best in shot; null before ranking
    take["review"]["mechanical"]["kill_reasons"]      list of strings, why it died
    take["review"]["mechanical"]["candidate_frames"]  list of seconds
    take["review"]["vlm"]                             null until stage 2 ran, else:
      {"engine": <str>,
       "defects": [{"t": <sec>, "t_end": <sec, optional>, "rule": <str>,
                    "severity": <1..5>, "confidence": <0..1, optional>,
                    "note": <str>}],
       "skipped": [<rule>], "unparsed": [<rule>], "uncertain": [<rule>],
       "samples": <int, optional>, "escalated": [<rule>, optional],
       "strong_engine": <str, optional>}

Exit codes: 0 reviewed, 1 no clips found, 2 ffmpeg or VLM error.

## 2. Summarize per shot

Group takes by `shot`. For each shot report: survivors (verdict `review` or `keep`) ordered by `rank_in_shot`, then kills with their `kill_reasons` verbatim. Close with totals: reviewed, killed, left for eyes.

## 3. Look at the evidence

For every take whose verdict is `review`, and for every rule name in `review.vlm.uncertain`:

1. Find that rule's entry in `review.vlm.defects` and take its `t`. An uncertain rule may have no defect entry (the yes votes lost); fall back to the first `candidate_frames` timestamp.
2. Extract the frame at the defect t:

       ffmpeg -v error -y -ss <t> -i <clip> -frames:v 1 -vf scale=512:-2 <tmp>.jpg

3. Read the image and actually look at it.
4. Recommend `pass` or `kill`, with the evidence: rule, t, severity, confidence, the judge's note, and what you can see in the frame. When the frame contradicts the judge, say so; second-guessing the judge is the point of looking.

## 4. Bank the ruling

The moment the human rules on a take, record it, one take at a time, never batched to the end of the session:

    dailies gold add <clip> --label pass --json
    -> {"labeled": ["<clip>", ...], "label": "pass"}

`--label` takes only `pass` or `kill`. No ruling, no label; this rule has no exceptions.

## 5. Recalibrate when the gold set can carry it

After labeling, count the gold set:

    dailies gold list <dir> --json
    -> {"gold": [{"clip": "<path>", "label": "pass" | "kill"}, ...]}

The conformal quantile needs ceil((1 - alpha) / alpha) gold-pass takes: 19 at the default alpha 0.05 (also reported as `"needed"` when calibrate refuses). When the pass count crosses that threshold, proactively run both, without waiting to be asked:

    dailies calibrate <dir> --json
    -> {"alpha": <float>, "lambda": <float or null>, "n_pass": <int>,
        "n_kill": <int>, "created": <ts>, "kill_recall": <float, optional>,
        "needed": <int, only when lambda is null>}

    dailies fit <dir> --json
    -> the calibration file after merge; fit adds
       {"weights": {<rule>: <float>}, "bias": <float>,
        "n_fit": <int>, "fit_accuracy": <float>}

Report `lambda` (the kill threshold, with its guarantee: at most an alpha fraction of auto-kills wrong on takes like the gold set) and the top weights sorted by absolute value, which say which rules actually predict this user's kills. `fit` needs at least 4 gold-labeled takes with VLM reviews. Then suggest running future reviews with `--calibration dailies-calibration.json`, and recalibrating after any judge, rubric, or generator change.

## 6. Recurring misses become rubric rules

When the user names a defect the judge keeps missing (or keeps flagging wrongly), draft a rule in rubric.py's shape:

    {
      "continuity.hat": {
        "questions": [
          {"ask": "Does the red hat vanish or change color between frames?",
           "severity": 4}
        ],
        "fail_at": 4
      }
    }

- Questions are yes/no evidence questions; each carries the severity a yes implies. The judge never picks severities.
- `fail_at` is the severity at which a confident yes kills; `null` means flag only.
- A rule needing prompt context declares `"needs": "recipe.prompt_text"` and references it as `{prompt}` inside the ask; the rule is skipped for takes without that context.
- A `--rubric` file replaces the built-in rules wholly. To keep the defaults, dump them first and add the new rule to the dump:

      python3 -c "import json; from dailies import rubric; print(json.dumps(rubric.DEFAULT, indent=2))" > film.json

Show the drafted rule to the user, then re-review with it (`--force`, because cached VLM results are otherwise skipped):

    dailies review <dir> --json --vlm URL --rubric film.json --force
