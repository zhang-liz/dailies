"""Social card for the dailies repo, 1280x640, matching the report's palette."""

from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 640
BG = (16, 16, 20)
CARD = (23, 23, 28)
BORDER = (38, 38, 44)
TEXT = (232, 232, 234)
MUTED = (139, 139, 147)
TRACK = (38, 38, 44)
BLACKSPAN = (90, 90, 102)
FREEZE = (79, 125, 216)
CUT = (216, 162, 79)
DEFECT = (216, 86, 79)
GREEN_BG, GREEN_FG = (29, 58, 42), (127, 216, 162)
RED_BG, RED_FG = (58, 29, 29), (216, 127, 127)


def font(size, bold=False, mono=False):
    if mono:
        return ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", size,
                                  index=1 if bold else 0)
    return ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", size,
                              index=1 if bold else 0)


img = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(img)

M = 84
d.text((M, 92), "dailies", font=font(118, bold=True), fill=TEXT)
d.text((M, 248), "Triage for AI-generated video takes.",
       font=font(36), fill=TEXT)
d.text((M, 300), "Kill the dead, rank the survivors.",
       font=font(36), fill=MUTED)

d.text((M, 388), "$ dailies review ./takes",
       font=font(26, mono=True), fill=GREEN_FG)
d.text((M, 428), "reviewed 40 takes, killed 34",
       font=font(26, mono=True), fill=MUTED)


def chip(x, y, label, bg, fg):
    f = font(22, bold=True)
    tw = d.textlength(label, font=f)
    d.rounded_rectangle([x, y, x + tw + 28, y + 38], radius=19, fill=bg)
    d.text((x + 14, y + 7), label, font=f, fill=fg)
    return x + tw + 28


def timeline(x, y, w, spans, cuts, defects):
    d.rounded_rectangle([x, y, x + w, y + 12], radius=6, fill=TRACK)
    for s0, s1, color in spans:
        d.rounded_rectangle([x + w * s0, y, x + w * s1, y + 12],
                            radius=6, fill=color)
    for c in cuts:
        d.rectangle([x + w * c - 2, y - 5, x + w * c + 2, y + 17], fill=CUT)
    for t in defects:
        cx = x + w * t
        d.ellipse([cx - 8, y - 12, cx + 8, y + 4], fill=DEFECT)


# Three take rows: two kills, one ranked survivor.
rows = [
    ("take-031.mp4", "REVIEW  #1", GREEN_BG, GREEN_FG,
     [], [], [0.62]),
    ("take-014.mp4", "KILL", RED_BG, RED_FG,
     [(0.30, 0.95, FREEZE)], [], [0.35, 0.78]),
    ("take-022.mp4", "KILL", RED_BG, RED_FG,
     [(0.0, 0.55, BLACKSPAN)], [0.68], []),
]
CX, CW = 700, 496
y = 118
for name, verdict, vbg, vfg, spans, cuts, defects in rows:
    d.rounded_rectangle([CX, y, CX + CW, y + 128], radius=14,
                        fill=CARD, outline=BORDER, width=2)
    d.text((CX + 26, y + 22), name, font=font(26, mono=True), fill=TEXT)
    f = font(22, bold=True)
    tw = d.textlength(verdict, f)
    chip(CX + CW - tw - 54, y + 18, verdict, vbg, vfg)
    timeline(CX + 26, y + 84, CW - 52, spans, cuts, defects)
    y += 152

d.text((M, H - 74), "github.com/zhang-liz/dailies",
       font=font(26, mono=True), fill=MUTED)
tag = "pip install video-dailies"
f = font(26, mono=True)
d.text((W - M - d.textlength(tag, f), H - 74), tag, font=f, fill=MUTED)

img.save("card.png")
print("saved card.png")
