"""Cost telemetry tests: usage recording against a stub endpoint that
bills every request a fixed token count."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dailies import take, vlm  # noqa: E402
from dailies.cli import main  # noqa: E402

FFMPEG = shutil.which("ffmpeg")

PROMPT_TOKENS = 100
COMPLETION_TOKENS = 20


class StubVlm(BaseHTTPRequestHandler):
    """Judge stub that reports usage on every reply. Checklist rules get
    a yes on question 1 (except physics.contact, all no); legacy prompt
    rules get one severity-5 defect. With flaky set, anatomy.hands
    alternates yes/no across requests so repeat samples disagree."""

    requests = []
    flaky = False
    report_usage = True

    def do_POST(self):
        body = json.loads(self.rfile.read(
            int(self.headers["Content-Length"])))
        type(self).requests.append(body)
        system = body["messages"][0]["content"]
        text = body["messages"][1]["content"][0]["text"]
        if '"answers"' in system:
            if type(self).flaky and "anatomy.hands" in text:
                nth = sum(1 for r in type(self).requests
                          if "anatomy.hands" in
                          r["messages"][1]["content"][0]["text"])
                yes = nth % 2 == 1
            else:
                yes = "physics.contact" not in text
            content = json.dumps({"answers": [
                {"q": 1, "yes": yes, "t": 0.5, "note": "six fingers"},
                {"q": 2, "yes": False}]})
        else:
            content = json.dumps({"defects": [
                {"t": 0.5, "severity": 5, "note": "watermark"}]})
        payload = {"choices": [{"message": {"content": content}}]}
        if type(self).report_usage:
            payload["usage"] = {"prompt_tokens": PROMPT_TOKENS,
                                "completion_tokens": COMPLETION_TOKENS}
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@unittest.skipUnless(FFMPEG, "ffmpeg not on PATH")
class StubEndpointCase(unittest.TestCase):
    """Shared harness: one stub server, one synthetic clip per class."""

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), StubVlm)
        threading.Thread(target=cls.server.serve_forever,
                         daemon=True).start()
        cls.endpoint = "http://127.0.0.1:%d/v1" % cls.server.server_port
        cls.dir = tempfile.mkdtemp(prefix="dailies-cost-test-")
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
        StubVlm.flaky = False
        StubVlm.report_usage = True
        sidecar = take.sidecar_path(self.clip)
        if os.path.exists(sidecar):
            os.unlink(sidecar)


class UsageTests(StubEndpointCase):
    def test_request_returns_content_and_usage(self):
        msgs = [{"role": "system", "content": vlm.CHECKLIST_SYSTEM},
                {"role": "user", "content": [
                    {"type": "text", "text": "Rule 'anatomy.hands'"}]}]
        content, usage = vlm._request(self.endpoint, "m", None, msgs)
        self.assertIn("answers", content)
        self.assertEqual(usage, {"prompt_tokens": PROMPT_TOKENS,
                                 "completion_tokens": COMPLETION_TOKENS})

    def test_request_without_usage_reports_zeros(self):
        StubVlm.report_usage = False
        msgs = [{"role": "system", "content": vlm.CHECKLIST_SYSTEM},
                {"role": "user", "content": [
                    {"type": "text", "text": "Rule 'anatomy.hands'"}]}]
        _, usage = vlm._request(self.endpoint, "m", None, msgs)
        self.assertEqual(usage, {"prompt_tokens": 0,
                                 "completion_tokens": 0})

    def test_screen_accumulates_per_rule_usage(self):
        main(["review", self.clip, "--vlm", self.endpoint])
        u = take.load(self.clip)["review"]["vlm"]["usage"]
        calls = len(StubVlm.requests)
        self.assertGreater(calls, 0)
        self.assertEqual(u["calls"], calls)
        self.assertEqual(u["prompt_tokens"], calls * PROMPT_TOKENS)
        self.assertEqual(u["completion_tokens"],
                         calls * COMPLETION_TOKENS)
        self.assertEqual(u["rules"]["anatomy.hands"],
                         {"calls": 1, "prompt_tokens": PROMPT_TOKENS,
                          "completion_tokens": COMPLETION_TOKENS})
        # A skipped rule made no request and gets no usage entry.
        self.assertNotIn("adherence.prompt", u["rules"])
        # Totals are the sum of the per-rule entries.
        self.assertEqual(sum(r["calls"] for r in u["rules"].values()),
                         u["calls"])

    def test_samples_multiply_per_rule_calls(self):
        main(["review", self.clip, "--vlm", self.endpoint,
              "--samples", "2"])
        u = take.load(self.clip)["review"]["vlm"]["usage"]
        self.assertEqual(u["rules"]["anatomy.hands"]["calls"], 2)
        self.assertEqual(u["rules"]["anatomy.hands"]["prompt_tokens"],
                         2 * PROMPT_TOKENS)

    def test_legacy_prompt_rule_is_billed(self):
        rubric = os.path.join(self.dir, "rubric.json")
        with open(rubric, "w") as f:
            json.dump({"rules": {"brand.no_text": {
                "prompt": "Any legible text or watermark?",
                "fail_at": 3}}}, f)
        main(["review", self.clip, "--vlm", self.endpoint,
              "--rubric", rubric])
        u = take.load(self.clip)["review"]["vlm"]["usage"]
        self.assertEqual(u["rules"]["brand.no_text"]["calls"], 1)
        self.assertEqual(u["prompt_tokens"], PROMPT_TOKENS)

    def test_escalation_usage_recorded_separately(self):
        class StrongStub(StubVlm):
            requests = []
            flaky = False
            report_usage = True
        strong = ThreadingHTTPServer(("127.0.0.1", 0), StrongStub)
        threading.Thread(target=strong.serve_forever,
                         daemon=True).start()
        self.addCleanup(strong.shutdown)
        StubVlm.flaky = True
        main(["review", self.clip, "--vlm", self.endpoint,
              "--samples", "2",
              "--vlm-strong",
              "http://127.0.0.1:%d/v1" % strong.server_port,
              "--vlm-strong-model", "big-vlm"])
        v = take.load(self.clip)["review"]["vlm"]
        # The cheap judge's own spend survives escalation untouched.
        cheap_calls = len(StubVlm.requests)
        self.assertEqual(v["usage"]["calls"], cheap_calls)
        # The strong judge's spend is its own record, one rule, one call.
        self.assertEqual(v["strong_usage"]["calls"], 1)
        self.assertEqual(list(v["strong_usage"]["rules"]),
                         ["anatomy.hands"])
        self.assertEqual(v["strong_usage"]["prompt_tokens"],
                         PROMPT_TOKENS)


class PriceTests(StubEndpointCase):
    IN_RATE = 2.0    # $/Mtok
    OUT_RATE = 10.0  # $/Mtok
    PER_CALL = (PROMPT_TOKENS * IN_RATE
                + COMPLETION_TOKENS * OUT_RATE) / 1e6

    def _prices(self, data=None):
        path = os.path.join(self.dir, "prices.json")
        with open(path, "w") as f:
            json.dump(data if data is not None else {
                "models": {"qwen3-vl": {"input": self.IN_RATE,
                                        "output": self.OUT_RATE}}}, f)
        return path

    def test_prices_write_cost_block(self):
        path = self._prices({
            "models": {"qwen3-vl": {"input": self.IN_RATE,
                                    "output": self.OUT_RATE}},
            "clip": 0.05})
        main(["review", self.clip, "--vlm", self.endpoint,
              "--prices", path])
        r = take.load(self.clip)["review"]
        expect = r["vlm"]["usage"]["calls"] * self.PER_CALL
        self.assertAlmostEqual(r["cost"]["vlm_usd"], expect, places=9)
        self.assertEqual(r["cost"]["clip_usd"], 0.05)
        self.assertAlmostEqual(r["cost"]["total_usd"], expect + 0.05,
                               places=9)
        self.assertNotIn("unpriced_models", r["cost"])

    def test_unpriced_model_is_named_not_zeroed(self):
        path = self._prices({"models": {"someone-else":
                                        {"input": 1, "output": 1}}})
        main(["review", self.clip, "--vlm", self.endpoint,
              "--prices", path])
        c = take.load(self.clip)["review"]["cost"]
        self.assertEqual(c["vlm_usd"], 0.0)
        self.assertEqual(c["unpriced_models"], ["qwen3-vl"])

    def test_clip_price_alone_without_vlm(self):
        path = self._prices({"clip": 0.25})
        main(["review", self.clip, "--prices", path])
        c = take.load(self.clip)["review"]["cost"]
        self.assertEqual(c["vlm_usd"], 0.0)
        self.assertEqual(c["total_usd"], 0.25)

    def test_prices_apply_to_cached_reviews_without_rejudging(self):
        main(["review", self.clip, "--vlm", self.endpoint])
        count = len(StubVlm.requests)
        main(["review", self.clip, "--vlm", self.endpoint,
              "--prices", self._prices()])
        self.assertEqual(len(StubVlm.requests), count)
        c = take.load(self.clip)["review"]["cost"]
        self.assertAlmostEqual(c["vlm_usd"], count * self.PER_CALL,
                               places=9)

    def test_escalation_billed_at_the_strong_models_rate(self):
        class StrongStub(StubVlm):
            requests = []
            flaky = False
        strong = ThreadingHTTPServer(("127.0.0.1", 0), StrongStub)
        threading.Thread(target=strong.serve_forever,
                         daemon=True).start()
        self.addCleanup(strong.shutdown)
        StubVlm.flaky = True
        path = self._prices({"models": {
            "qwen3-vl": {"input": self.IN_RATE, "output": self.OUT_RATE},
            "big-vlm": {"input": 200.0, "output": 1000.0}}})
        main(["review", self.clip, "--vlm", self.endpoint,
              "--samples", "2",
              "--vlm-strong",
              "http://127.0.0.1:%d/v1" % strong.server_port,
              "--vlm-strong-model", "big-vlm", "--prices", path])
        r = take.load(self.clip)["review"]
        cheap = r["vlm"]["usage"]["calls"] * self.PER_CALL
        strong_usd = (r["vlm"]["strong_usage"]["prompt_tokens"] * 200.0
                      + r["vlm"]["strong_usage"]["completion_tokens"]
                      * 1000.0) / 1e6
        self.assertAlmostEqual(r["cost"]["vlm_usd"],
                               cheap + strong_usd, places=9)

    def test_bad_price_file_fails_loudly(self):
        from dailies import cost
        path = self._prices({"models": {"m": 3}})
        with self.assertRaises(RuntimeError):
            cost.load(path)
        # The CLI catches it and exits 2 instead of pricing at zero.
        self.assertEqual(main(["review", self.clip, "--prices", path]), 2)

    def test_watch_loop_writes_cost(self):
        from dailies import watch
        wdir = tempfile.mkdtemp(prefix="dailies-cost-watch-")
        self.addCleanup(shutil.rmtree, wdir)
        clip = os.path.join(wdir, "take.mp4")
        shutil.copy(self.clip, clip)
        stop = threading.Event()
        done = []
        th = threading.Thread(
            target=watch.loop, args=(wdir,),
            kwargs={"interval": 0.2, "settle": 0.2, "stop": stop,
                    "emit": lambda t, c: (done.append(c), stop.set()),
                    "prices": {"clip": 0.25}},
            daemon=True)
        th.start()
        deadline = time.time() + 20
        while time.time() < deadline and not done:
            time.sleep(0.1)
        stop.set()
        th.join(timeout=10)
        self.assertTrue(done)
        self.assertEqual(take.load(clip)["review"]["cost"]["total_usd"],
                         0.25)

    def test_watch_cli_loads_prices(self):
        path = self._prices({"clip": 0.25})
        with mock.patch("dailies.watch.loop") as loop:
            rc = main(["watch", self.dir, "--prices", path])
        self.assertEqual(rc, 0)
        self.assertEqual(loop.call_args[1]["prices"]["clip"], 0.25)


if __name__ == "__main__":
    unittest.main()
