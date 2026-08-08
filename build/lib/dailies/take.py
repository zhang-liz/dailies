"""take.json sidecar read/write.

One sidecar per clip: <clip>.take.json, next to the clip. The spec lives in
docs/ideas-dailies-slate-mvp.md. dailies owns the "review" block; slate owns
"recipe". Either tool works alone, so missing blocks are normal.

Hashes carry an algorithm prefix ("blake3:..." or "sha256:...") so the two
tools never have to agree on an installed library. blake3 is used when the
optional package is importable, sha256 otherwise.
"""

import json
import os

try:
    from blake3 import blake3 as _blake3
except ImportError:
    _blake3 = None

import hashlib

_CHUNK = 1 << 20


def hash_file(path):
    """Content hash of a file, with algorithm prefix."""
    h = _blake3() if _blake3 else hashlib.sha256()
    prefix = "blake3:" if _blake3 else "sha256:"
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return prefix + h.hexdigest()


def sidecar_path(clip_path):
    return clip_path + ".take.json"


def load(clip_path):
    """Existing sidecar for a clip, or a fresh skeleton."""
    p = sidecar_path(clip_path)
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return {
        "take_id": None,
        "shot": None,
        "parent": None,
        "created": None,
        "output": {"file": os.path.basename(clip_path)},
        "recipe": None,
        "review": None,
    }


def save(clip_path, take):
    p = sidecar_path(clip_path)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(take, f, indent=2, sort_keys=False)
        f.write("\n")
    os.replace(tmp, p)
    return p
