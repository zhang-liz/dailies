"""Envelope pin for the triage-dailies skill.

skills/triage-dailies/SKILL.md hard-codes the review --json shapes it
drives the morning ritual with; this test fails loudly when cli.py
drifts under the skill.
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dailies.cli import main  # noqa: E402

FFMPEG = shutil.which("ffmpeg")


class StubVlm(BaseHTTPRequestHandler):
    """Answers yes on question 1 of every checklist rule, so the vlm
    block always carries at least one defect to pin the shape of."""

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        content = json.dumps({"answers": [
            {"q": 1, "yes": True, "t": 0.5, "note": "pinned"},
            {"q": 2, "yes": False}]})
        payload = json.dumps(
            {"choices": [{"message": {"content": content}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@unittest.skipUnless(FFMPEG, "ffmpeg not on PATH")
class SkillEnvelopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), StubVlm)
        threading.Thread(target=cls.server.serve_forever,
                         daemon=True).start()
        cls.endpoint = "http://127.0.0.1:%d/v1" % cls.server.server_port
        cls.dir = tempfile.mkdtemp(prefix="dailies-skill-test-")
        cls.clip = os.path.join(cls.dir, "take.mp4")
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
             "-i", "testsrc2=size=320x240:rate=8", "-t", "1",
             "-pix_fmt", "yuv420p", cls.clip],
            check=True)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        shutil.rmtree(cls.dir)

    def test_review_json_envelope_keys(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(["review", self.clip, "--vlm", self.endpoint,
                       "--json"])
        self.assertEqual(rc, 0)
        out = json.loads(buf.getvalue())

        for key in ("reviewed", "killed", "takes"):
            self.assertIn(key, out)
        self.assertEqual(out["reviewed"], len(out["takes"]))

        r = out["takes"][0]["review"]
        self.assertIn(r["verdict"], ("keep", "kill", "review"))
        self.assertIsInstance(r["mechanical"]["kill_reasons"], list)
        self.assertIsInstance(r["mechanical"]["candidate_frames"], list)

        vlm = r["vlm"]
        self.assertIsInstance(vlm["uncertain"], list)
        # The evidence pass extracts a frame at each defect's t under
        # its rule name; those keys drifting would blind the skill.
        self.assertTrue(vlm["defects"])
        for d in vlm["defects"]:
            for key in ("t", "rule", "severity", "note"):
                self.assertIn(key, d)


if __name__ == "__main__":
    unittest.main()
