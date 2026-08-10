"""review --ndjson: the streaming half of the orchestrator contract."""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dailies import take, watch  # noqa: E402
from dailies.cli import main  # noqa: E402

FFMPEG = shutil.which("ffmpeg")


def gen(path, lavfi, seconds=1, fps=8):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", lavfi, "-t", str(seconds), "-r", str(fps),
         "-pix_fmt", "yuv420p", path],
        check=True)


@unittest.skipUnless(FFMPEG, "ffmpeg not on PATH")
class NdjsonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="dailies-ndjson-test-")
        cls.shot = os.path.join(cls.dir, "shot-01")
        os.makedirs(cls.shot)
        cls.good = os.path.join(cls.shot, "good.mp4")
        cls.dead = os.path.join(cls.shot, "dead.mp4")
        gen(cls.good, "testsrc2=size=320x240:rate=8")
        gen(cls.dead, "color=c=black:size=320x240:rate=8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir)

    def review(self, *extra):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = main(["review", self.dir, "--ndjson"] + list(extra))
        self.assertEqual(rc, 0)
        return [json.loads(line) for line in
                out.getvalue().splitlines() if line]

    def test_one_line_per_clip_plus_summary(self):
        lines = self.review()
        self.assertEqual(len(lines), 3)
        clips, summary = lines[:-1], lines[-1]
        self.assertEqual({l["clip"] for l in clips},
                         {self.good, self.dead})
        verdicts = {l["clip"]: l["verdict"] for l in clips}
        self.assertEqual(verdicts[self.dead], "kill")
        self.assertEqual(verdicts[self.good], "review")
        dead = next(l for l in clips if l["clip"] == self.dead)
        self.assertTrue(dead["kill_reasons"])
        self.assertEqual(dead["shot"], "shot-01")
        self.assertEqual(summary, {"reviewed": 2, "killed": 1})

    def test_lines_share_the_watch_emit_shape(self):
        lines = self.review()
        for line in lines[:-1]:
            t = take.load(line["clip"])
            expected = watch.serialize(t, line["clip"])
            self.assertEqual(set(line), set(expected))
            for key in ("clip", "shot", "verdict", "kill_reasons"):
                self.assertEqual(line[key], expected[key], key)

    def test_summary_line_has_no_clip_key(self):
        self.assertNotIn("clip", self.review()[-1])

    def test_json_and_ndjson_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit) as cm, \
                redirect_stderr(io.StringIO()):
            main(["review", self.dir, "--json", "--ndjson"])
        self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
