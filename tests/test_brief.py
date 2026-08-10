"""Brief tests over fabricated sidecars; brief never opens a clip."""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dailies import brief  # noqa: E402
from dailies.cli import main  # noqa: E402


def sidecar(dirpath, name, **fields):
    """Fabricate a sidecar; callers override any block."""
    t = {"take_id": "sha256:%s" % name, "shot": None, "parent": None,
         "created": None, "output": {"file": name}, "recipe": None,
         "review": None}
    t.update(fields)
    os.makedirs(dirpath, exist_ok=True)
    with open(os.path.join(dirpath, name + ".take.json"), "w") as f:
        json.dump(t, f)
    return t


def review(verdict, kill_reasons=(), defects=(), rank=None):
    return {"mechanical": {"kill_reasons": list(kill_reasons)},
            "vlm": ({"engine": "stub", "defects": list(defects)}
                    if defects else None),
            "verdict": verdict, "rank_in_shot": rank}


def run(argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main(argv)
    return rc, buf.getvalue()


class BriefTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dailies-brief-test-")
        self.addCleanup(shutil.rmtree, self.dir)
        shot_a = os.path.join(self.dir, "shot-a")
        # t1: mechanical kill, root of the lineage chain.
        sidecar(shot_a, "t1.mp4", shot="shot-a",
                review=review("kill", ["black for 1.6s of 2.0s"]))
        # t2: rule kill, rerun of t1, first recipe.
        sidecar(shot_a, "t2.mp4", shot="shot-a", parent="sha256:t1.mp4",
                review=review(
                    "kill",
                    ["anatomy.hands severity 4 at 1.2s: six fingers"],
                    [{"rule": "anatomy.hands", "t": 1.2, "severity": 4,
                      "confidence": 0.6, "note": "six fingers"}]),
                recipe={"seeds": {"3": 2},
                        "models": [{"file": "wan.safetensors"}],
                        "loras": [{"file": "l.safetensors",
                                   "strength": 0.7}]})
        # t3: survivor, rerun of t2, second recipe.
        sidecar(shot_a, "t3.mp4", shot="shot-a", parent="sha256:t2.mp4",
                review=review(
                    "review", [],
                    [{"rule": "anatomy.hands", "t": 0.5, "severity": 3,
                      "confidence": 1.0, "note": "slight blur"},
                     {"rule": "physics.contact", "t": 0.8, "t_end": 1.4,
                      "severity": 2, "note": "cup floats"}],
                    rank=1),
                recipe={"seeds": {"3": 1},
                        "models": [{"file": "wan.safetensors"}],
                        "loras": [{"file": "l.safetensors",
                                   "strength": 0.85}]})
        # shot-b: one unreviewed take, no recipe: the graceful-absence path.
        sidecar(os.path.join(self.dir, "shot-b"), "raw.mp4", shot="shot-b")

    def data(self):
        rc, out = run(["brief", self.dir, "--json"])
        self.assertEqual(rc, 0)
        return json.loads(out)

    def shot(self, name):
        return next(s for s in self.data()["shots"] if s["shot"] == name)

    def test_totals_and_per_shot_counts(self):
        d = self.data()
        self.assertEqual(d["n_takes"], 4)
        self.assertEqual(d["kills"], 2)
        self.assertEqual(d["yield"], 0.5)
        self.assertEqual([s["shot"] for s in d["shots"]],
                         ["shot-a", "shot-b"])
        a = d["shots"][0]
        self.assertEqual((a["n_takes"], a["kills"], a["yield"]),
                         (3, 2, 0.333))
        b = d["shots"][1]
        self.assertEqual((b["n_takes"], b["kills"], b["yield"]),
                         (1, 0, 1.0))

    def test_kill_class_pins_the_reason_grammar(self):
        self.assertEqual(
            brief.kill_class("anatomy.hands severity 3 at 1.2s: six"),
            ("rule", "anatomy.hands"))
        self.assertEqual(
            brief.kill_class("calibrated kill score 4.10 > 3.20 "
                             "(false-kill rate <= 0.05)"),
            ("rule", "calibrated"))
        self.assertEqual(brief.kill_class("black for 4.0s of 4.0s"),
                         ("mechanical", "black"))
        self.assertEqual(brief.kill_class("probe: moov atom missing"),
                         ("mechanical", "probe"))

    def test_kill_histogram_splits_mechanical_vs_rule(self):
        a = self.shot("shot-a")
        self.assertEqual(a["kill_reasons"],
                         {"mechanical": {"black": 1},
                          "rule": {"anatomy.hands": 1}})

    def test_calibrated_kill_counts_on_the_rule_side(self):
        sidecar(os.path.join(self.dir, "shot-b"), "cal.mp4", shot="shot-b",
                review=review("kill", ["calibrated kill score 4.10 > "
                                       "3.20 (false-kill rate <= 0.05)"]))
        b = self.shot("shot-b")
        self.assertEqual(b["kill_reasons"]["rule"], {"calibrated": 1})
        self.assertEqual(b["kill_reasons"]["mechanical"], {})

    def test_rule_stats_with_example_defect(self):
        rules = self.shot("shot-a")["rules"]
        hands = rules["anatomy.hands"]
        self.assertEqual(hands["count"], 2)
        self.assertEqual(hands["takes_affected"], 2)
        self.assertEqual(hands["mean_severity"], 3.5)
        self.assertEqual(hands["mean_confidence"], 0.8)
        # The example is the worst defect: t2's severity 4.
        self.assertEqual(hands["example"],
                         {"file": "t2.mp4", "t": 1.2, "severity": 4,
                          "note": "six fingers", "confidence": 0.6})
        # A rule whose defects carry no confidence reports null.
        contact = rules["physics.contact"]
        self.assertIsNone(contact["mean_confidence"])
        self.assertEqual(contact["example"]["t_end"], 1.4)

    def test_survivors_ranked_and_unreviewed_included(self):
        a = self.shot("shot-a")
        self.assertEqual([sv["file"] for sv in a["survivors"]],
                         ["t3.mp4"])
        self.assertEqual(a["survivors"][0]["rank_in_shot"], 1)
        self.assertEqual(a["survivors"][0]["defects"], 2)
        b = self.shot("shot-b")
        self.assertEqual(b["survivors"][0]["verdict"], None)

    def test_lineage_depth_follows_parent_chains(self):
        a = self.shot("shot-a")
        self.assertEqual(a["lineage"], {"reruns": 2, "max_depth": 2})
        b = self.shot("shot-b")
        self.assertEqual(b["lineage"], {"reruns": 0, "max_depth": 0})

    def test_lineage_cycle_terminates(self):
        d = os.path.join(self.dir, "shot-c")
        sidecar(d, "x.mp4", shot="shot-c", parent="sha256:y.mp4")
        sidecar(d, "y.mp4", shot="shot-c", parent="sha256:x.mp4")
        c = self.shot("shot-c")
        self.assertEqual(c["lineage"]["reruns"], 2)
        self.assertEqual(c["lineage"]["max_depth"], 2)

    def test_recipe_deltas_and_graceful_absence(self):
        a = self.shot("shot-a")
        self.assertEqual(a["recipe"], {
            "n_with_recipe": 2,
            "seeds": {"3": [1, 2]},
            "models": ["wan.safetensors"],
            "lora_strengths": {"l.safetensors": [0.7, 0.85]}})
        self.assertIsNone(self.shot("shot-b")["recipe"])

    def test_text_output(self):
        rc, out = run(["brief", self.dir])
        self.assertEqual(rc, 0)
        self.assertIn("4 takes across 2 shots: 2 killed, yield 50%", out)
        self.assertIn("shot-a: 3 takes, 2 killed, yield 33%", out)
        self.assertIn("kills (mechanical): black 1", out)
        self.assertIn("kills (rule): anatomy.hands 1", out)
        self.assertIn("anatomy.hands: 2 defects on 2 takes, "
                      "mean severity 3.5, mean confidence 0.80", out)
        self.assertIn("e.g. t2.mp4 at 1.2s: six fingers", out)
        self.assertIn("e.g. t3.mp4 at 0.8-1.4s: cup floats", out)
        self.assertIn("#1 t3.mp4 review, 2 defects", out)
        self.assertIn("lineage: 2 reruns, max depth 2", out)
        self.assertIn("recipes 2/3: 2 distinct seeds, 1 models, 1 loras",
                      out)
        self.assertIn("lora l.safetensors strengths 0.7, 0.85", out)
        self.assertIn("recipes: none recorded", out)
        self.assertIn("raw.mp4 unreviewed, 0 defects", out)
        self.assertNotIn("\u2014", out)

    def test_empty_dir_exits_1(self):
        empty = tempfile.mkdtemp(prefix="dailies-brief-empty-")
        self.addCleanup(shutil.rmtree, empty)
        rc, _ = run(["brief", empty])
        self.assertEqual(rc, 1)

    def test_build_is_pure_read(self):
        def snapshot():
            state = {}
            for dirpath, _, files in os.walk(self.dir):
                for f in files:
                    p = os.path.join(dirpath, f)
                    with open(p) as fh:
                        state[p] = fh.read()
            return state

        before = snapshot()
        brief.build(self.dir)
        self.assertEqual(snapshot(), before)


if __name__ == "__main__":
    unittest.main()
