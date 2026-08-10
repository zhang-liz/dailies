# dailies roadmap

The ranking points one direction: dailies stops being a morning report and becomes the decision layer of an unattended overnight loop. The next month hardens the machine contract and removes the morning glue using code that already exists behind --json. The phase after closes the generate, triage, regenerate loop, with the safety work shipping in the same release as the loop because an uncapped, unaudited regen loop is a Goodhart machine and a money fire. Everything later mines the sidecar corpus those first two phases produce. Three constraints hold throughout: the filesystem stays the only bus, the core stays stdlib plus ffmpeg, and every statistical claim keeps the honest-limits wording the tool trades on.

## Now (next 2 to 4 weeks)

### 1. Claude Code skill: /morning-triage
**What.** A `triage-dailies` skill (SKILL.md in the repo, installable to ~/.claude/skills) that drives the whole morning ritual through an agent.
**Pain.** Morning triage is still manual glue: run review, read the report, squint at the review pile, remember to gold-label, remember to recalibrate.
**Mechanism.** The skill encodes the workflow the README implies: run `dailies review --json` or read existing sidecars; summarize survivors and kills per shot; for every `review` verdict and every rule in vlm.uncertain, extract the defect frame at defect t and view it, then recommend a call with the evidence; on the user's ruling run `dailies gold add` immediately; when gold count crosses calibrate's needed threshold, proactively run calibrate and fit and report the new lambda and top weights; when the user names a recurring miss, draft a rubric rule in rubric.py's questions/severity/fail_at shape and re-review with --rubric. Envelope shapes are hard-coded from cli.py, never scraped from prose.
**Effort.** Days. Zero core code.
**Dependencies.** None. Add one test pinning the documented envelope shapes so cli.py cannot drift under the skill.
**Risks.** The agent must never gold-label without a human ruling; the instructions forbid it, matching SPEC's rule that gold belongs to the human. Envelope drift is the failure mode the pinned test exists for.

### 2. dailies brief: per-shot failure dossiers
**What.** `dailies brief [dir] --json`, pure deterministic aggregation over sidecars, no LLM.
**Pain.** Anyone deciding what to change for tonight's batch has to re-derive failure patterns from dozens of sidecars. Nothing answers "why does shot-07 keep dying" in one machine-readable place.
**Mechanism.** Same walk as report.build, JSON target. Per shot: n_takes, kills, yield; kill_reasons histogram split mechanical vs rule; per-rule stats with count, takes affected, mean severity and confidence, one example defect; survivors ranked; lineage depth via parent chains and whether reruns improved; recipe deltas across takes (seeds, lora strengths, models) when recipe blocks exist. The reasoning stays in the agent or the human; dailies supplies the evidence table.
**Effort.** Days.
**Dependencies.** None. Recipe deltas only get useful once the ComfyUI node pack or slate writes recipe blocks; the dossier must stay useful with recipe null, which most sidecars will be.
**Risks.** With 12 takes per shot, seed-vs-survival patterns are noise: report counts, never causes. Shot grouping relies on pipeline.shot_for's directory heuristic, which mislabels flat output dirs.

### 3. Doomed-shot circuit breaker
**What.** A per-shot sequential monitor in watch.py that flags a prompt as doomed before it burns thirty more takes.
**Pain.** The biggest overnight cost is generating dozens of takes from a configuration that was never going to work, discovered at breakfast.
**Mechanism.** After each review, update a Beta posterior on the shot's mechanical-kill fraction (mechanical stats only, zero VLM cost); when the posterior puts usable yield below a floor after n takes (eight straight mechanical kills decides fast), mark the shot doomed. Actions: a `doomed` flag in the report header, an opt-in `--on-doomed CMD` hook receiving the shot id and worst sidecar, and a JSON line in --json mode.
**Effort.** Days.
**Dependencies.** None; it delivers value to users who never adopt regen. When the night ledger ships, its futility stop reuses this monitor: one futility mechanism, not two.
**Risks.** It catches only mechanically doomed prompts; a shot can pass mechanically and die at the VLM stage, and the report says so. The hook could cancel a healthy queue, so flag-only is the default.

### 4. dailies assemble: the morning rough cut
**What.** `dailies assemble ./takes -o cut.mp4`: best passing take per shot, slated and joined into one watchable file.
**Pain.** After triage comes an hour of hand-assembling survivors into something screenable. Directors think in cuts, not file lists.
**Mechanism.** Pick rank_in_shot == 1 among non-kill takes per shot, order by an optional shot-list file or shot name, normalize fps and scale, burn a drawtext slate per take (shot id, short take_id, verdict, top defect rule when verdict is review), join with the concat demuxer. `--alts N` appends the next N ranked takes per shot. Writes an EDL/CSV mapping cut timecodes back to files; the report links the cut. All ffmpeg plus stdlib.
**Effort.** Days. Testable with the existing synthetic-clip harness.
**Dependencies.** None.
**Risks.** Mixed resolutions and fps force a re-encode pass. Ordering convention without a shot list must be documented. Scope creep toward an NLE is the trap: concat and slates only, no trims, no audio.

### 5. Headless orchestrator contract
**What.** NDJSON streaming, published JSON Schemas, and a single-take `dailies verdict` gate.
**Pain.** Orchestrators need a stable per-take machine contract; today only watch emits per-take lines, review buffers one blob, exit codes are folk knowledge, and no schema is published.
**Mechanism.** (a) `--ndjson` on review: one line per clip as review_clip returns, sharing watch.py's emit serializer, with a final summary line. (b) `dailies verdict <clip> --json`: one clip in, one decision out; exit 0 keep or review, 3 kill, 2 error. This is the primitive every regen loop and orchestrator if-statement needs. (c) `dailies schema [take|event|calibration|judge-history]` printing schema files checked into the repo and validated against synthesized sidecars in tests. Document the exit-code table in the README.
**Effort.** Days. This formalizes behavior the code already exhibits.
**Dependencies.** None, and everything else stands on it: the skill's pinned envelopes, the MCP server, the regen drivers, watch --regen. Build first within this group even though pain ranks it fifth.
**Risks.** Freezing a schema means take.json changes need a schema_version field; tolerate-unknown-keys is already SPEC law, so forward compatibility is cheap. Exit 3 must be documented against shell conventions.

### 6. Cost telemetry
**What.** A costs block in the sidecar and dollars-per-usable-take in the report.
**Pain.** Users spend money every night with no number for what one usable take costs, so nothing downstream can be optimized.
**Mechanism.** vlm.py's `_request` already parses the response JSON and drops `usage` on the floor; return it and accumulate per-rule call counts and tokens into the vlm block. A user-editable price table maps judge models to $/Mtok and generators to $/clip; review_clip writes `review.cost`. The report gains a header strip ending in the headline number: total $ divided by non-kill takes, per shot and per night. Same numbers in --json.
**Effort.** Days. Additive under SPEC's unknown-key rule.
**Dependencies.** None. Prerequisite for the ledger's spend caps, compare, tune, and the budget governor.
**Risks.** Local GPU cost needs a user-supplied $/GPU-hour assumption. Hosted prices change often, so the table stays data, never code.

## Next

This is one coordinated release: the regen loop (drivers, ledger, filesystem integration) ships together with its defense layer, never with the defense as a fast-follow. The ComfyUI node pack lands beside it as the recipe producer.

### 1. Close the loop through the filesystem: watch --regen
**What.** Regeneration as one more producer feeding the existing watcher. No daemon, no RPC.
**Pain.** dailies can call a take dead at 2am and nothing acts on it; the user wakes to a mostly wasted night of GPU time. Highest pain on the list.
**Mechanism.** watch.py's emit path already fires after each fresh review. Add a hook: on verdict kill, and shot not complete or blocked in the ledger, consult the policy, produce the mutated recipe, submit via the configured driver, non-blocking. The driver drops the new clip into the watched directory, and settle detection, content-hash caching, sidecar writes, rerank, and report rebuild apply with near-zero new code. Land, review, kill, mutate, submit, land. Ctrl-c loses nothing because sidecars plus the ledger are the whole state. `--dry-run` prints intended mutations for the first night of trust-building.
**Effort.** Days once the drivers and ledger exist.
**Dependencies.** Regen drivers and the night ledger; the verdict/NDJSON contract from Now.
**Risks.** A misconfigured driver that errors instantly could spin submit-fail loops: cap submits per minute and block the shot after consecutive driver failures. Fast hosted generation can outrun VLM review; the ledger's attempt caps bound the damage.

### 2. Regen drivers as subprocess plugins
**What.** A driver is an external executable honoring a stdin/stdout contract, not an import. ComfyUI driver first, hosted (Seedance, Hailuo) later.
**Pain.** Same wasted-night pain as above; this is the piece that actually resubmits work.
**Mechanism.** `dailies regen` invokes `<driver> submit` with a mutated recipe JSON on stdin; the driver prints a job id, and `<driver> poll <id>` returns status and output path. The ComfyUI reference driver is urllib-only: it takes recipe.workflow (stored verbatim per SPEC), patches seeds by node id and any workflow_patch ops, POSTs to /prompt, polls /history, and moves output into the watched directory. Every driver writes the new clip's sidecar stub with parent set to the failed take_id and the mutated recipe before the file lands, so provenance survives a crash. The pattern is the git remote-helper model: core stays clean, backends stay arbitrary.
**Effort.** A week covers the contract plus the ComfyUI driver only. Hosted drivers follow.
**Dependencies.** The verdict contract (Now item 5). Full ComfyUI resubmission needs recipe.workflow in sidecars, which the node pack below writes; hosted recipes are thinner (seed and prompt only) by nature.
**Risks.** Driver crashes mid-poll orphan jobs; the ledger's job table must reconcile on restart. Two processes writing one sidecar needs read-modify-write under SPEC's preserve-other-blocks rule, ideally with an fcntl lock.

### 3. The night ledger
**What.** Crash-safe per-run state with budget caps, futility stops, and best-of-k shot completion. Ships with the drivers, never after.
**Pain.** An unattended loop will happily spend all night on a doomed prompt or overshoot a shot that already has enough good takes.
**Mechanism.** One `dailies-night.json` per run, written atomically like sidecars: per-shot state (wanted k, passing, attempts, spend, status) plus a job table mapping driver job ids to parent takes and mutations. Stopping rules: shot completion at k non-kill takes with queued-job cancellation (`--want shot-07=3`); a lineage cap per parent chain (default 4); the three-distinct-seeds futility test (the same rule killing three takes with distinct seeds under one prompt proves it is not seed luck), reusing the circuit breaker's monitor; global attempt and spend caps fed by cost telemetry. Blocked shots surface in the morning report as needs-human with the recurring defect class named. On restart, reconcile the job table against driver poll and the watched directory.
**Effort.** Days.
**Dependencies.** Ships with the drivers and watch --regen. Spend caps need cost telemetry from Now.
**Risks.** Local GPU cost accounting is approximate (wall clock as proxy). Takes generated by an adaptive policy are not exchangeable with the calibration set, so the conformal false-kill guarantee weakens on regen takes; the report labels the guarantee as applying to first-generation takes.

### 4. Regen-to-green defense
**What.** Escalated scrutiny, audit sampling, and a judge-health gate. Same release as watch --regen, not optional.
**Pain.** A loop that optimizes takes until the judge says yes converges on takes that fool the judge, exactly where VLMs hallucinate worst. A fake keep in the cut is worse than a kill.
**Mechanism.** Three layers on shipped machinery. (1) Asymmetric scrutiny: a take passing on attempt two or later is never accepted at cheap-judge confidence; the rule that killed its parent is re-judged with k samples and forced escalation to the strong model. This absorbs the targeted re-ask from the lineage-aware review idea. (2) Audit sampling: a configurable share of auto-passed regen takes (default 10 to 20 percent) land in `review` badged audit; morning gold labels on them flow into fit and calibrate, and rising disagreement on audited takes is the alarm that the loop found a blind spot. (3) Judge-health gate: watch --regen refuses to start, or drops to triage-only, when the last judge-check kappa is below threshold or calibration predates a judge, rubric, or generator change. Plus the intent guard: adherence is always judged against the original prompt_text, never a patched prompt.
**Effort.** A week.
**Dependencies.** The regen loop itself; judge-check history and the escalation path already exist.
**Risks.** Audit sampling taxes the morning-review time the tool exists to save; the rate is tunable and can decay as audited agreement holds. Strong-model escalation costs tokens. Correlated blind spots across both judges are irreducible; the human audit lane can shrink but never reach zero.

### 5. comfyui-dailies node pack
**What.** Custom nodes putting triage and recipe capture inside the generation graph. Declare it slate's first form to resolve the scope overlap.
**Pain.** ComfyUI is the primary user environment per the README, and nothing in the graph kills dead takes at generation time or writes the recipe block SPEC reserves.
**Mechanism.** Four nodes, importing dailies in-process since it is stdlib-only. `DailiesReview` (mechanical-only by default so the worker thread never blocks on a VLM; VLM optional or offloaded to a companion watch). `DailiesGate` routes on verdict so upscale and interpolate run only on survivors. `DailiesRecipeWriter` hooks prompt and extra_pnginfo to write the recipe block via load-merge-save: workflow verbatim, model and lora hashes, seeds by node id, flattened prompt_text, which activates the currently skipped adherence.prompt rule. `DailiesRerun` re-enqueues via /prompt with a fresh seed, parent set, and a max-retries widget; it should consult the ledger for its budget.
**Effort.** A week understates the ComfyUI testing burden; plan more.
**Dependencies.** None hard, but it is the recipe producer that brief's deltas, forensics, compare, and full driver resubmission all starve without. Coordinate with slate rather than duplicating it.
**Risks.** ComfyUI API churn around prompt introspection is a standing tax. VLM screening in-graph blocks the worker, hence the mechanical-only default.

### 6. Early-exit judging
**What.** Stop spending samples and rules once a take's verdict is decided.
**Pain.** screen() runs every rule K times even when the fate is sealed, so a clearly dead take costs as much to judge as a borderline one.
**Mechanism.** Sample-level: drop settled questions from subsequent sample requests once the majority cannot flip. Rule-level in calibrated mode: kill_score is max(severity times confidence), so once one rule crosses lambda at full agreement no remaining rule can un-kill; order rules by fitted weight so decision-relevant rules run first; record skipped rules in a `not_run` list.
**Effort.** Days, after a design fix.
**Dependencies.** Calibration passed into screen. Better after telemetry so the savings are measurable.
**Risks.** One judge showed the exactness claim is false on the kill side: CONF_KILL is 0.67 and 2 of 3 agreement is 0.667, so stopping at 2/2 yes can kill a take the third sample would have demoted to review. Only the no side is exact as proposed. Ship a corrected, kill-aware version, and record actual sample counts per defect because not_run holes starve fit and calibrate, the tool's compounding asset.

## Later

### 1. dailies MCP server
Stdio JSON-RPC server in stdlib exposing review, defect query, gold, calibrate, judge-check, and brief as tools, with `extract_frames` returning image content blocks so an agent can look at the frame behind a verdict and second-guess the judge. That evidence-frame return is the differentiator over shelling out to --json. Effort: a week plus permanent protocol upkeep. Depends on the orchestrator contract, so the server is a thin shell over published schemas, and on the skill proving the agent workflow. Risks: protocol drift if hand-rolled (pin one version, test with the inspector); cursor paging must be the default or long batches blow client tool timeouts.

### 2. Defect-conditioned mutation policy as a data file
Map defect classes to mutation ladders: reseed for mechanical kills, sampler and CFG patches then model swap for anatomy and morphing, model swap for physics, immediate prompt patch for adherence. Patched prompts go in recipe.prompt_patches while prompt_text stays the original, so the loop cannot pass a take by deleting the hard part of the prompt; that guard must survive any redesign. Ship the loop first with a built-in ladder and log (defect, mutation, outcome) in the ledger, then promote the ladder to a data file fitted from that log, the way fit learns rule weights. Risks: the default ladder is folklore until data accumulates; LLM prompt patches can drift creative intent, so the morning report shows the diff.

### 3. Lineage rendering and rerun verification
Merges the report work and the lineage-aware review idea. The report gains a shot completion board (k-of-n, attempts, cost, blocked shots marked needs-human) and per-shot retry chains grouped by root ancestor: killed attempts as chips labeled with the killing rule, mutations as edge labels, the survivor full-size with an audit badge when it passed on a late attempt. `dailies lineage` prints the same trees in the terminal, plus flattened recipe diffs aggregated across rerun pairs with counts, never causal claims. The targeted re-ask of the parent's killing rules already lives in the regen defense. Sequence one release behind the loop; pointless before chains exist. Keep killed-take sidecars when clips purge so chains render from data alone.

### 4. Judging budget governor
The `--budget USD` half of the governor-plus-batch idea, split as the judges directed. As budget depletes the watcher degrades in stated steps: samples to 1, then judge only takes near the decision boundary, then mechanical-only with a `budget_exhausted` note, honest per the SPEC rule that mechanical alone never keeps. Depends on telemetry and early-exit. The batch-API half is dropped from plan unless telemetry later proves interactive judge spend is a meaningful slice; it splits screen() into submit and collect halves across provider-variant endpoints, the largest refactor proposed.

### 5. dailies forensics
Per-(ngram, rule) lift and chi-square over recipe.prompt_text versus defect occurrence, plus numeric recipe fields versus mechanical stats. About 150 stdlib lines, but inert until recipe capture ships and per-user corpora clear a support floor, which takes weeks of accumulation. Sequence directly behind the node pack. Output says correlates, never causes, shows n, and reports top-k only.

### 6. Calibration packs
Shared conformal priors keyed by (judge, rubric hash, generator), weighted split conformal with the guarantee explicitly downgraded until local gold crosses the threshold. Two judges voted later, one drop: cross-user exchangeability is weak enough that the wording risk cuts against the honest-limits brand. The legitimate case is a private studio sharing one pack across its artists; build for that workflow when such a user appears. Packs stay file-based and hash-pinned, never auto-downloaded.

### 7. Local stats, then atlas
`dailies stats` cross-tabulating model source_hint against verdicts and defect classes is cheap and can fold into brief. The published opt-in benchmark (hosted collector, public scoreboard) is the long-term moat: the CLI is forkable, the aggregate dataset is not. But it is a second product with ops duty, privacy review, and an opted-in user base that does not exist yet. Launch sharing only when opted-in n clears a publishable bar, disclosed per cell, and keep the collector outside the pip package.

### 8. dailies compare
Cheap-generate-then-filter versus premium-generate math: $/usable take per arm with Wilson intervals and a breakeven line, plus experiment design when arms are untested. Right question, wrong moment: at 3 to 5 percent yield, tight intervals need hundreds of takes per arm, and per-arm identification needs recipe coverage. Build after telemetry and the node pack have data flowing. Must show intervals, prefer gold labels over verdicts, and refuse verdicts on tiny samples.

### 9. dailies tune
Vote-replay search for the cheapest judge config meeting a target kappa. Judge cost is second-order next to generation cost, the vote-capture refactor is a week, and subsampling on 20-take gold sets gives intervals too wide to act on. Instead, document the defaults the literature already supports (the 2 fps frame optimum, K's diminishing returns) and revisit if gold sets grow.

### 10. Distillation export
`dailies export --distill` replaying judged takes into image-question-answer JSONL, strong-model answers where the cheap judge split, gold as held-out eval, judge-check as the acceptance test for the tuned result. Training stays outside the package. The audience that LoRA-tunes a personal VLM is a sliver of a young user base and supervision volume is thin until months of watch traffic. Revisit when telemetry shows escalation spend worth recovering.

## Considered and dropped

- GitHub Actions story (judge-check PR gate, nightly generator regression): dropped by two of three judges. The audience is teams with CI pipelines, not the solo overnight generator; gold clips as CI binaries plus hosted VLM spend per PR is standing ops cost for one maintainer. Document a judge-check CI recipe in the README instead.