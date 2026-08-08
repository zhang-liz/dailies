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


if __name__ == "__main__":
    unittest.main()
