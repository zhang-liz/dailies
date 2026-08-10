"""Regen-to-green defense over fabricated sidecars and a stub judge:
ancestor kill rules, the intent guard, deterministic audit picks, and
the judge-health gate."""

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

from dailies import defense, pipeline, take  # noqa: E402

FFMPEG = shutil.which("ffmpeg")


def sidecar(ident, parent=None, verdict="kill", reasons=None,
            prompt=None, recipe="auto"):
    """A fabricated take dict; recipe defaults to one carrying prompt."""
    if recipe == "auto":
        recipe = {"prompt_text": prompt} if prompt is not None else None
    return {"take_id": "sha256:%064x" % ident, "shot": "shot-07",
            "parent": parent, "recipe": recipe,
            "review": {"mechanical": {"kill_reasons": reasons or []},
                       "vlm": None, "verdict": verdict}}


HANDS = "anatomy.hands severity 4 at 0.5s: six fingers"
MORPH = "artifact.morphing severity 4 at 1.0s: cup dissolves"


class LineageIndexTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dailies-defense-")
        self.addCleanup(shutil.rmtree, self.dir)

    def write(self, name, doc):
        with open(os.path.join(self.dir, name), "w") as f:
            if isinstance(doc, str):
                f.write(doc)
            else:
                json.dump(doc, f)

    def test_indexes_sidecars_without_clips(self):
        # The killed ancestor's clip is purged; its sidecar remains.
        self.write("dead.mp4.take.json", sidecar(1))
        self.write("alive.mp4.take.json", sidecar(2, verdict="review"))
        self.write("not-a-sidecar.json", sidecar(3))
        idx = defense.lineage_index(self.dir)
        self.assertEqual(set(idx), {"sha256:%064x" % 1,
                                    "sha256:%064x" % 2})

    def test_tolerates_junk_and_missing_ids(self):
        self.write("bad.mp4.take.json", "{not json")
        self.write("list.mp4.take.json", [1, 2])
        self.write("noid.mp4.take.json", {"take_id": None})
        self.assertEqual(defense.lineage_index(self.dir), {})
        self.assertEqual(defense.lineage_index(
            os.path.join(self.dir, "gone")), {})


class AncestryTests(unittest.TestCase):
    def test_walks_nearest_first_and_stops_at_missing(self):
        root = sidecar(1)
        mid = sidecar(2, parent=root["take_id"])
        child = sidecar(3, parent=mid["take_id"])
        by_id = {t["take_id"]: t for t in (root, mid)}
        self.assertEqual([a["take_id"] for a in
                          defense.ancestors(child, by_id)],
                         [mid["take_id"], root["take_id"]])
        self.assertEqual(list(defense.ancestors(child, {})), [])

    def test_cycle_ends_the_walk(self):
        a = sidecar(1, parent="sha256:%064x" % 2)
        b = sidecar(2, parent=a["take_id"])
        by_id = {t["take_id"]: t for t in (a, b)}
        self.assertEqual(len(list(defense.ancestors(a, by_id))), 2)

    def test_kill_rules_collect_across_the_chain(self):
        root = sidecar(1, reasons=[HANDS])
        mid = sidecar(2, parent=root["take_id"], reasons=[MORPH])
        child = sidecar(3, parent=mid["take_id"], verdict="review")
        by_id = {t["take_id"]: t for t in (root, mid)}
        self.assertEqual(defense.parent_kill_rules(child, by_id),
                         ["anatomy.hands", "artifact.morphing"])

    def test_mechanical_and_calibrated_kills_name_no_rule(self):
        root = sidecar(1, reasons=[
            "black for 1.0s from 0.0s",
            "calibrated kill score 3.10 > 2.00 (false-kill rate <= 0.05)"])
        child = sidecar(2, parent=root["take_id"], verdict="review")
        by_id = {root["take_id"]: root}
        self.assertEqual(defense.parent_kill_rules(child, by_id), [])

    def test_surviving_ancestors_contribute_nothing(self):
        root = sidecar(1, verdict="review", reasons=[])
        child = sidecar(2, parent=root["take_id"], verdict="review")
        by_id = {root["take_id"]: root}
        self.assertEqual(defense.parent_kill_rules(child, by_id), [])


class IntentGuardTests(unittest.TestCase):
    def test_root_prompt_wins_over_patched_child(self):
        root = sidecar(1, prompt="the original direction")
        mid = sidecar(2, parent=root["take_id"], prompt="patched once")
        child = sidecar(3, parent=mid["take_id"],
                        prompt="patched twice", verdict="review")
        by_id = {t["take_id"]: t for t in (root, mid)}
        judged, guarded = defense.intent_guard(child, by_id)
        self.assertTrue(guarded)
        self.assertEqual(judged["recipe"]["prompt_text"],
                         "the original direction")
        # The take itself is untouched; only the judging copy swaps.
        self.assertEqual(child["recipe"]["prompt_text"], "patched twice")

    def test_matching_or_absent_root_prompt_swaps_nothing(self):
        root = sidecar(1, prompt="same words")
        child = sidecar(2, parent=root["take_id"], prompt="same words",
                        verdict="review")
        by_id = {root["take_id"]: root}
        judged, guarded = defense.intent_guard(child, by_id)
        self.assertIs(judged, child)
        self.assertFalse(guarded)
        bare = sidecar(3, parent="sha256:%064x" % 9, verdict="review")
        self.assertEqual(defense.intent_guard(bare, {}), (bare, False))

    def test_recipeless_child_gains_the_root_prompt(self):
        root = sidecar(1, prompt="the original direction")
        child = sidecar(2, parent=root["take_id"], verdict="review",
                        recipe=None)
        judged, guarded = defense.intent_guard(
            child, {root["take_id"]: root})
        self.assertTrue(guarded)
        self.assertEqual(judged["recipe"]["prompt_text"],
                         "the original direction")


class AuditPickTests(unittest.TestCase):
    def test_deterministic_and_bounded(self):
        tid = "sha256:%064x" % 7
        self.assertEqual(defense.audit_pick(tid, 0.15),
                         defense.audit_pick(tid, 0.15))
        self.assertFalse(defense.audit_pick(tid, 0))
        self.assertFalse(defense.audit_pick(tid, None))
        self.assertFalse(defense.audit_pick(None, 1.0))
        self.assertTrue(defense.audit_pick(tid, 1.0))

    def test_rate_lands_near_the_asked_fraction(self):
        ids = ["sha256:%064x" % i for i in range(400)]
        picked = sum(defense.audit_pick(t, 0.15) for t in ids)
        # Deterministic given these ids; the band just documents that
        # the hash spreads instead of clumping.
        self.assertGreater(picked, 30)
        self.assertLess(picked, 90)
        self.assertEqual(sum(defense.audit_pick(t, 1.0) for t in ids),
                         400)


class StubJudge(BaseHTTPRequestHandler):
    """A checklist judge answering no to everything, except rules named
    in `yes`; with sampled_only set, the yes appears only on sampled
    (temperature > 0) requests, modeling a defect a single cheap ask
    misses and repeat asks catch."""

    requests = []
    yes = ()
    sampled_only = False

    def do_POST(self):
        body = json.loads(self.rfile.read(
            int(self.headers["Content-Length"])))
        type(self).requests.append(body)
        text = body["messages"][1]["content"][0]["text"]
        hit = (any(rule in text for rule in type(self).yes)
               and (body.get("temperature", 0) > 0
                    or not type(self).sampled_only))
        content = json.dumps({"answers": [
            {"q": 1, "yes": bool(hit), "t": 0.5, "note": "seen"},
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


def request_texts(requests):
    return [r["messages"][1]["content"][0]["text"] for r in requests]


@unittest.skipUnless(FFMPEG, "ffmpeg not on PATH")
class PipelineDefenseBase(unittest.TestCase):
    """One stub judge and one synthetic clip per test class."""

    handler = StubJudge

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), cls.handler)
        threading.Thread(target=cls.server.serve_forever,
                         daemon=True).start()
        cls.endpoint = "http://127.0.0.1:%d/v1" % cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        self.handler.requests = []
        self.handler.yes = ()
        self.handler.sampled_only = False
        self.dir = tempfile.mkdtemp(prefix="dailies-defense-e2e-")
        self.addCleanup(shutil.rmtree, self.dir)
        self.clip = os.path.join(self.dir, "take-002.mp4")
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
             "-i", "testsrc2=size=320x240:rate=8", "-t", "1",
             "-pix_fmt", "yuv420p", self.clip],
            check=True)

    def write_parent(self, ident=1, reasons=(HANDS,), prompt=None,
                     parent=None):
        """A purged ancestor: sidecar on disk, no clip."""
        t = sidecar(ident, parent=parent, reasons=list(reasons),
                    prompt=prompt)
        path = os.path.join(self.dir, "take-%03d.mp4.take.json" % ident)
        with open(path, "w") as f:
            json.dump(t, f)
        return t

    def prep_child(self, parent_id, prompt=None, recipe="auto"):
        t = take.load(self.clip)
        t["parent"] = parent_id
        if recipe == "auto":
            recipe = ({"prompt_text": prompt}
                      if prompt is not None else None)
        t["recipe"] = recipe
        take.save(self.clip, t)

    def review(self, **kwargs):
        kwargs.setdefault("vlm_endpoint", self.endpoint)
        t, _ = pipeline.review_clip(self.clip, **kwargs)
        return t


class IntentGuardPipelineTests(PipelineDefenseBase):
    def test_judge_sees_the_root_prompt_not_the_patch(self):
        parent = self.write_parent(reasons=["black for 1.0s from 0.0s"],
                                   prompt="the original direction")
        self.prep_child(parent["take_id"], prompt="patched prompt")
        t = self.review()
        sent = request_texts(self.handler.requests)
        self.assertTrue(any("the original direction" in s for s in sent))
        self.assertFalse(any("patched prompt" in s for s in sent))
        self.assertTrue(t["review"]["vlm"]["root_prompt"])
        # The sidecar's own recipe stays verbatim.
        self.assertEqual(take.load(self.clip)["recipe"]["prompt_text"],
                         "patched prompt")
        self.assertEqual(t["review"]["verdict"], "review")

    def test_first_generation_take_judges_its_own_prompt(self):
        self.prep_child(None, prompt="my own words")
        t = self.review()
        sent = request_texts(self.handler.requests)
        self.assertTrue(any("my own words" in s for s in sent))
        self.assertNotIn("root_prompt", t["review"]["vlm"])


class JudgeGateTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dailies-gate-")
        self.addCleanup(shutil.rmtree, self.dir)
        self.path = os.path.join(self.dir, "dailies-judge-check.json")

    def history(self, kappa, created="2026-08-08T00:00:00Z"):
        run = {"created": created}
        if kappa is not None:
            run["kappa"] = kappa
        with open(self.path, "w") as f:
            json.dump({"runs": [{"kappa": 0.9}, run]}, f)

    def test_absent_history_refuses_and_names_the_fix(self):
        ok, why = defense.judge_gate([self.path])
        self.assertFalse(ok)
        self.assertIn("dailies judge-check", why)
        self.assertIn("--allow-unchecked-judge", why)

    def test_empty_runs_refuse(self):
        with open(self.path, "w") as f:
            json.dump({"runs": []}, f)
        ok, why = defense.judge_gate([self.path])
        self.assertFalse(ok)
        self.assertIn("no judge-check runs", why)

    def test_last_run_rules_not_the_best_run(self):
        self.history(0.2)
        ok, why = defense.judge_gate([self.path])
        self.assertFalse(ok)
        self.assertIn("0.20", why)
        self.assertIn("0.60", why)
        self.assertIn("2026-08-08", why)

    def test_healthy_kappa_passes_and_floor_is_tunable(self):
        self.history(0.7)
        self.assertEqual(defense.judge_gate([self.path]), (True, None))
        ok, _ = defense.judge_gate([self.path], min_kappa=0.8)
        self.assertFalse(ok)

    def test_kappa_free_run_refuses(self):
        self.history(None)
        ok, why = defense.judge_gate([self.path])
        self.assertFalse(ok)
        self.assertIn("no kappa", why)

    def test_first_existing_path_answers(self):
        other = os.path.join(self.dir, "elsewhere.json")
        self.history(0.9)
        ok, _ = defense.judge_gate([other, self.path])
        self.assertTrue(ok)

    def test_unparsable_history_refuses(self):
        with open(self.path, "w") as f:
            f.write("{broken")
        ok, why = defense.judge_gate([self.path])
        self.assertFalse(ok)
        self.assertIn("not JSON", why)


if __name__ == "__main__":
    unittest.main()
