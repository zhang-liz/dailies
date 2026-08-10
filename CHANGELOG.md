# Changelog

Notable changes per release. Format follows [Keep a Changelog](https://keepachangelog.com/); versions follow [SemVer](https://semver.org/) with 0.x meaning the CLI surface may still move.

## [Unreleased]

## [0.2.0] - 2026-08-10

### Added
- Checklist rubric: rules are yes/no evidence questions with severity fixed in the rubric; legacy prompt rules still work.
- `--samples K`: judge disagreement becomes per-defect confidence; a defect below two-thirds agreement cannot kill.
- `--vlm-strong`: cascade that re-judges only the unsure rules on a stronger model.
- `dailies gold`: record human pass/kill verdicts in sidecars.
- `dailies calibrate`: conformal kill threshold with a stated false-kill rate; refuses below 19 gold-pass takes at alpha 0.05.
- `dailies fit`: per-rule ranking weights learned from your own verdicts.
- `dailies judge-check`: re-judge the gold set, track agreement and kappa drift, `--fail-below` gate for CI.
- Motion smoothness (interpolation reconstruction) and motion-masked flicker in the mechanical stage; uniform frame strip alongside YDIF peaks.
- `dailies brief`: per-shot failure dossiers from sidecars, no LLM.
- `dailies assemble`: slated rough cut of the best take per shot, with an EDL CSV.
- Cost telemetry: `--prices` turns recorded token usage into dollars per usable take.
- Doomed-shot circuit breaker in `watch`, with `--on-doomed` hook.
- Machine interface: `review --ndjson`, `dailies verdict` with a documented exit-code table, `dailies schema` printing published JSON Schemas.
- Regen loop: `watch --regen` with a subprocess driver contract (docs/DRIVERS.md), night ledger (best-of-k, lineage cap, futility, attempt and spend caps), and the regen-to-green defense layer (ancestor-rule scrutiny, audit sampling, judge-health gate, root-prompt adherence).
- `triage-dailies` Claude Code skill.

### Changed
- Report header now leads with spend and dollars per usable take when prices are supplied.

## [0.1.0] - 2026-08-08

### Added
- Mechanical funnel: decode, black, freeze, flicker, scene cuts, YDIF candidate frames.
- VLM screening against any OpenAI-compatible endpoint with a rubric of rules.
- take.json sidecars (SPEC.md) and the static HTML morning report.
- `dailies watch`: review takes as they land.

[Unreleased]: https://github.com/zhang-liz/dailies/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/zhang-liz/dailies/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/zhang-liz/dailies/releases/tag/v0.1.0
