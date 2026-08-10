"""The morning report: one static HTML file over take.json sidecars.

Per shot: survivors ranked first, hover-to-scrub previews, defect spans
marked on a timeline, kill reasons one click away. No server, no deps;
video tags point at the clips relative to the report file.
"""

import html
import json
import os
import re

from . import breaker

CSS = """
:root { color-scheme: dark; }
* { box-sizing: border-box; margin: 0; }
body { background: #101014; color: #e8e8ea; font: 16px/1.6 -apple-system,
  'Helvetica Neue', Arial, sans-serif; padding: 32px; }
h1 { font-size: 24px; letter-spacing: .02em; margin-bottom: 4px; }
.sub { color: #8b8b93; margin-bottom: 28px; }
h2 { font-size: 17px; text-transform: uppercase; letter-spacing: .08em;
  color: #b7b7bf; margin: 28px 0 12px; border-bottom: 1px solid #26262c;
  padding-bottom: 6px; }
.takes { display: grid; grid-template-columns: repeat(2, 1fr);
  gap: 18px; }
@media (max-width: 760px) { .takes { grid-template-columns: 1fr; } }
.take { background: #17171c; border: 1px solid #26262c;
  border-radius: 8px; }
.take.kill { border-color: #6b2f2f; }
.take video { display: block; width: 100%; height: auto;
  max-height: 76vh; background: #000;
  border-radius: 7px 7px 0 0; }
[data-t] { cursor: pointer; }
a.seek { color: inherit; text-decoration: underline;
  text-decoration-color: #55555e; text-underline-offset: 3px; }
a.seek:hover { text-decoration-color: #e8e8ea; }
.meta { padding: 10px 12px; }
.row1 { display: flex; justify-content: space-between; align-items: baseline; }
.name { font-weight: 600; overflow-wrap: anywhere; }
.verdict { font-size: 12px; text-transform: uppercase; letter-spacing: .06em;
  padding: 2px 7px; border-radius: 99px; }
.verdict.passed { background: #1d3a2a; color: #7fd8a2; }
.verdict.review { background: #3a331d; color: #d8c97f; }
.verdict.kill { background: #3a1d1d; color: #d87f7f; }
.stats { color: #9a9aa3; font-size: 14px; margin-top: 4px; }
.timeline { position: relative; height: 8px; background: #26262c;
  border-radius: 4px; margin-top: 8px; }
.span { position: absolute; top: 0; height: 100%; border-radius: 4px; }
.span.black { background: #5a5a66; }
.span.freeze { background: #4f7dd8; }
.cut { position: absolute; top: -2px; width: 2px; height: 12px;
  background: #d8a24f; }
.defect { position: absolute; top: -3px; width: 12px; height: 12px;
  min-width: 12px; border-radius: 50%; background: #d8564f;
  transform: translateX(-6px); transition: transform .1s; }
.timeline .defect:hover { transform: translateX(-6px) scale(1.3);
  z-index: 6; }
/* Instant tooltip; the native title waits a second, this does not. */
.timeline .defect::after { content: attr(data-note); position: absolute;
  bottom: 16px; left: 50%; transform: translateX(-50%);
  background: #1f1f26; border: 1px solid #3a3a44; border-radius: 8px;
  padding: 8px 12px; font-size: 13px; line-height: 1.5; color: #e8e8ea;
  width: max-content; max-width: 340px; white-space: normal;
  opacity: 0; pointer-events: none; transition: opacity .08s; z-index: 7; }
.timeline .defect:hover::after { opacity: 1; }
/* Edge dots align the tooltip inward so it cannot leave the page. */
.timeline .defect.tip-left::after { left: 0; transform: none; }
.timeline .defect.tip-right::after { left: auto; right: 0;
  transform: none; }
.defect.anatomy { background: #d8564f; }
.defect.physics { background: #d88a4f; }
.defect.artifact { background: #a06fd8; }
.defect.text { background: #d8c94f; }
.defect.continuity { background: #6fb1d8; }
.defect.environment { background: #4fd8b8; }
.defect.adherence { background: #d84fa8; }
.lane-name { font-family: inherit; font-weight: 400; font-size: 11px;
  letter-spacing: .1em; text-transform: uppercase; }
.lane-name.mechanical { color: #8b8b93; }
.lane-name.anatomy { color: #d8564f; }
.lane-name.physics { color: #d88a4f; }
.lane-name.artifact { color: #a06fd8; }
.lane-name.text { color: #d8c94f; }
.lane-name.continuity { color: #6fb1d8; }
.lane-name.environment { color: #4fd8b8; }
.lane-name.adherence { color: #d84fa8; }
details { margin-top: 8px; }
summary { cursor: pointer; color: #9a9aa3; font-size: 14px; }
.fam { margin-top: 8px; }
.fam pre { white-space: pre-wrap; font-size: 14px; color: #c4c4cc;
  margin-top: 3px; }
.legend { color: #9a9aa3; font-size: 14px; margin: -16px 0 24px; }
.legend i { position: static; transform: none; display: inline-block;
  width: 10px; height: 10px; border-radius: 2px; margin: 0 4px 0 12px;
  vertical-align: -1px; }
.doomed { font-size: 12px; letter-spacing: .06em; padding: 2px 7px;
  border-radius: 99px; background: #3a1d1d; color: #d87f7f; }
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
});
// Any timestamp or timeline dot jumps its card's video to that moment
// and pauses on the frame.
document.addEventListener('click', function (e) {
  var el = e.target.closest('[data-t]');
  if (!el) return;
  var take = el.closest('.take');
  var v = take && take.querySelector('video');
  if (!v) return;
  e.preventDefault();
  v.pause();
  v.currentTime = parseFloat(el.dataset.t);
});
"""


def find_takes(root):
    """Every take.json sidecar under root, loaded; _clip carries the clip
    path so consumers never re-derive it. Shared with brief."""
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
    # All defects share the one track. Dots that would land on top of each
    # other stack downward instead, so every color stays visible.
    placed = []
    max_row = 0
    for d in sorted(defects, key=lambda d: d["t"]):
        pct = 100 * min(d["t"], duration) / duration
        end_pct = (100 * min(d["t_end"], duration) / duration
                   if d.get("t_end") else pct)
        row = 0
        while any(abs(pct - p) < 1.8 and r == row for p, r in placed):
            row += 1
        placed.append((pct, row))
        max_row = max(max_row, row)
        width = ";width:%.1f%%;border-radius:6px" % (end_pct - pct) \
            if end_pct > pct else ""
        when = ("%s-%ss" % (d["t"], d["t_end"])
                if d.get("t_end") else "%ss" % d["t"])
        tip = " tip-left" if pct < 18 else (
            " tip-right" if pct > 82 else "")
        spans.append(
            '<i class="defect %s%s" style="left:%.1f%%;top:%dpx%s" '
            'data-t="%s" data-note="%s"></i>' % (
                html.escape(d["rule"].split(".")[0]), tip, pct,
                2 + row * 14, width, d["t"],
                html.escape("%s %s (%d): %s" % (
                    when, d["rule"], d["severity"], d["note"]))))
    height = 16 + max_row * 14
    return ('<div class="timeline" style="height:%dpx">%s</div>'
            % (height, "".join(spans)))


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
    if mech.get("motion_smoothness") is not None:
        stats.append("motion %.3f" % mech["motion_smoothness"])
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
        # The timestamp is a link that jumps the card's video to the
        # defect; everything after it is escaped text.
        when = ("%s-%ss" % (d["t"], d["t_end"])
                if d.get("t_end") else "%ss" % d["t"])
        by_family.setdefault(d["rule"].split(".")[0], []).append(
            '<a class="seek" data-t="%s">%s</a> %s' % (
                d["t"], when,
                html.escape("%s (%d): %s" % (
                    d["rule"], d["severity"], d["note"]))))
    groups.extend(sorted(by_family.items()))
    details = ""
    if groups:
        label = "why killed" if verdict == "kill" else "defects"
        body = "".join(
            '<div class="fam"><b class="lane-name %s">%s</b><pre>%s</pre>'
            "</div>" % (html.escape(fam), html.escape(fam),
                        "\n".join(lines_) if fam != "mechanical"
                        else html.escape("\n".join(lines_)))
            for fam, lines_ in groups)
        details = ("<details><summary>%s</summary>%s</details>"
                   % (label, body))
    rank = r.get("rank_in_shot")
    # Display label: kill stays kill; a survivor with findings says
    # "needs review", a survivor with none says "passed". The sidecar
    # verdict enum is unchanged.
    label = verdict if verdict == "kill" else (
        "needs review" if defects else "passed")
    chip = verdict if verdict == "kill" else (
        "review" if defects else "passed")
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
        chip, label,
        html.escape(" · ".join(stats)),
        _timeline(mech, duration, defects), details)


def build(root, output):
    takes = find_takes(root)
    report_dir = os.path.dirname(os.path.abspath(output)) or "."
    by_shot = {}
    for t in takes:
        by_shot.setdefault(t.get("shot") or "untagged", []).append(t)

    sections = []
    doomed_shots = []
    for shot in sorted(by_shot):
        group = sorted(by_shot[shot], key=lambda t: (
            (t.get("review") or {}).get("rank_in_shot") or 10**6))
        kills = sum(1 for t in group
                    if (t.get("review") or {}).get("verdict") == "kill")
        # The breaker recomputes from sidecars, so a report built hours
        # after the watcher stopped flags the same shots.
        state = breaker.assess([t["review"] for t in group
                                if t.get("review")])
        badge = ""
        if state["doomed"]:
            doomed_shots.append(shot)
            badge = ' <span class="doomed">doomed</span>'
        # The heading is linkable (report.html#shot-07): slugged shot name.
        slug = re.sub(r"[^a-z0-9]+", "-", shot.lower()).strip("-") or "shot"
        sections.append('<h2 id="%s">%s · %d takes · %d killed%s</h2>'
                        '<div class="takes">%s</div>' % (
                            slug, html.escape(shot), len(group), kills,
                            badge,
                            "".join(_take_card(t, report_dir)
                                    for t in group)))

    doomed_line = ""
    if doomed_shots:
        doomed_line = (' <span class="doomed">doomed: %s</span> mechanics '
                       "kill nearly every take there; change the recipe "
                       "before generating more."
                       % html.escape(", ".join(doomed_shots)))
    killed = sum(1 for t in takes
                 if (t.get("review") or {}).get("verdict") == "kill")
    doc = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>dailies report</title><style>%s</style></head><body>
<h1>Dailies</h1>
<div class="sub">%d takes reviewed, %d killed, %d to watch.%s
Click any timestamp or timeline dot to jump the clip to that moment.</div>
<div class="legend">timeline:<i class="span black"></i>black
<i class="span freeze"></i>frozen <i class="cut"></i>cut
<i class="defect anatomy"></i>anatomy <i class="defect physics"></i>physics
<i class="defect artifact"></i>artifact <i class="defect text"></i>text
<i class="defect continuity"></i>continuity
<i class="defect environment"></i>environment
<i class="defect adherence"></i>adherence</div>
%s
<script>%s</script></body></html>""" % (
        CSS, len(takes), killed, len(takes) - killed, doomed_line,
        "\n".join(sections), JS)
    with open(output, "w") as f:
        f.write(doc)
    return os.path.abspath(output)
