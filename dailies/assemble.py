"""The morning rough cut: best non-kill take per shot, slated and joined.

Directors think in cuts, not file lists, so the survivors become one
watchable file. Concat and slates only, by design: no trims, no audio,
no NLE ambitions. The CSV beside the cut maps every segment's timecodes
back to its source file, because the cut is for watching and the files
are for acting on.

Segments are re-encoded to one fps and frame size first; mixed sources
make stream-copy concat glitch, so normalization is not optional.
"""

import csv
import json
import os
import tempfile

from . import mechanical, report

DEFAULT_FPS = 24
DEFAULT_SIZE = (1280, 720)
# Readable over any footage: boxed white text, sized off the frame.
SLATE_STYLE = ("fontsize=h/18:fontcolor=white:box=1:boxcolor=black@0.55:"
               "boxborderw=8:x=12:y=12")

_drawtext = None


class NothingToCut(RuntimeError):
    """No surviving takes: nothing-to-do, exit 1, not an error."""


def has_drawtext():
    """Whether this ffmpeg build carries drawtext. Builds without
    libfreetype lack it; the cut still assembles, just unslated."""
    global _drawtext
    if _drawtext is None:
        r = mechanical._run(["ffmpeg", "-hide_banner", "-h",
                             "filter=drawtext"])
        _drawtext = "Unknown filter" not in (r.stdout + r.stderr)
    return _drawtext


def select(takes, alts=0):
    """Surviving takes per shot, best rank first, 1 + alts deep.
    Kills never reach the cut; that is the whole point of triage."""
    by_shot = {}
    for t in takes:
        r = t.get("review") or {}
        if r.get("verdict") in ("keep", "review"):
            by_shot.setdefault(t.get("shot") or "untagged", []).append(t)
    for group in by_shot.values():
        group.sort(key=lambda t: t["review"].get("rank_in_shot") or 10 ** 6)
    return {shot: group[: 1 + alts] for shot, group in by_shot.items()}


def shot_order(shots, shots_file=None):
    """Cut order: the shot-list file when given, shot name otherwise.
    Shots absent from the file go last, in name order, so a partial
    list never silently drops footage."""
    if not shots_file:
        return sorted(shots)
    listed = []
    with open(shots_file) as f:
        for line in f:
            name = line.split("#", 1)[0].strip()
            if name and name not in listed:
                listed.append(name)
    ordered = [s for s in listed if s in shots]
    return ordered + sorted(s for s in shots if s not in listed)


def short_id(take_id):
    """Eight hash characters: enough to find the take, short enough to
    burn into a corner."""
    if not take_id:
        return "unhashed"
    return take_id.split(":", 1)[-1][:8]


def top_rule(t):
    """The defect most worth a director's glance: highest severity,
    confidence breaking ties."""
    defects = ((t.get("review") or {}).get("vlm") or {}).get("defects") or []
    if not defects:
        return None
    best = max(defects, key=lambda d: (d.get("severity") or 0,
                                       d.get("confidence") or 0))
    return best.get("rule")


def slate_text(t):
    """What the burned-in slate says: shot, short take id, verdict, and
    the top defect rule when the verdict is review."""
    r = t.get("review") or {}
    parts = [t.get("shot") or "untagged", short_id(t.get("take_id")),
             r.get("verdict") or "unreviewed"]
    if r.get("verdict") == "review":
        rule = top_rule(t)
        if rule:
            parts.append(rule)
    return "  ".join(parts)


def drawtext_escape(text):
    """Escape slate text for drawtext inside a filtergraph. Two parsers
    read it (the graph's, then the filter's), so specials get a
    backslash; expansion=none keeps percent signs literal."""
    out = text.replace("\\", "\\\\")
    for ch in "':,[];=":
        out = out.replace(ch, "\\" + ch)
    return out


def _segment_filter(fps, width, height, text):
    vf = ("fps=%s,scale=%d:%d:force_original_aspect_ratio=decrease,"
          "pad=%d:%d:(ow-iw)/2:(oh-ih)/2,setsar=1"
          % (fps, width, height, width, height))
    if text is not None:
        vf += (",drawtext=text=%s:expansion=none:%s"
               % (drawtext_escape(text), SLATE_STYLE))
    return vf


def _run_or_die(cmd, what):
    r = mechanical._run(cmd)
    if r.returncode != 0:
        err = (r.stderr or "").strip().splitlines()
        raise RuntimeError("%s: %s" % (what,
                                       err[-1] if err else "ffmpeg failed"))
    return r


def _duration(path):
    r = mechanical._run(["ffprobe", "-v", "error", "-show_entries",
                         "format=duration", "-of", "json", path])
    dur = json.loads(r.stdout or "{}").get("format", {}).get("duration")
    return float(dur) if dur is not None else 0.0


def timecode(seconds, fps):
    """HH:MM:SS:FF at the cut's frame rate."""
    frames = int(round(seconds * fps))
    ff = frames % int(round(fps))
    s = frames // int(round(fps))
    return "%02d:%02d:%02d:%02d" % (s // 3600, s % 3600 // 60, s % 60, ff)


def assemble(root, output, shots_file=None, alts=0, fps=None, size=None,
             csv_path=None, slate=True):
    """Build the cut and its CSV. Returns a summary dict; raises
    NothingToCut when nothing survives to cut."""
    takes = report.find_takes(root)
    picked = select(takes, alts=alts)
    if not picked:
        raise NothingToCut("no surviving takes to assemble in %s" % root)
    order = shot_order(picked, shots_file)
    segments = [t for shot in order for t in picked[shot]]

    # The first cut take sets fps and frame size unless told otherwise,
    # so a uniform batch never re-letterboxes itself.
    first = segments[0].get("output") or {}
    fps = fps or first.get("fps") or DEFAULT_FPS
    if not size:
        size = ((first.get("width"), first.get("height"))
                if first.get("width") and first.get("height")
                else DEFAULT_SIZE)
    width, height = size

    slate_on = slate and has_drawtext()
    rows = []
    with tempfile.TemporaryDirectory(prefix="dailies-assemble-") as tmp:
        listing = os.path.join(tmp, "concat.txt")
        at = 0.0
        with open(listing, "w") as lst:
            for i, t in enumerate(segments):
                seg = os.path.join(tmp, "seg-%04d.mp4" % i)
                text = slate_text(t) if slate_on else None
                _run_or_die(
                    ["ffmpeg", "-v", "error", "-y", "-i", t["_clip"],
                     "-vf", _segment_filter(fps, width, height, text),
                     "-an", "-pix_fmt", "yuv420p", "-c:v", "libx264",
                     "-preset", "veryfast", "-crf", "18", seg],
                    "encode %s" % t["_clip"])
                lst.write("file '%s'\n" % seg.replace("'", "'\\''"))
                dur = _duration(seg)
                rows.append({
                    "n": i + 1,
                    "record_in": round(at, 3),
                    "record_out": round(at + dur, 3),
                    "tc_in": timecode(at, fps),
                    "shot": t.get("shot") or "untagged",
                    "rank": t["review"].get("rank_in_shot"),
                    "verdict": t["review"].get("verdict"),
                    "take_id": t.get("take_id"),
                    "file": t["_clip"],
                })
                at += dur
        _run_or_die(["ffmpeg", "-v", "error", "-y", "-f", "concat",
                     "-safe", "0", "-i", listing, "-c", "copy", output],
                    "concat %s" % output)

    csv_path = csv_path or os.path.splitext(output)[0] + ".csv"
    fields = ["n", "record_in", "record_out", "tc_in", "shot", "rank",
              "verdict", "take_id", "file"]
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    return {
        "output": os.path.abspath(output),
        "csv": os.path.abspath(csv_path),
        "fps": fps, "width": width, "height": height,
        "duration": round(at, 3),
        "slated": slate_on,
        "shots": order,
        "segments": rows,
    }
