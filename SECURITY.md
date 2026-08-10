# Security policy

## Supported versions

The latest release on PyPI (`video-dailies`) is the supported version.

## Reporting a vulnerability

Report privately via [GitHub security advisories](https://github.com/zhang-liz/dailies/security/advisories/new). Do not open a public issue for a vulnerability.

Relevant surface: dailies shells out to ffmpeg/ffprobe on user-supplied paths, sends frames to a user-configured VLM endpoint (`DAILIES_VLM_KEY` is read from the environment and never written to disk), executes user-configured driver commands (`--regen`, `--on-doomed`), and parses JSON sidecars. Reports about path handling, command construction, or credential leakage in any of those are especially welcome.
