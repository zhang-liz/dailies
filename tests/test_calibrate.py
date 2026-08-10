"""Conformal calibration tests over fabricated sidecars."""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dailies import calibrate  # noqa: E402
from dailies.cli import main  # noqa: E402


def sidecar(dirpath, name, label, severities, rule="r.x"):
    """Fabricate a gold-labeled, reviewed sidecar; the clip itself is
    never touched by calibration."""
    defects = [{"t": 1.0, "severity": s, "note": "x", "rule": rule}
               for s in severities]
    t = {"take_id": "sha256:%s" % name, "shot": "s", "output":
         {"file": name}, "gold": {"label": label},
         "review": {"mechanical": {"kill_reasons": []},
                    "vlm": {"engine": "stub", "defects": defects},
                    "verdict": "review"}}
    with open(os.path.join(dirpath, name + ".take.json"), "w") as f:
        json.dump(t, f)


class CalibrateTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dailies-cal-test-")
        self.addCleanup(shutil.rmtree, self.dir)

    def test_kill_score_is_max_severity_times_confidence(self):
        t = {"review": {"vlm": {"defects": [
            {"severity": 3}, {"severity": 5, "confidence": 0.67}]}}}
        self.assertAlmostEqual(calibrate.kill_score(t), 3.35)
        self.assertEqual(calibrate.kill_score({"review": None}), 0.0)

    def test_threshold_is_the_conformal_quantile(self):
        # Pass scores 0,0,1,2,4. At alpha=0.5, k=ceil(6*0.5)=3, so
        # lambda is the 3rd smallest: 1. Kills score above it.
        for i, sevs in enumerate([[], [], [1], [2], [4]]):
            sidecar(self.dir, "p%d.mp4" % i, "pass", sevs)
        sidecar(self.dir, "k0.mp4", "kill", [5])
        sidecar(self.dir, "k1.mp4", "kill", [1])
        cal = calibrate.calibrate(self.dir, alpha=0.5)
        self.assertEqual(cal["lambda"], 1)
        self.assertEqual(cal["n_pass"], 5)
        self.assertEqual(cal["kill_recall"], 0.5)

    def test_small_gold_set_refuses_to_guarantee(self):
        for i in range(3):
            sidecar(self.dir, "p%d.mp4" % i, "pass", [])
        cal = calibrate.calibrate(self.dir, alpha=0.05)
        self.assertIsNone(cal["lambda"])
        self.assertEqual(cal["needed"], 19)

    def test_no_gold_is_an_error(self):
        with self.assertRaises(RuntimeError):
            calibrate.calibrate(self.dir)

    def test_fit_learns_which_rule_predicts_kills(self):
        # Rule a.kills fires on every killed take; rule b.noise fires
        # everywhere. The fitted weight must separate them.
        for i in range(4):
            sidecar(self.dir, "k%d.mp4" % i, "kill", [4], rule="a.kills")
            sidecar(self.dir, "kn%d.mp4" % i, "kill", [4],
                    rule="a.kills")
        for i in range(4):
            sidecar(self.dir, "p%d.mp4" % i, "pass", [4], rule="b.noise")
        fitted = calibrate.fit(self.dir)
        self.assertGreater(fitted["weights"]["a.kills"],
                           fitted["weights"]["b.noise"])
        self.assertGreaterEqual(fitted["fit_accuracy"], 0.9)
        # rank_score orders a killing take above a noisy pass.
        killer = {"review": {"vlm": {"defects": [
            {"severity": 4, "rule": "a.kills"}]}}}
        noisy = {"review": {"vlm": {"defects": [
            {"severity": 4, "rule": "b.noise"}]}}}
        self.assertGreater(calibrate.rank_score(killer, fitted),
                           calibrate.rank_score(noisy, fitted))
        self.assertIsNone(calibrate.rank_score(killer, {}))

    def test_fit_needs_enough_labels(self):
        sidecar(self.dir, "one.mp4", "kill", [4])
        with self.assertRaises(RuntimeError):
            calibrate.fit(self.dir)

    def test_cli_writes_calibration_file(self):
        for i, sevs in enumerate([[], [], [1], [2], [4]]):
            sidecar(self.dir, "p%d.mp4" % i, "pass", sevs)
        out = os.path.join(self.dir, "cal.json")
        rc = main(["calibrate", self.dir, "--alpha", "0.5", "-o", out])
        self.assertEqual(rc, 0)
        self.assertEqual(calibrate.load(out)["lambda"], 1)


if __name__ == "__main__":
    unittest.main()
