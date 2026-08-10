# Contributing

Thanks for looking. Bug reports, rubric contributions, and drivers are the most useful things you can send.

## Ground rules

- **Core stays stdlib + ffmpeg.** No new runtime dependencies in `dailies/`. Optional integrations belong behind `[project.optional-dependencies]` or external processes (see docs/DRIVERS.md for the driver pattern).
- **The sidecar is a contract.** Changes to take.json fields must update SPEC.md and `dailies/schemas/`, stay additive, and preserve unknown keys. Tools must never clobber blocks they do not own.
- **Verdicts stay honest.** Nothing in this tool may claim a take is good. Mechanical checks alone never produce keep; guarantees are stated only when the statistics back them.
- **Every behavior gets a test.** The suite runs on plain `unittest` with synthetic ffmpeg clips and stub HTTP judges; follow the existing patterns in `tests/`.

## Setup

```sh
git clone https://github.com/zhang-liz/dailies
cd dailies
python3 -m unittest discover tests
```

ffmpeg and ffprobe must be on PATH. There is nothing to build and nothing else to install.

## Pull requests

- One logical change per commit, short subject line.
- Run the full suite before pushing; CI runs it on Python 3.9, 3.12, and 3.13.
- New sidecar fields: update SPEC.md, the schema, and a test in the same PR.
- Rubric contributions: real rules you use in production, with the defect class they catch described in the PR.

## Reporting bugs

Open an issue with the dailies version, the command you ran, and if at all possible the sidecar (`<clip>.take.json`) of the take that misbehaved. The sidecar usually contains the whole story.
