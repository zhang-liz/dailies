---
name: triage-dailies
description: Drive the dailies morning triage ritual over an overnight batch of AI-generated video takes. Check judge health, open with the failure dossier and cost, review audited regen takes first, look at multi-frame evidence behind every doubtful verdict, record the human's rulings as gold labels, surface judge disagreement patterns, recalibrate when the gold set can carry it, and close with a plan for tonight. Use when the user says /morning-triage, "morning triage", "triage the overnight batch", "review last night's takes", "what happened overnight", or "run dailies on the batch".
---

# triage-dailies

Drive the dailies morning ritual: report what the night produced and what it cost, look at the evidence behind every doubtful verdict, get the human's ruling, bank it as gold, keep the judge calibrated and honest, and end with a concrete plan for tonight.

## Ground rules

- You recommend; the human rules. **Never run `dailies gold add` without an explicit human ruling on that specific take. Never derive a label from the judge's verdict, from your own recommendation, or from the user's silence. The `gold` block belongs to the human (SPEC.md); review tooling and agents must never write or infer it.**
- The JSON shapes below are hard-coded from `cli.py` and pinned by `tests/test_skill_envelope.py`. Trust them over any prose.
- All state lives in `<clip>.take.json` sidecars next to the clips (shape in SPEC.md). Write through the CLI, never by editing sidecars; sidecar tools must preserve keys they do not own.

## 1. Judge health first

Before trusting any verdict, read `dailies-judge-check.json` (in the takes directory, else the working directory). Shape:

    {"runs": [..., {"engine": <str>, "n": <int>, "agreement": <float>,
                    "kappa": <float>, "false_kills": <int>,
                    "missed_kills": <int>, "created": <ts>}]}

The last run is the judge's report card. Flag it when: the file is missing, kappa is below 0.6 (tonight's `watch --regen` will refuse to start), or the newest run predates a judge model, rubric, or generator change the user has mentioned. When the gold set has grown or the judge changed, offer to run it (it re-judges the labeled takes, costs VLM calls, writes no sidecars):

    dailies judge-check <dir> --vlm URL --json

## 2. Get the batch

If an overnight `dailies watch` already reviewed everything, read the existing `*.take.json` sidecars under the takes directory. Otherwise run the review (add the VLM flags the user normally uses):

    dailies review <dir> --json
    dailies review <dir> --json --vlm URL --vlm-model M --samples 3 --vlm-strong URL2
    dailies review <dir> --json --vlm URL --calibration dailies-calibration.json --prices prices.json

Envelope, from `cmd_review` in cli.py:

    {"reviewed": <int>, "killed": <int>, "takes": [<take>, ...]}

Each entry in `takes` is a full take.json sidecar. The paths this ritual reads:

    take["shot"]                                      shot id, may be null
    take["parent"]                                    take_id of the take this reran, or null
    take["output"]["file"]                            clip filename
    take["review"]["verdict"]                         "keep" | "kill" | "review"
    take["review"]["rank_in_shot"]                    1 is best in shot; null before ranking
    take["review"]["mechanical"]["kill_reasons"]      list of strings, why it died
    take["review"]["mechanical"]["candidate_frames"]  list of seconds
    take["review"]["cost"]                            optional: {"vlm_usd": <float>, "total_usd": <float>, ...}
    take["review"]["audit"]                           optional: {"rate": <float>}, see step 4
    take["review"]["scrutiny"]                        optional: the harder re-judge of a passing regen take
    take["review"]["vlm"]                             null until stage 2 ran, else:
      {"engine": <str>,
       "defects": [{"t": <sec>, "t_end": <sec, optional>, "rule": <str>,
                    "severity": <1..5>, "confidence": <0..1, optional>,
                    "note": <str>}],
       "skipped": [<rule>], "unparsed": [<rule>], "uncertain": [<rule>],
       "samples": <int, optional>, "escalated": [<rule>, optional],
       "strong_engine": <str, optional>}

Exit codes: 0 reviewed, 1 no clips found, 2 ffmpeg or VLM error.

## 3. Open with the dossier and the bill

Do not hand-build the summary; the tool computes it:

    dailies brief <dir> --json

Shape: `{"n_takes", "kills", "pending", "yield", "shots": [<shot>, ...]}`; each shot carries `shot`, `n_takes`, `pending`, `kills`, `yield`, `kill_reasons` (histogram, mechanical vs rule), `rules` (per-rule count, takes affected, mean severity, mean confidence, one example defect), `survivors` (ranked), `lineage`, `recipe` (seed/model/lora deltas when recipes exist).

Report per shot: yield, the dominant kill rule with its example, survivors in rank order. Then one cost line when sidecars carry `review.cost`: total spend and dollars per usable (non-kill) take, compared against the previous morning if the user has one on record. A shot at 0% yield with one rule at full confidence is a prompt or model problem, not a seed problem; say so in the summary, it becomes tonight's plan in step 9.

## 4. Audit lane first

Takes with `review.audit` are regen survivors the defense layer deliberately routed to human eyes: the loop passed them, and a hash-picked slice lands here so a judge fooling itself gets caught by the human. Triage these before everything else, and tell the user why they are in the pile. Their gold labels matter double: they feed `fit` and `calibrate`, and rising human disagreement on audited takes is the alarm that the regen loop found a judge blind spot; if today's audited takes keep drawing kill rulings, say that plainly and recommend pausing `--regen` until after a judge-check.

A take with `review.scrutiny` already survived a harder bench (its ancestors' killing rules, extra samples, forced strong-model escalation); mention that context when presenting it.

## 5. Look at the evidence

For every take whose verdict is `review`, and for every rule name in `review.vlm.uncertain`:

1. Find that rule's entry in `review.vlm.defects` and take its `t`. An uncertain rule may have no defect entry (the yes votes lost); fall back to the first `candidate_frames` timestamp.
2. A single frame cannot show a vanish, a morph, or a pop-in. Extract a three-frame strip around the moment and look at change, not a still:

       for T in <t-0.3> <t> <t+0.3>; do
         ffmpeg -v error -y -ss $T -i <clip> -frames:v 1 -vf scale=512:-2 <tmp>-$T.jpg
       done

   Clamp t-0.3 at 0. Read all three images in order.
3. For motion defects (physics.motion, jerky movement, rubber-banding), a strip is still too coarse; sample a second of footage at 6 fps and read the sequence:

       ffmpeg -v error -y -ss <t-0.5> -t 1 -i <clip> -vf fps=6,scale=384:-2 <tmp>-%02d.jpg

4. Recommend `pass` or `kill`, with the evidence: rule, t, severity, confidence, the judge's note, and what you can see across the frames. When the frames contradict the judge, say so; second-guessing the judge is the point of looking.

## 6. Bank the ruling

The moment the human rules on a take, record it, one take at a time, never batched to the end of the session:

    dailies gold add <clip> --label pass --json
    -> {"labeled": ["<clip>", ...], "label": "pass"}

`--label` takes only `pass` or `kill`. No ruling, no label; this rule has no exceptions.

## 7. Surface disagreement patterns

After banking, compare the human's gold labels against the judge's verdicts, per rule:

    dailies gold list <dir> --json
    -> {"gold": [{"clip": "<path>", "label": "pass" | "kill"}, ...]}

Read each labeled take's sidecar for the verdict and the rules behind it. Two patterns are worth raising unprompted, with counts:

- **The judge kills, the human passes, same rule, three or more times**: that rule is over-firing for this user. Offer the menu: refit (`dailies fit` will down-weight it), soften the rule (lower a question's severity or drop `fail_at` in a custom rubric), or leave it and keep overruling.
- **The human kills what the judge passed, same visible defect class, twice or more**: the judge has a blind spot. Offer to draft a rubric rule for it (step 8 shape) without waiting to be asked.

## 8. Recurring misses become rubric rules

When the user names a defect the judge keeps missing (or step 7 finds one), draft a rule in rubric.py's shape:

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

Show the drafted rule to the user, then re-review with it (`--force`, because cached VLM results are otherwise skipped). A rubric change stales the calibration and the judge-check history; say so and offer both re-runs.

    dailies review <dir> --json --vlm URL --rubric film.json --force

## 9. Recalibrate when the gold set can carry it

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

## 10. Close with tonight's plan and the cut

End the ritual with decisions, not a recap:

- **Per dead shot**, the fix the dossier implies: one rule at full confidence across every take means change the prompt or the model (quote the rule's example defect); scattered low-confidence kills mean seeds may still win, so more takes or `--regen`. A shot the futility rule blocked (three distinct-seed kills, same rule) is already named needs-human in the ledger; relay that verdict.
- **Per surviving shot short of its quota**, the `--want` line: `dailies watch <dir> --regen DRIVER --want shot-07=3 ...`, with `--dry-run` recommended for a first regen night and a reminder that the judge-health gate from step 1 must be green.
- **When any shot has survivors**, offer the rough cut:

      dailies assemble <dir> -o cut.mp4
      -> cut.mp4 plus cut.csv mapping each segment back to its source take

  One watchable file beats a folder; the CSV row is the path back to any take the user reacts to.
