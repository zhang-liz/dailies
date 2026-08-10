import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dailies.cli import main  # noqa: E402

FFMPEG = shutil.which("ffmpeg")


def gen(path, lavfi, seconds=1, fps=8):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", lavfi, "-t", str(seconds), "-r", str(fps),
         "-pix_fmt", "yuv420p", path],
        check=True)


@unittest.skipUnless(FFMPEG, "ffmpeg not on PATH")
class ReportTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dailies-report-test-")
        shot = os.path.join(self.dir, "shot-01")
        os.makedirs(shot)
        gen(os.path.join(shot, "good.mp4"), "testsrc2=size=320x240:rate=8")
        gen(os.path.join(shot, "dead.mp4"), "color=c=black:size=320x240:rate=8")
        main(["review", self.dir])

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_report_builds(self):
        out = os.path.join(self.dir, "report.html")
        rc = main(["report", self.dir, "-o", out])
        self.assertEqual(rc, 0)
        page = open(out).read()
        self.assertIn("shot-01", page)
        self.assertIn("2 takes", page)
        self.assertIn("1 killed", page)
        self.assertIn("why killed", page)
        # video srcs resolve relative to the report file
        self.assertIn('src="shot-01/good.mp4"', page)
        self.assertNotIn("—", page)

    def test_report_marks_vlm_defects(self):
        from dailies import take
        clip = os.path.join(self.dir, "shot-01", "good.mp4")
        t = take.load(clip)
        t["review"]["vlm"] = {"engine": "stub", "defects": [
            {"t": 0.5, "severity": 4, "rule": "anatomy.hands",
             "note": "six fingers"}], "skipped": [], "unparsed": []}
        take.save(clip, t)
        out = os.path.join(self.dir, "report.html")
        main(["report", self.dir, "-o", out])
        page = open(out).read()
        self.assertIn('class="defect anatomy"', page)
        self.assertIn("anatomy.hands (4): six fingers", page)
        self.assertIn("1 defects", page)
        # survivor with defects gets a details block labeled defects
        self.assertIn("<summary>defects</summary>", page)


class DoomedReportTests(unittest.TestCase):
    # Fabricated sidecars only: the report reads sidecars, not clips, so
    # no ffmpeg is needed to pin the doomed header.

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dailies-doomrep-test-")

    def tearDown(self):
        shutil.rmtree(self.dir)

    def sidecar(self, shot, name, verdict, rank):
        d = os.path.join(self.dir, shot)
        os.makedirs(d, exist_ok=True)
        reasons = ["black for 100% of clip"] if verdict == "kill" else []
        t = {"take_id": "sha256:%s" % name, "shot": shot,
             "output": {"file": "%s/%s" % (shot, name)},
             "review": {"mechanical": {"kill_reasons": reasons},
                        "verdict": verdict, "rank_in_shot": rank}}
        with open(os.path.join(d, name + ".take.json"), "w") as f:
            json.dump(t, f)

    def test_doomed_shot_flagged_in_header(self):
        for i in range(8):
            self.sidecar("shot-66", "dead-%d.mp4" % i, "kill", i + 1)
        self.sidecar("shot-01", "good.mp4", "review", 1)
        out = os.path.join(self.dir, "report.html")
        main(["report", self.dir, "-o", out])
        page = open(out).read()
        self.assertIn('<span class="doomed">doomed: shot-66</span>', page)
        self.assertIn("change the recipe", page)
        # the doomed shot's heading gets the badge, the healthy one not
        self.assertEqual(page.count('<span class="doomed">doomed</span>'),
                         1)
        self.assertNotIn("—", page)

    def test_no_doomed_line_when_all_healthy(self):
        self.sidecar("shot-01", "good.mp4", "review", 1)
        self.sidecar("shot-01", "dead.mp4", "kill", 2)
        out = os.path.join(self.dir, "report.html")
        main(["report", self.dir, "-o", out])
        # the class definition stays in the CSS; no badge is rendered
        self.assertNotIn('<span class="doomed">', open(out).read())


if __name__ == "__main__":
    unittest.main()
