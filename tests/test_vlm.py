"""VLM stage tests against a stub OpenAI-compatible endpoint."""

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

from dailies import rubric, take, vlm  # noqa: E402
from dailies.cli import main  # noqa: E402

FFMPEG = shutil.which("ffmpeg")


class StubVlm(BaseHTTPRequestHandler):
    """Answers every rule with one severity-5 hand defect, except
    physics.contact, which passes."""

    requests = []

    def do_POST(self):
        body = json.loads(self.rfile.read(
            int(self.headers["Content-Length"])))
        type(self).requests.append(body)
        text = body["messages"][1]["content"][0]["text"]
        if "physics.contact" in text:
            content = '{"defects": []}'
        else:
            content = ('Here you go:\n```json\n{"defects": [{"t": 0.5, '
                       '"severity": 5, "note": "six fingers"}]}\n```')
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
class VlmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), StubVlm)
        threading.Thread(target=cls.server.serve_forever,
                         daemon=True).start()
        cls.endpoint = "http://127.0.0.1:%d/v1" % cls.server.server_port
        cls.dir = tempfile.mkdtemp(prefix="dailies-vlm-test-")
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

    def setUp(self):
        StubVlm.requests = []
        sidecar = take.sidecar_path(self.clip)
        if os.path.exists(sidecar):
            os.unlink(sidecar)

    def test_review_with_vlm_kills_on_fail_at(self):
        rc = main(["review", self.clip, "--vlm", self.endpoint])
        self.assertEqual(rc, 0)
        t = take.load(self.clip)
        r = t["review"]
        self.assertEqual(r["verdict"], "kill")
        self.assertTrue(any("anatomy.hands" in k
                            for k in r["mechanical"]["kill_reasons"]))
        rules = {d["rule"] for d in r["vlm"]["defects"]}
        self.assertIn("anatomy.hands", rules)
        self.assertNotIn("physics.contact", rules)
        # adherence.prompt needs recipe.prompt_text; bare clip has none
        self.assertIn("adherence.prompt", r["vlm"]["skipped"])

    def test_adherence_runs_when_recipe_present(self):
        t = take.load(self.clip)
        t["recipe"] = {"prompt_text": "slow dolly toward the window"}
        take.save(self.clip, t)
        main(["review", self.clip, "--vlm", self.endpoint])
        r = take.load(self.clip)["review"]
        self.assertNotIn("adherence.prompt", r["vlm"]["skipped"])
        sent = json.dumps(StubVlm.requests)
        self.assertIn("slow dolly toward the window", sent)

    def test_vlm_results_cached(self):
        main(["review", self.clip, "--vlm", self.endpoint])
        count = len(StubVlm.requests)
        main(["review", self.clip, "--vlm", self.endpoint])
        self.assertEqual(len(StubVlm.requests), count)

    def test_defect_parser_tolerates_prose(self):
        self.assertEqual(
            vlm._parse_defects('yes {"defects":[{"t":1,"severity":9,'
                               '"note":"x"}]} ok'),
            [{"t": 1.0, "severity": 5, "note": "x"}])
        self.assertIsNone(vlm._parse_defects("no json here"))

    def test_custom_rubric_json(self):
        path = os.path.join(self.dir, "rubric.json")
        with open(path, "w") as f:
            json.dump({"rules": {"brand.no_text": {
                "prompt": "Any legible text or watermark?",
                "fail_at": 3}}}, f)
        main(["review", self.clip, "--vlm", self.endpoint,
              "--rubric", path])
        r = take.load(self.clip)["review"]
        self.assertEqual({d["rule"] for d in r["vlm"]["defects"]},
                         {"brand.no_text"})

    def test_rubric_rejects_promptless_rule(self):
        path = os.path.join(self.dir, "bad.json")
        with open(path, "w") as f:
            json.dump({"rules": {"x": {"fail_at": 3}}}, f)
        with self.assertRaises(ValueError):
            rubric.load(path)


if __name__ == "__main__":
    unittest.main()


class MergeTests(unittest.TestCase):
    def test_per_frame_repeats_collapse_to_a_range(self):
        d = lambda t, sev=4: {"t": t, "severity": sev, "rule": "text.legibility",
                              "note": "Sign reads 5-5PM"}
        merged = vlm._merge([d(3.875), d(3.917), d(4.042), d(4.083),
                             d(4.125), d(4.167), d(4.333)])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["t"], 3.875)
        self.assertEqual(merged[0]["t_end"], 4.333)

    def test_distant_and_cross_rule_defects_stay_separate(self):
        merged = vlm._merge([
            {"t": 0.0, "severity": 4, "rule": "text.legibility", "note": "a"},
            {"t": 3.0, "severity": 4, "rule": "text.legibility", "note": "b"},
            {"t": 3.0, "severity": 3, "rule": "anatomy.hands", "note": "c"}])
        self.assertEqual(len(merged), 3)
        self.assertTrue(all("t_end" not in m for m in merged))

    def test_merge_keeps_highest_severity_note(self):
        merged = vlm._merge([
            {"t": 1.0, "severity": 3, "rule": "r.x", "note": "mild"},
            {"t": 1.5, "severity": 5, "rule": "r.x", "note": "severe"}])
        self.assertEqual(merged[0]["severity"], 5)
        self.assertEqual(merged[0]["note"], "severe")
