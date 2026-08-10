"""Judge regression check tests against a stub judge that kills
everything: agreement is then a function of the gold labels alone."""

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

from dailies import judgecheck  # noqa: E402
from dailies.cli import main  # noqa: E402

FFMPEG = shutil.which("ffmpeg")


class KillsEverything(BaseHTTPRequestHandler):
    def do_POST(self):
        payload = json.dumps({"choices": [{"message": {"content": json.dumps(
            {"answers": [{"q": 1, "yes": True, "t": 0.5,
                          "note": "bad"}]})}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


class KappaTests(unittest.TestCase):
    def test_kappa_math(self):
        agree = [("kill", "kill"), ("pass", "pass")] * 5
        self.assertEqual(judgecheck.kappa(agree), 1.0)
        # Judge kills everything: chance-corrected agreement is 0.
        allkill = [("kill", "kill"), ("pass", "kill")] * 5
        self.assertEqual(judgecheck.kappa(allkill), 0.0)
        self.assertIsNone(judgecheck.kappa([]))


@unittest.skipUnless(FFMPEG, "ffmpeg not on PATH")
class JudgeCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0),
                                         KillsEverything)
        threading.Thread(target=cls.server.serve_forever,
                         daemon=True).start()
        cls.endpoint = "http://127.0.0.1:%d/v1" % cls.server.server_port
        cls.dir = tempfile.mkdtemp(prefix="dailies-jc-test-")
        for name in ("badtake.mp4", "goodtake.mp4"):
            subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                 "-i", "testsrc2=size=320x240:rate=8", "-t", "1",
                 "-pix_fmt", "yuv420p", os.path.join(cls.dir, name)],
                check=True)
        main(["gold", "add", os.path.join(cls.dir, "badtake.mp4"),
              "--label", "kill"])
        main(["gold", "add", os.path.join(cls.dir, "goodtake.mp4"),
              "--label", "pass"])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        shutil.rmtree(cls.dir)

    def test_run_reports_agreement_and_history_delta(self):
        history = os.path.join(self.dir, "history.json")
        rc = main(["judge-check", self.dir, "--vlm", self.endpoint,
                   "--history", history, "--json"])
        self.assertEqual(rc, 0)
        runs = json.load(open(history))["runs"]
        self.assertEqual(len(runs), 1)
        # Kills everything: the kill label agrees, the pass label is a
        # false kill.
        self.assertEqual(runs[0]["n"], 2)
        self.assertEqual(runs[0]["agreement"], 0.5)
        self.assertEqual(runs[0]["false_kills"], 1)
        self.assertEqual(runs[0]["missed_kills"], 0)
        # Sidecars stay untouched by the check: no review was written.
        t = json.load(open(os.path.join(self.dir,
                                        "badtake.mp4.take.json")))
        self.assertIsNone(t.get("review"))
        # Second run appends and the kappa gate trips.
        rc = main(["judge-check", self.dir, "--vlm", self.endpoint,
                   "--history", history, "--fail-below", "0.9"])
        self.assertEqual(rc, 1)
        self.assertEqual(len(json.load(open(history))["runs"]), 2)


if __name__ == "__main__":
    unittest.main()
