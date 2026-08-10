import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dailies import breaker  # noqa: E402


def review(verdict, vlm=None, rank=None):
    r = {"verdict": verdict,
         "mechanical": {"kill_reasons": [] if verdict != "kill"
                        else ["black for 100% of clip"]}}
    if vlm is not None:
        r["vlm"] = vlm
    if rank is not None:
        r["rank_in_shot"] = rank
    return r


class BetaincTests(unittest.TestCase):
    # Closed forms: I_x(1,1) = x, I_x(a,1) = x^a, I_x(1,b) = 1-(1-x)^b,
    # I_x(2,2) = 3x^2 - 2x^3, and the symmetry I_x(a,b) = 1 - I_(1-x)(b,a).

    def test_uniform(self):
        for x in (0.1, 0.25, 0.5, 0.9):
            self.assertAlmostEqual(breaker.betainc(1, 1, x), x, places=10)

    def test_power_forms(self):
        self.assertAlmostEqual(breaker.betainc(9, 1, 0.75),
                               0.75 ** 9, places=10)
        self.assertAlmostEqual(breaker.betainc(1, 4, 0.3),
                               1 - 0.7 ** 4, places=10)
        x = 0.4
        self.assertAlmostEqual(breaker.betainc(2, 2, x),
                               3 * x ** 2 - 2 * x ** 3, places=10)

    def test_symmetry(self):
        for a, b, x in ((3, 7, 0.2), (12.5, 2.0, 0.85), (5, 5, 0.5)):
            self.assertAlmostEqual(
                breaker.betainc(a, b, x),
                1.0 - breaker.betainc(b, a, 1.0 - x), places=10)

    def test_bounds(self):
        self.assertEqual(breaker.betainc(3, 4, 0.0), 0.0)
        self.assertEqual(breaker.betainc(3, 4, 1.0), 1.0)


class DoomProbabilityTests(unittest.TestCase):
    def test_matches_closed_form(self):
        # 8 kills, 0 passes, uniform prior: posterior Beta(9, 1), whose
        # CDF is x^9, so P(p > 0.75) = 1 - 0.75^9.
        self.assertAlmostEqual(breaker.doom_probability(8, 0),
                               1 - 0.75 ** 9, places=10)

    def test_monotone_in_kills(self):
        probs = [breaker.doom_probability(k, 2) for k in range(0, 12)]
        self.assertEqual(probs, sorted(probs))

    def test_pass_lowers_it(self):
        self.assertLess(breaker.doom_probability(8, 1),
                        breaker.doom_probability(8, 0))

    def test_no_takes_is_prior_mass(self):
        # Uniform prior: P(p > 0.75) = 0.25. Far from the trip line.
        self.assertAlmostEqual(breaker.doom_probability(0, 0), 0.25,
                               places=10)


class AssessTests(unittest.TestCase):
    def test_eight_straight_kills_trip(self):
        st = breaker.assess([review("kill")] * 8)
        self.assertTrue(st["doomed"])
        self.assertEqual(st["takes"], 8)
        self.assertEqual(st["mechanical_kills"], 8)

    def test_seven_straight_do_not(self):
        self.assertFalse(breaker.assess([review("kill")] * 7)["doomed"])

    def test_one_pass_buys_more_takes(self):
        st = breaker.assess([review("kill")] * 8 + [review("review")])
        self.assertFalse(st["doomed"])

    def test_vlm_kills_do_not_count(self):
        # A kill carrying a vlm block died at the judge, not mechanics;
        # the monitor must stay blind to it.
        rs = [review("kill", vlm={"engine": "stub", "defects": []})] * 8
        st = breaker.assess(rs)
        self.assertEqual(st["mechanical_kills"], 0)
        self.assertFalse(st["doomed"])

    def test_custom_floor_and_confidence(self):
        rs = [review("kill")] * 3
        self.assertFalse(breaker.assess(rs)["doomed"])
        self.assertTrue(breaker.assess(rs, confidence=0.6)["doomed"])


class WorstTests(unittest.TestCase):
    def test_picks_deepest_ranked_mechanical_kill(self):
        pairs = [("a.mp4", review("review", rank=1)),
                 ("b.mp4", review("kill", rank=3)),
                 ("c.mp4", review("kill", rank=2)),
                 ("d.mp4", review("kill", rank=4,
                                  vlm={"engine": "stub"}))]
        self.assertEqual(breaker.worst(pairs), "b.mp4")

    def test_none_without_mechanical_kills(self):
        self.assertIsNone(breaker.worst([("a.mp4", review("review"))]))


class StatesTests(unittest.TestCase):
    def test_groups_by_shot(self):
        takes = {}
        for i in range(8):
            takes["s1/k%d.mp4" % i] = {
                "shot": "shot-01", "review": review("kill", rank=i + 1)}
        takes["s2/ok.mp4"] = {"shot": "shot-02",
                              "review": review("review", rank=1)}
        takes["untagged.mp4"] = {"shot": None, "review": review("review")}
        takes["bare.mp4"] = {"shot": "shot-03", "review": None}
        st = breaker.states(takes)
        self.assertEqual(sorted(st), ["shot-01", "shot-02"])
        self.assertTrue(st["shot-01"]["doomed"])
        self.assertEqual(st["shot-01"]["worst"], "s1/k7.mp4")
        self.assertFalse(st["shot-02"]["doomed"])
        self.assertIsNone(st["shot-02"]["worst"])


if __name__ == "__main__":
    unittest.main()
