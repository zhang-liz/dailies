"""Regen-to-green defense over fabricated sidecars and a stub judge:
ancestor kill rules, the intent guard, deterministic audit picks, and
the judge-health gate."""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dailies import defense  # noqa: E402


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
