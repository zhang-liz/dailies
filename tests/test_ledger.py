"""Night ledger tests over fabricated states: no clips, no drivers, no
clock games. Every stopping rule is pinned on the exact boundary where
it flips, because the ledger is the only thing standing between an
unattended loop and a wasted night."""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dailies import ledger, regen  # noqa: E402

RULE_KILL = "anatomy.hands severity 3 at 1.2s: six fingers"
MECH_KILL = "black for 4.0s of 4.0s"


def fab(shot="shot-07", verdict="review", reasons=(), vlm=None,
        seeds=None, parent=None, take_id=None, cost=None):
    """One fabricated sidecar dict in rerank's clip-keyed value shape."""
    t = {"take_id": take_id, "shot": shot, "parent": parent,
         "recipe": {"seeds": seeds} if seeds is not None else None,
         "review": {"mechanical": {"kill_reasons": list(reasons)},
                    "vlm": vlm, "verdict": verdict}}
    if cost is not None:
        t["review"]["cost"] = {"total_usd": cost}
    return t


def rule_kill(seeds, rule=RULE_KILL, **kw):
    # The vlm block marks the kill as the judge's, not mechanics', so
    # the doomed breaker stays blind to it.
    return fab(verdict="kill", reasons=(rule,),
               vlm={"engine": "stub", "defects": []}, seeds=seeds, **kw)


def mech_kill(seeds=None, **kw):
    return fab(verdict="kill", reasons=(MECH_KILL,), seeds=seeds, **kw)


class FileTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dailies-ledger-")
        self.addCleanup(shutil.rmtree, self.dir)
        self.path = os.path.join(self.dir, "dailies-night.json")

    def test_load_missing_is_fresh(self):
        led = ledger.load(self.path)
        self.assertEqual(led["caps"],
                         {"lineage": 4, "attempts": None,
                          "spend_usd": None})
        self.assertEqual((led["want_default"], led["jobs"],
                          led["shots"]), (1, {}, {}))

    def test_save_is_atomic_and_roundtrips(self):
        led = ledger.fresh(want={"shot-07": 3}, attempt_cap=40,
                           spend_cap=5.0)
        ledger.save(self.path, led)
        self.assertFalse(os.path.exists(self.path + ".tmp"))
        back = ledger.load(self.path)
        self.assertEqual(back["want"], {"shot-07": 3})
        self.assertEqual(back["caps"]["attempts"], 40)
        self.assertEqual(back["caps"]["spend_usd"], 5.0)
        self.assertTrue(back["updated"])

    def test_save_leaves_valid_json(self):
        ledger.save(self.path, ledger.fresh())
        with open(self.path) as f:
            json.load(f)


class FutilityTests(unittest.TestCase):
    def test_three_distinct_seed_kills_by_one_rule(self):
        group = [rule_kill({"3": s}) for s in (1, 2, 3)]
        self.assertEqual(ledger.futility(group), "anatomy.hands")

    def test_two_distinct_seeds_are_not_enough(self):
        group = [rule_kill({"3": s}) for s in (1, 2)]
        self.assertIsNone(ledger.futility(group))

    def test_repeated_seed_counts_once(self):
        group = [rule_kill({"3": s}) for s in (1, 2, 2)]
        self.assertIsNone(ledger.futility(group))

    def test_seedless_kills_prove_nothing(self):
        group = [rule_kill(None) for _ in range(5)]
        self.assertIsNone(ledger.futility(group))

    def test_kills_split_across_rules_do_not_block(self):
        group = [rule_kill({"3": 1}),
                 rule_kill({"3": 2}, rule="physics.gravity severity 4 "
                                          "at 0.5s: floats"),
                 rule_kill({"3": 3}, rule="motion.morph severity 3 "
                                          "at 2.0s: melts")]
        self.assertIsNone(ledger.futility(group))

    def test_mechanical_class_blocks_too(self):
        group = [mech_kill({"3": s}) for s in (1, 2, 3)]
        self.assertEqual(ledger.futility(group), "black")

    def test_two_futile_rules_answer_alphabetically(self):
        group = ([rule_kill({"3": s}) for s in (1, 2, 3)]
                 + [rule_kill({"3": s}, rule="adherence.prompt severity "
                                             "4 at 0.0s: wrong subject")
                    for s in (4, 5, 6)])
        self.assertEqual(ledger.futility(group), "adherence.prompt")


class ChainTests(unittest.TestCase):
    def chain(self, n):
        takes = {}
        for i in range(n):
            takes["t%d.mp4" % i] = fab(
                take_id="id-%d" % i,
                parent="id-%d" % (i - 1) if i else None)
        return takes

    def test_root_is_one(self):
        takes = self.chain(1)
        self.assertEqual(ledger.chain_length(
            takes["t0.mp4"], ledger.by_take_id(takes)), 1)

    def test_chain_of_three(self):
        takes = self.chain(3)
        self.assertEqual(ledger.chain_length(
            takes["t2.mp4"], ledger.by_take_id(takes)), 3)

    def test_unknown_ancestor_counts_one_hop(self):
        t = fab(take_id="id-9", parent="id-gone")
        self.assertEqual(ledger.chain_length(t, {}), 2)

    def test_cycle_terminates(self):
        a = fab(take_id="a", parent="b")
        b = fab(take_id="b", parent="a")
        by_id = {"a": a, "b": b}
        self.assertEqual(ledger.chain_length(a, by_id), 3)


class RefreshTests(unittest.TestCase):
    def test_one_survivor_completes_by_default(self):
        led = ledger.fresh()
        takes = {"a.mp4": fab(verdict="review"),
                 "b.mp4": mech_kill()}
        shots = ledger.refresh(led, takes)
        st = shots["shot-07"]
        self.assertEqual((st["status"], st["passing"], st["want"],
                          st["takes"]), ("complete", 1, 1, 2))

    def test_want_override_keeps_the_shot_active(self):
        led = ledger.fresh(want={"shot-07": 3})
        takes = {"a.mp4": fab(), "b.mp4": fab(), "c.mp4": mech_kill()}
        self.assertEqual(ledger.refresh(led, takes)["shot-07"]["status"],
                         "active")

    def test_futile_rule_blocks_and_is_named(self):
        led = ledger.fresh()
        takes = {"k%d.mp4" % s: rule_kill({"3": s}) for s in (1, 2, 3)}
        st = ledger.refresh(led, takes)["shot-07"]
        self.assertEqual(st["status"], "blocked")
        self.assertEqual(st["blocked_by"], "anatomy.hands")

    def test_eight_seedless_mechanical_kills_doom(self):
        led = ledger.fresh()
        takes = {"k%d.mp4" % i: mech_kill() for i in range(8)}
        st = ledger.refresh(led, takes)["shot-07"]
        self.assertEqual(st["status"], "doomed")
        self.assertEqual(st["mechanical_kills"], 8)
        self.assertIsNone(st["blocked_by"])

    def test_complete_beats_blocked(self):
        led = ledger.fresh()
        takes = {"k%d.mp4" % s: rule_kill({"3": s}) for s in (1, 2, 3)}
        takes["ok.mp4"] = fab(verdict="keep")
        self.assertEqual(ledger.refresh(led, takes)["shot-07"]["status"],
                         "complete")

    def test_spend_sums_costs_and_totals(self):
        led = ledger.fresh()
        takes = {"a.mp4": fab(cost=0.25), "b.mp4": mech_kill(),
                 "c.mp4": fab(shot="shot-08", cost=0.5)}
        shots = ledger.refresh(led, takes)
        self.assertEqual(shots["shot-07"]["spend_usd"], 0.25)
        self.assertEqual(shots["shot-08"]["spend_usd"], 0.5)
        self.assertEqual(led["spend_usd"], 0.75)

    def test_shot_known_only_from_the_job_table(self):
        led = ledger.fresh()
        ledger.record_submit(led, "drv", "shot-09",
                             {"job": "j1", "clip": "/x/n.mp4",
                              "parent": "id-1",
                              "recipe": {"seeds": {"3": 5}}})
        st = ledger.refresh(led, {})["shot-09"]
        self.assertEqual((st["status"], st["takes"], st["attempts"]),
                         ("active", 0, 1))

    def test_unreviewed_stub_and_untagged_take_are_ignored(self):
        led = ledger.fresh()
        takes = {"stub.mp4": {"take_id": None, "shot": "shot-07",
                              "parent": "id-1", "recipe": None,
                              "review": None},
                 "loose.mp4": fab(shot=None)}
        self.assertEqual(ledger.refresh(led, takes)["shot-07"]["takes"],
                         0)


class ShouldSubmitTests(unittest.TestCase):
    def kills(self, n, shot="shot-07"):
        return {"%s-k%d.mp4" % (shot, s):
                mech_kill({"3": 100 + s}, shot=shot,
                          take_id="id-%s-%d" % (shot, s))
                for s in range(n)}

    def test_active_shot_below_caps_is_allowed(self):
        ok, why = ledger.should_submit(ledger.fresh(), self.kills(2),
                                       "shot-07-k0.mp4")
        self.assertEqual((ok, why), (True, None))

    def test_complete_shot_refuses(self):
        takes = self.kills(1)
        takes["ok.mp4"] = fab(verdict="review")
        ok, why = ledger.should_submit(ledger.fresh(), takes,
                                       "shot-07-k0.mp4")
        self.assertFalse(ok)
        self.assertIn("shot-07 complete", why)

    def test_blocked_shot_names_the_rule(self):
        takes = {"k%d.mp4" % s: rule_kill({"3": s}, take_id="id-%d" % s)
                 for s in (1, 2, 3)}
        ok, why = ledger.should_submit(ledger.fresh(), takes, "k1.mp4")
        self.assertFalse(ok)
        self.assertIn("blocked", why)
        self.assertIn("anatomy.hands", why)

    def test_doomed_shot_refuses(self):
        takes = {"k%d.mp4" % i: mech_kill(take_id="id-%d" % i)
                 for i in range(8)}
        ok, why = ledger.should_submit(ledger.fresh(), takes, "k0.mp4")
        self.assertFalse(ok)
        self.assertIn("doomed", why)

    def test_lineage_cap_refuses_the_fifth_generation(self):
        # Seedless kills so the futility rule cannot fire first; the
        # same chain with recorded seeds blocks at three distinct.
        takes = {}
        for i in range(4):
            takes["t%d.mp4" % i] = mech_kill(
                take_id="id-%d" % i,
                parent="id-%d" % (i - 1) if i else None)
        # Depth 3 parent extends; depth 4 parent has spent the chain.
        ok, _ = ledger.should_submit(ledger.fresh(), takes, "t2.mp4")
        self.assertTrue(ok)
        ok, why = ledger.should_submit(ledger.fresh(), takes, "t3.mp4")
        self.assertFalse(ok)
        self.assertIn("lineage cap 4", why)

    def test_lineage_cap_is_configurable(self):
        takes = {"t0.mp4": mech_kill({"3": 0}, take_id="id-0"),
                 "t1.mp4": mech_kill({"3": 1}, take_id="id-1",
                                     parent="id-0")}
        led = ledger.fresh(lineage_cap=2)
        ok, why = ledger.should_submit(led, takes, "t1.mp4")
        self.assertFalse(ok)
        self.assertIn("lineage cap 2", why)

    def test_global_attempt_cap(self):
        led = ledger.fresh(attempt_cap=2)
        for i in range(2):
            ledger.record_submit(led, "drv", "shot-07",
                                 {"job": "j%d" % i, "clip": "/x/%d" % i,
                                  "parent": "p", "recipe": None})
        ok, why = ledger.should_submit(led, self.kills(2),
                                       "shot-07-k0.mp4")
        self.assertFalse(ok)
        self.assertIn("attempt cap 2", why)

    def test_global_spend_cap(self):
        led = ledger.fresh(spend_cap=1.0)
        takes = self.kills(2)
        takes["a.mp4"] = mech_kill({"3": 9}, shot="shot-08", cost=0.6)
        takes["b.mp4"] = mech_kill({"3": 8}, shot="shot-08", cost=0.5)
        ok, why = ledger.should_submit(led, takes, "shot-07-k0.mp4")
        self.assertFalse(ok)
        self.assertIn("$1.10 at the $1.00 cap", why)

    def test_missing_cost_blocks_nothing(self):
        led = ledger.fresh(spend_cap=1.0)
        ok, _ = ledger.should_submit(led, self.kills(2),
                                     "shot-07-k0.mp4")
        self.assertTrue(ok)

    def test_unknown_parent_refuses(self):
        ok, why = ledger.should_submit(ledger.fresh(), self.kills(1),
                                       "ghost.mp4")
        self.assertFalse(ok)
        self.assertIn("ghost.mp4", why)


class RecordTests(unittest.TestCase):
    def test_submit_row_holds_the_mutation_not_the_recipe(self):
        led = ledger.fresh()
        row = ledger.record_submit(
            led, "drv --flag", "shot-07",
            {"job": "j1", "clip": "/takes/n.mp4", "parent": "id-1",
             "recipe": {"workflow": {"3": {}}, "seeds": {"3": 99}}})
        self.assertEqual(row["seeds"], {"3": 99})
        self.assertNotIn("workflow", row)
        self.assertNotIn("recipe", row)
        self.assertEqual((row["state"], row["driver"], row["shot"]),
                         ("queued", "drv --flag", "shot-07"))
        self.assertTrue(row["submitted"])
        self.assertEqual(led["attempts"], 1)

    def test_duplicate_job_id_gets_a_suffixed_key(self):
        led = ledger.fresh()
        for clip in ("/x/a.mp4", "/x/b.mp4"):
            ledger.record_submit(led, "drv", "s",
                                 {"job": "j1", "clip": clip,
                                  "parent": "p", "recipe": None})
        self.assertEqual(sorted(led["jobs"]), ["j1", "j1-dup2"])
        self.assertEqual(led["jobs"]["j1"]["clip"], "/x/a.mp4")
        self.assertEqual(led["jobs"]["j1-dup2"]["clip"], "/x/b.mp4")
        # Both rows keep the driver's own id for polling.
        self.assertEqual(led["jobs"]["j1-dup2"]["job"], "j1")
        self.assertEqual(led["attempts"], 2)

    def test_result_stamps_terminal_states(self):
        led = ledger.fresh()
        ledger.record_submit(led, "drv", "s",
                             {"job": "j1", "clip": "/x", "parent": "p",
                              "recipe": None})
        row = ledger.record_result(led, "j1", "running")
        self.assertNotIn("resolved", row)
        row = ledger.record_result(led, "j1", "error", "gpu on fire")
        self.assertEqual(row["error"], "gpu on fire")
        self.assertTrue(row["resolved"])


class AdoptStubTests(unittest.TestCase):
    # The crash window between driver submit and record_submit leaves
    # the job id only in the stub sidecar; adoption files it as a
    # pending row so reconcile() can settle it.

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dailies-adopt-")
        self.addCleanup(shutil.rmtree, self.dir)
        self.led = ledger.fresh()

    def stub(self, name, regen_block, **fields):
        t = {"take_id": None, "shot": "shot-07", "parent": "sha256:p",
             "created": None, "output": {"file": name},
             "recipe": {"seeds": {"3": 7}}, "review": None,
             "regen": regen_block}
        t.update(fields)
        with open(os.path.join(self.dir, name + ".take.json"),
                  "w") as f:
            json.dump(t, f)
        return t

    def test_unrecorded_stub_becomes_a_pending_row(self):
        self.stub("a-regen-01.mp4", {"driver": "drv", "job": "j-lost",
                                     "submitted": "2026-08-09T00:00:00Z"})
        self.assertEqual(ledger.adopt_stubs(self.led, self.dir),
                         ["j-lost"])
        row = self.led["jobs"]["j-lost"]
        self.assertEqual(row["state"], "queued")
        self.assertEqual(row["driver"], "drv")
        self.assertEqual(row["clip"],
                         os.path.join(self.dir, "a-regen-01.mp4"))
        self.assertEqual((row["parent"], row["shot"], row["seeds"]),
                         ("sha256:p", "shot-07", {"3": 7}))
        self.assertEqual(self.led["attempts"], 1)

    def test_recorded_and_regenless_stubs_left_alone(self):
        self.stub("a-regen-01.mp4", {"driver": "drv", "job": "j1"})
        self.stub("b-regen-02.mp4", None)
        ledger.record_submit(self.led, "drv", "shot-07",
                             {"job": "j1", "clip": "/x/a.mp4",
                              "parent": "p", "recipe": None})
        self.assertEqual(ledger.adopt_stubs(self.led, self.dir), [])
        self.assertEqual(sorted(self.led["jobs"]), ["j1"])

    def test_adopted_row_settles_through_reconcile(self):
        self.stub("a-regen-01.mp4", {"driver": "drv", "job": "j-lost"})
        open(os.path.join(self.dir, "a-regen-01.mp4"), "wb").close()
        ledger.adopt_stubs(self.led, self.dir)
        observed = ledger.reconcile(
            self.led, poll=lambda d, j: {"state": "running"})
        self.assertEqual(observed, {"j-lost": "done"})


class ReconcileTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dailies-reconcile-")
        self.addCleanup(shutil.rmtree, self.dir)
        self.led = ledger.fresh()

    def add_job(self, jid, clip):
        ledger.record_submit(self.led, "drv", "shot-07",
                             {"job": jid, "clip": clip, "parent": "p",
                              "recipe": {"seeds": {"3": 1}}})

    def test_landed_clip_wins_without_polling(self):
        clip = os.path.join(self.dir, "landed.mp4")
        open(clip, "wb").close()
        self.add_job("j1", clip)

        def poll(driver, jid):
            raise AssertionError("polled a job the filesystem settled")
        observed = ledger.reconcile(self.led, poll=poll)
        self.assertEqual(observed, {"j1": "done"})
        self.assertEqual(self.led["jobs"]["j1"]["state"], "done")
        self.assertTrue(self.led["jobs"]["j1"]["resolved"])

    def test_driver_answers_for_missing_clips(self):
        self.add_job("j1", os.path.join(self.dir, "not-yet.mp4"))
        observed = ledger.reconcile(
            self.led, poll=lambda d, j: {"state": "running"})
        self.assertEqual(observed, {"j1": "running"})
        job = self.led["jobs"]["j1"]
        self.assertEqual(job["state"], "running")
        self.assertNotIn("resolved", job)

    def test_done_elsewhere_records_the_landed_path(self):
        self.add_job("j1", os.path.join(self.dir, "wanted.mp4"))
        observed = ledger.reconcile(
            self.led, poll=lambda d, j: {"state": "done",
                                         "output": "/elsewhere/x.mp4"})
        self.assertEqual(observed, {"j1": "done"})
        self.assertEqual(self.led["jobs"]["j1"]["landed"],
                         "/elsewhere/x.mp4")

    def test_driver_error_state_is_recorded(self):
        self.add_job("j1", os.path.join(self.dir, "never.mp4"))
        ledger.reconcile(
            self.led, poll=lambda d, j: {"state": "error",
                                         "error": "oom"})
        job = self.led["jobs"]["j1"]
        self.assertEqual((job["state"], job["error"]), ("error", "oom"))

    def test_a_forgetful_driver_does_not_stop_the_sweep(self):
        self.add_job("j1", os.path.join(self.dir, "a.mp4"))
        self.add_job("j2", os.path.join(self.dir, "b.mp4"))

        def poll(driver, jid):
            if jid == "j1":
                raise regen.DriverError("no such job")
            return {"state": "queued"}
        observed = ledger.reconcile(self.led, poll=poll)
        self.assertEqual(observed, {"j1": "error", "j2": "queued"})
        self.assertEqual(self.led["jobs"]["j1"]["error"], "no such job")

    def test_suffixed_rows_poll_the_drivers_own_id(self):
        self.add_job("j1", os.path.join(self.dir, "a.mp4"))
        self.add_job("j1", os.path.join(self.dir, "b.mp4"))
        polled = []

        def poll(driver, jid):
            polled.append(jid)
            return {"state": "queued"}
        ledger.reconcile(self.led, poll=poll)
        self.assertEqual(polled, ["j1", "j1"])

    def test_terminal_jobs_are_left_alone(self):
        self.add_job("j1", os.path.join(self.dir, "done.mp4"))
        ledger.record_result(self.led, "j1", "done")

        def poll(driver, jid):
            raise AssertionError("polled a finished job")
        self.assertEqual(ledger.reconcile(self.led, poll=poll), {})


if __name__ == "__main__":
    unittest.main()
