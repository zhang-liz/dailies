"""dailies verdict: the single-take gate every regen loop branches on."""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dailies import take  # noqa: E402
from dailies.cli import main  # noqa: E402

FFMPEG = shutil.which("ffmpeg")


def gen(path, lavfi, seconds=1, fps=8):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", lavfi, "-t", str(seconds), "-r", str(fps),
         "-pix_fmt", "yuv420p", path],
        check=True)


class StubVlm(BaseHTTPRequestHandler):
    """Answers yes to question 1 of every checklist rule, so a
    mechanically clean clip picks up VLM defects."""

    def do_POST(self):
        content = json.dumps({"answers": [
            {"q": 1, "yes": True, "t": 0.5, "note": "bad"},
            {"q": 2, "yes": False}]})
        payload = json.dumps({
            "choices": [{"message": {"content": content}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@unittest.skipUnless(FFMPEG, "ffmpeg not on PATH")
class VerdictTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), StubVlm)
        threading.Thread(target=cls.server.serve_forever,
                         daemon=True).start()
        cls.endpoint = "http://127.0.0.1:%d/v1" % cls.server.server_port
        cls.dir = tempfile.mkdtemp(prefix="dailies-verdict-test-")
        cls.good = os.path.join(cls.dir, "good.mp4")
        cls.dead = os.path.join(cls.dir, "dead.mp4")
        gen(cls.good, "testsrc2=size=320x240:rate=8")
        gen(cls.dead, "color=c=black:size=320x240:rate=8")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        shutil.rmtree(cls.dir)

    def verdict(self, argv):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = main(["verdict"] + argv)
        lines = [l for l in out.getvalue().splitlines() if l]
        self.assertEqual(len(lines), 1, "exactly one JSON line")
        return rc, json.loads(lines[0])

    def setUp(self):
        for clip in (self.good, self.dead):
            sidecar = take.sidecar_path(clip)
            if os.path.exists(sidecar):
                os.unlink(sidecar)

    def test_review_exits_zero(self):
        rc, line = self.verdict([self.good])
        self.assertEqual(rc, 0)
        self.assertEqual(line["verdict"], "review")
        self.assertEqual(line["clip"], self.good)
        self.assertEqual(line["kill_reasons"], [])

    def test_mechanical_kill_exits_three(self):
        rc, line = self.verdict([self.dead])
        self.assertEqual(rc, 3)
        self.assertEqual(line["verdict"], "kill")
        self.assertTrue(line["kill_reasons"])

    def test_missing_clip_exits_two(self):
        with redirect_stderr(io.StringIO()):
            rc = main(["verdict", os.path.join(self.dir, "nope.mp4")])
        self.assertEqual(rc, 2)

    def test_vlm_defects_kill_through_the_gate(self):
        rc, line = self.verdict([self.good, "--vlm", self.endpoint])
        self.assertEqual(rc, 3)
        self.assertEqual(line["verdict"], "kill")
        self.assertTrue(take.load(self.good)["review"]["vlm"]["defects"])

    def test_calibration_flag_vetoes_the_fail_at_kill(self):
        # Lambda above every defect score demotes the kill to review.
        cal = os.path.join(self.dir, "cal.json")
        with open(cal, "w") as f:
            json.dump({"alpha": 0.05, "lambda": 5.5}, f)
        rc, line = self.verdict([self.good, "--vlm", self.endpoint,
                                 "--force", "--calibration", cal])
        self.assertEqual(rc, 0)
        self.assertEqual(line["verdict"], "review")

    def test_shot_flag_lands_in_the_line(self):
        rc, line = self.verdict([self.good, "--shot", "shot-42",
                                 "--force"])
        self.assertEqual(rc, 0)
        self.assertEqual(line["shot"], "shot-42")


if __name__ == "__main__":
    unittest.main()
