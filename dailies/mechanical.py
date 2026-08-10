"""Stage 1 of the triage funnel: mechanical checks. CPU only, ffmpeg/ffprobe.

Kills the cheap deaths (decode errors, black, freeze) and selects candidate
frames for the VLM stage: artifact frames are frame-difference outliers, so
YDIF peaks beat uniform sampling.

Never says "keep". Mechanical verdicts are kill or review; keep needs eyes
or a VLM.
"""

import json
import re
import statistics
import subprocess

# A clip is dead when more than this fraction of it is black or frozen.
KILL_FRACTION = 0.5
BLACKDETECT = "blackdetect=d=0.1:pix_th=0.10"
FREEZEDETECT = "freezedetect=n=-60dB:d=0.5"
SCENE_THRESHOLD = 0.4
CANDIDATE_FRAMES = 8


class FfmpegMissing(RuntimeError):
    pass


def _run(cmd):
    try:
        return subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
    except FileNotFoundError:
        raise FfmpegMissing("%s not found on PATH" % cmd[0])


def _lavfi_movie(path, filters):
    # Escape for the movie source filter: \ ' : , are special inside a
    # filtergraph argument.
    escaped = re.sub(r"([\\':,])", r"\\\1", path)
    return "movie=%s,%s" % (escaped, filters)


def probe(path):
    """Container/stream sanity via ffprobe. Returns dict, never raises on a
    bad file: decode problems land in result["errors"]."""
    r = _run(
        [
            "ffprobe", "-v", "error", "-count_frames",
            "-show_entries",
            "format=duration:stream=codec_type,width,height,avg_frame_rate,nb_read_frames",
            "-of", "json", path,
        ]
    )
    out = {"duration": None, "fps": None, "width": None, "height": None,
           "frames": None, "errors": []}
    if r.returncode != 0:
        out["errors"].append(r.stderr.strip() or "ffprobe failed")
        return out
    data = json.loads(r.stdout or "{}")
    video = [s for s in data.get("streams", []) if s.get("codec_type") == "video"]
    if not video:
        out["errors"].append("no video stream")
        return out
    s = video[0]
    out["width"] = s.get("width")
    out["height"] = s.get("height")
    if s.get("nb_read_frames", "N/A") != "N/A":
        out["frames"] = int(s["nb_read_frames"])
    rate = s.get("avg_frame_rate", "0/1")
    num, _, den = rate.partition("/")
    if den and float(den) != 0:
        out["fps"] = round(float(num) / float(den), 3)
    dur = data.get("format", {}).get("duration")
    if dur is not None:
        out["duration"] = float(dur)

    # Full-decode check: container metadata can be fine while frames are not.
    d = _run(["ffmpeg", "-v", "error", "-i", path, "-f", "null", "-"])
    decode_errors = [l for l in d.stderr.splitlines() if l.strip()]
    if decode_errors:
        out["errors"].append("decode: %s" % decode_errors[0])
    return out


def black_and_freeze(path):
    """Spans of black and frozen video, parsed from detector logs."""
    r = _run(
        [
            "ffmpeg", "-v", "info", "-i", path,
            "-vf", "%s,%s" % (BLACKDETECT, FREEZEDETECT),
            "-an", "-f", "null", "-",
        ]
    )
    log = r.stderr
    black = [
        {"start": float(m.group(1)), "end": float(m.group(2))}
        for m in re.finditer(r"black_start:([\d.]+) black_end:([\d.]+)", log)
    ]
    freeze = []
    starts = [float(m.group(1)) for m in
              re.finditer(r"freeze_start: ([\d.]+)", log)]
    ends = [float(m.group(1)) for m in
            re.finditer(r"freeze_end: ([\d.]+)", log)]
    for i, start in enumerate(starts):
        end = ends[i] if i < len(ends) else None  # still frozen at EOF
        freeze.append({"start": start, "end": end})
    return black, freeze


def luma_series(path):
    """Per-frame (pts_time, YAVG, YDIF) from signalstats."""
    r = _run(
        [
            "ffprobe", "-v", "error", "-f", "lavfi",
            "-i", _lavfi_movie(path, "signalstats"),
            "-show_entries",
            "frame=pts_time:frame_tags=lavfi.signalstats.YAVG,lavfi.signalstats.YDIF",
            "-of", "json",
        ]
    )
    frames = json.loads(r.stdout or "{}").get("frames", [])
    series = []
    for f in frames:
        tags = f.get("tags", {})
        try:
            series.append((
                float(f.get("pts_time", 0)),
                float(tags.get("lavfi.signalstats.YAVG", 0)),
                float(tags.get("lavfi.signalstats.YDIF", 0)),
            ))
        except (TypeError, ValueError):
            continue
    return series


def scene_cuts(path):
    """Timestamps of detected cuts. Any cut in a single generated shot is a
    defect: the model glitched, not the editor."""
    r = _run(
        [
            "ffprobe", "-v", "error", "-f", "lavfi",
            "-i", _lavfi_movie(
                path, "select=gt(scene\\,%s)" % SCENE_THRESHOLD),
            "-show_entries", "frame=pts_time", "-of", "json",
        ]
    )
    frames = json.loads(r.stdout or "{}").get("frames", [])
    return [float(f["pts_time"]) for f in frames if "pts_time" in f]


MOTION_MASK_RATIO = 2.0


def flicker_score(series):
    """Mean absolute frame-to-frame luma change, normalized to 0..1.

    Frames whose YDIF is well above the clip's median carry genuine
    motion, and intended action is not flicker; they are masked out
    (VBench masks motion regions for the same reason). A clip that
    flickers throughout raises its own median, so nothing is masked
    and the flicker still counts."""
    if len(series) < 2:
        return 0.0
    ydifs = sorted(f[2] for f in series[1:])
    cap = ydifs[len(ydifs) // 2] * MOTION_MASK_RATIO
    diffs = [abs(series[i][1] - series[i - 1][1])
             for i in range(1, len(series)) if series[i][2] <= cap]
    if not diffs:
        diffs = [abs(series[i][1] - series[i - 1][1])
                 for i in range(1, len(series))]
    return round(statistics.mean(diffs) / 255.0, 4)


SMOOTHNESS_WIDTH = 256


def motion_smoothness(path, fps):
    """Interpolation-reconstruction smoothness, 0..1, higher is smoother.

    VBench's motion-smoothness construct with ffmpeg's minterpolate
    standing in for the learned interpolator: drop the odd frames,
    re-invent them from the evens, SSIM the reconstruction against the
    original. Smooth plausible motion reconstructs well; jerky or
    rubber-band motion does not. None when fps is unknown or ffmpeg
    cannot compute it."""
    if not fps or fps <= 0:
        return None
    graph = ("[0:v]scale=%d:-2,split[a][b];"
             "[a]select='not(mod(n\\,2))',minterpolate=fps=%s[rec];"
             "[rec][b]ssim" % (SMOOTHNESS_WIDTH, fps))
    r = _run(["ffmpeg", "-v", "info", "-i", path,
              "-filter_complex", graph, "-an", "-f", "null", "-"])
    m = re.search(r"SSIM.*All:([\d.]+)", r.stderr)
    return round(float(m.group(1)), 4) if m else None


def candidate_frames(series, n=CANDIDATE_FRAMES):
    """Timestamps for the VLM stage: YDIF peaks plus the first frame."""
    if not series:
        return []
    peaks = sorted(series[1:], key=lambda f: f[2], reverse=True)[: n - 1]
    times = {series[0][0]} | {t for t, _, _ in peaks}
    return sorted(round(t, 3) for t in times)


def review(path):
    """Run the full mechanical stage on one clip. Returns the take.json
    "review" dict (mechanical block + verdict)."""
    info = probe(path)
    result = {
        "mechanical": {
            "probe": info,
            "black_frames": [],
            "freeze": [],
            "scene_cuts": [],
            "flicker_score": None,
            "motion_smoothness": None,
            "candidate_frames": [],
            "kill_reasons": [],
        },
        "vlm": None,
        "verdict": "review",
        "rank_in_shot": None,
    }
    mech = result["mechanical"]
    if info["errors"]:
        mech["kill_reasons"] = ["probe: %s" % e for e in info["errors"]]
        result["verdict"] = "kill"
        return result

    duration = info["duration"] or 0
    black, freeze = black_and_freeze(path)
    mech["black_frames"] = black
    mech["freeze"] = freeze
    series = luma_series(path)
    mech["flicker_score"] = flicker_score(series)
    mech["scene_cuts"] = scene_cuts(path)
    mech["motion_smoothness"] = motion_smoothness(path, info["fps"])
    mech["candidate_frames"] = candidate_frames(series)

    if duration > 0:
        black_total = sum(s["end"] - s["start"] for s in black)
        if black_total / duration > KILL_FRACTION:
            mech["kill_reasons"].append(
                "black for %.1fs of %.1fs" % (black_total, duration))
        freeze_total = sum(
            (s["end"] if s["end"] is not None else duration) - s["start"]
            for s in freeze)
        if freeze_total / duration > KILL_FRACTION:
            mech["kill_reasons"].append(
                "frozen for %.1fs of %.1fs" % (freeze_total, duration))

    if mech["kill_reasons"]:
        result["verdict"] = "kill"
    return result
