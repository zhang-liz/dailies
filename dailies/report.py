"""The morning report: one static HTML file over take.json sidecars.

Per shot: survivors ranked first, hover-to-scrub previews, defect spans
marked on a timeline, kill reasons one click away. No server, no deps;
video tags point at the clips relative to the report file.
"""

import html
import json
import os
import re

CSS = """
:root { color-scheme: dark; }
* { box-sizing: border-box; margin: 0; }
body { background: #101014; color: #e8e8ea; font: 14px/1.5 -apple-system,
  'Helvetica Neue', Arial, sans-serif; padding: 32px; }
h1 { font-size: 20px; letter-spacing: .02em; margin-bottom: 4px; }
.sub { color: #8b8b93; margin-bottom: 28px; }
h2 { font-size: 15px; text-transform: uppercase; letter-spacing: .08em;
  color: #b7b7bf; margin: 28px 0 12px; border-bottom: 1px solid #26262c;
  padding-bottom: 6px; }
.takes { display: grid; grid-template-columns: repeat(auto-fill,
  minmax(440px, 1fr)); gap: 16px; }
@media (max-width: 500px) { .takes { grid-template-columns: 1fr; } }
.take { background: #17171c; border: 1px solid #26262c; border-radius: 8px;
  overflow: hidden; }
.take.kill { opacity: .55; }
.take video { display: block; width: 100%; aspect-ratio: 16/9;
  object-fit: contain; background: #000; cursor: ew-resize; }
.meta { padding: 10px 12px; }
.row1 { display: flex; justify-content: space-between; align-items: baseline; }
.name { font-weight: 600; overflow-wrap: anywhere; }
.verdict { font-size: 11px; text-transform: uppercase; letter-spacing: .06em;
  padding: 2px 7px; border-radius: 99px; }
.verdict.review { background: #1d3a2a; color: #7fd8a2; }
.verdict.kill { background: #3a1d1d; color: #d87f7f; }
.stats { color: #8b8b93; font-size: 12px; margin-top: 4px; }
.timeline { position: relative; height: 8px; background: #26262c;
  border-radius: 4px; margin-top: 8px; }
.span { position: absolute; top: 0; height: 100%; border-radius: 4px; }
.span.black { background: #5a5a66; }
.span.freeze { background: #4f7dd8; }
.cut { position: absolute; top: -2px; width: 2px; height: 12px;
  background: #d8a24f; }
.defect { position: absolute; top: -3px; width: 6px; height: 6px;
  border-radius: 50%; background: #d8564f; transform: translateX(-3px); }
.defect.anatomy { background: #d8564f; }
.defect.physics { background: #d88a4f; }
.defect.artifact { background: #a06fd8; }
.defect.text { background: #d8c94f; }
.defect.environment { background: #4fd8b8; }
.defect.adherence { background: #d84fa8; }
.lane { position: relative; height: 14px; margin-top: 5px; }
.lane-track { position: absolute; left: 96px; right: 8px; top: 0;
  height: 100%; }
.lane .defect { top: 4px; }
.lane-name { font-family: inherit; font-weight: 400; font-size: 9px;
  letter-spacing: .1em; text-transform: uppercase; }
.lane > .lane-name { position: absolute; left: 0; top: 1px; }
.lane-name.mechanical { color: #8b8b93; }
.lane-name.anatomy { color: #d8564f; }
.lane-name.physics { color: #d88a4f; }
.lane-name.artifact { color: #a06fd8; }
.lane-name.text { color: #d8c94f; }
.lane-name.environment { color: #4fd8b8; }
.lane-name.adherence { color: #d84fa8; }
details { margin-top: 8px; }
summary { cursor: pointer; color: #8b8b93; font-size: 12px; }
.fam { margin-top: 8px; }
.fam pre { white-space: pre-wrap; font-size: 12px; color: #b7b7bf;
  margin-top: 3px; }
.legend { color: #8b8b93; font-size: 12px; margin-top: 24px; }
.legend i { position: static; transform: none; display: inline-block;
  width: 10px; height: 10px; border-radius: 2px; margin: 0 4px 0 12px;
  vertical-align: -1px; }
"""

JS = """
document.querySelectorAll('.take video').forEach(function (v) {
  // Without a poster the card is a black box until a frame decodes, so
  // decode one. With a poster, leave it be.
  if (!v.poster) {
    v.addEventListener('loadedmetadata', function () {
      v.currentTime = Math.min(0.04, v.duration || 0);
    }, { once: true });
  }
  // Hover-scrub is a preview aid; the native controls own playback. Scrub
  // only while paused, and never in the strip where the control bar sits,
  // so reaching for the play button does not yank the playhead.
  v.addEventListener('mousemove', function (e) {
    if (!v.duration || !v.paused) return;
    var r = v.getBoundingClientRect();
    if (e.clientY > r.bottom - 48) return;
    v.currentTime = v.duration * Math.min(Math.max(
      (e.clientX - r.left) / r.width, 0), 0.999);
  });
});
"""


def _find_takes(root):
    takes = []
    for dirpath, _, files in os.walk(root):
        for f in files:
            if not f.endswith(".take.json"):
                continue
            path = os.path.join(dirpath, f)
            with open(path) as fh:
                t = json.load(fh)
            t["_clip"] = path[: -len(".take.json")]
            takes.append(t)
    return takes


def _timeline(mech, duration, defects=()):
    if not duration:
        return ""
    spans = []
    for s in mech.get("black_frames", []):
        spans.append('<i class="span black" style="left:%.1f%%;width:%.1f%%">'
                     "</i>" % (100 * s["start"] / duration,
                               100 * (s["end"] - s["start"]) / duration))
    for s in mech.get("freeze", []):
        end = s["end"] if s["end"] is not None else duration
        spans.append('<i class="span freeze" style="left:%.1f%%;width:%.1f%%">'
                     "</i>" % (100 * s["start"] / duration,
                               100 * (end - s["start"]) / duration))
    for t in mech.get("scene_cuts", []):
        spans.append('<i class="cut" style="left:%.1f%%"></i>'
                     % (100 * t / duration))
    # One lane per defect family, so co-timed defects of different kinds
    # never draw over each other.
    lanes = []
    by_family = {}
    for d in defects:
        by_family.setdefault(d["rule"].split(".")[0], []).append(d)
    for family in sorted(by_family):
        dots = "".join(
            '<i class="defect %s" style="left:%.1f%%" title="%s"></i>' % (
                html.escape(family),
                100 * min(d["t"], duration) / duration,
                html.escape("%s (%d): %s" % (
                    d["rule"], d["severity"], d["note"])))
            for d in by_family[family])
        lanes.append('<div class="lane"><b class="lane-name %s">%s</b>'
                     '<span class="lane-track">%s</span></div>'
                     % (html.escape(family), html.escape(family), dots))
    return ('<div class="timeline">%s</div>%s'
            % ("".join(spans), "".join(lanes)))


def _take_card(t, report_dir):
    r = t.get("review") or {}
    mech = r.get("mechanical") or {}
    verdict = r.get("verdict", "review")
    rel = os.path.relpath(t["_clip"], report_dir)
    # A sibling jpg (same name, .jpg) becomes the poster, so cards show a
    # frame before any video data loads. Browsers vary on whether an unplayed
    # video paints at all; a poster removes the dependence.
    poster = ""
    jpg = os.path.splitext(t["_clip"])[0] + ".jpg"
    if os.path.exists(jpg):
        poster = ' poster="%s"' % html.escape(
            os.path.relpath(jpg, report_dir))
    out = t.get("output", {})
    duration = (mech.get("probe") or {}).get("duration")
    stats = []
    if out.get("width"):
        stats.append("%sx%s" % (out["width"], out["height"]))
    if out.get("frames"):
        stats.append("%s frames" % out["frames"])
    if mech.get("flicker_score") is not None:
        stats.append("flicker %.3f" % mech["flicker_score"])
    if mech.get("scene_cuts"):
        stats.append("%d cuts" % len(mech["scene_cuts"]))
    defects = (r.get("vlm") or {}).get("defects") or []
    if defects:
        stats.append("%d defects" % len(defects))
    # Sectioned by family, each section under a colored heading, so a take
    # that failed three different ways reads as three groups, not one wall.
    mech_reasons = [r_ for r_ in (mech.get("kill_reasons") or [])
                    if not any(r_.startswith(d["rule"]) for d in defects)]
    groups = []
    if mech_reasons:
        groups.append(("mechanical", mech_reasons))
    by_family = {}
    for d in defects:
        by_family.setdefault(d["rule"].split(".")[0], []).append(
            "%ss %s (%d): %s" % (d["t"], d["rule"], d["severity"],
                                 d["note"]))
    groups.extend(sorted(by_family.items()))
    details = ""
    if groups:
        label = "why killed" if verdict == "kill" else "defects"
        body = "".join(
            '<div class="fam"><b class="lane-name %s">%s</b><pre>%s</pre>'
            "</div>" % (html.escape(fam), html.escape(fam),
                        html.escape("\n".join(lines_)))
            for fam, lines_ in groups)
        details = ("<details><summary>%s</summary>%s</details>"
                   % (label, body))
    rank = r.get("rank_in_shot")
    return """<div class="take %s">
<video src="%s"%s preload="metadata" muted playsinline controls controlslist="nodownload noremoteplayback"></video>
<div class="meta">
<div class="row1"><span class="name">%s%s</span>
<span class="verdict %s">%s</span></div>
<div class="stats">%s</div>
%s%s
</div></div>""" % (
        verdict, html.escape(rel), poster,
        ("#%d " % rank) if rank else "",
        html.escape(out.get("file") or os.path.basename(t["_clip"])),
        verdict, verdict,
        html.escape(" · ".join(stats)),
        _timeline(mech, duration, defects), details)


def build(root, output):
    takes = _find_takes(root)
    report_dir = os.path.dirname(os.path.abspath(output)) or "."
    by_shot = {}
    for t in takes:
        by_shot.setdefault(t.get("shot") or "untagged", []).append(t)

    sections = []
    for shot in sorted(by_shot):
        group = sorted(by_shot[shot], key=lambda t: (
            (t.get("review") or {}).get("rank_in_shot") or 10**6))
        kills = sum(1 for t in group
                    if (t.get("review") or {}).get("verdict") == "kill")
        # The heading is linkable (report.html#shot-07): slugged shot name.
        slug = re.sub(r"[^a-z0-9]+", "-", shot.lower()).strip("-") or "shot"
        sections.append('<h2 id="%s">%s · %d takes · %d killed</h2>'
                        '<div class="takes">%s</div>' % (
                            slug, html.escape(shot), len(group), kills,
                            "".join(_take_card(t, report_dir)
                                    for t in group)))

    killed = sum(1 for t in takes
                 if (t.get("review") or {}).get("verdict") == "kill")
    doc = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>dailies report</title><style>%s</style></head><body>
<h1>Dailies</h1>
<div class="sub">%d takes reviewed, %d killed, %d to watch.
Hover a paused clip to scrub; play with the controls.</div>
%s
<div class="legend">timeline:<i class="span black"></i>black
<i class="span freeze"></i>frozen <i class="cut"></i>cut
<i class="defect anatomy"></i>anatomy <i class="defect physics"></i>physics
<i class="defect artifact"></i>artifact <i class="defect text"></i>text
<i class="defect environment"></i>environment
<i class="defect adherence"></i>adherence</div>
<script>%s</script></body></html>""" % (
        CSS, len(takes), killed, len(takes) - killed,
        "\n".join(sections), JS)
    with open(output, "w") as f:
        f.write(doc)
    return os.path.abspath(output)
