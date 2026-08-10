"""Gold label tests: recording and collecting human verdicts."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dailies import gold, take  # noqa: E402
from dailies.cli import main  # noqa: E402

FFMPEG = shutil.which("ffmpeg")


@unittest.skipUnless(FFMPEG, "ffmpeg not on PATH")
class GoldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="dailies-gold-test-")
        cls.good = os.path.join(cls.dir, "good.mp4")
        cls.bad = os.path.join(cls.dir, "bad.mp4")
        for clip in (cls.good, cls.bad):
            subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                 "-i", "testsrc2=size=320x240:rate=8", "-t", "1",
                 "-pix_fmt", "yuv420p", clip],
                check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir)

    def test_add_and_collect(self):
        rc = main(["gold", "add", self.good, "--label", "pass"])
        self.assertEqual(rc, 0)
        main(["gold", "add", self.bad, "--label", "kill"])
        labeled = dict(
            (os.path.basename(c), t["gold"]["label"])
            for c, t in gold.collect(self.dir))
        self.assertEqual(labeled, {"good.mp4": "pass", "bad.mp4": "kill"})

    def test_label_survives_review(self):
        main(["gold", "add", self.good, "--label", "pass"])
        main(["review", self.good, "--force"])
        self.assertEqual(take.load(self.good)["gold"]["label"], "pass")

    def test_bad_label_rejected(self):
        with self.assertRaises(ValueError):
            gold.add(self.good, "maybe")


if __name__ == "__main__":
    unittest.main()
