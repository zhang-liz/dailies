"""watch --regen: the regenerator that closes the loop. Unit tests run
the policy over fabricated kills and the fake drivers; the end-to-end
test lets a black clip die inside a live watch loop, the driver land a
fresh clip in the watched directory, and the ordinary review path pick
it up with lineage intact."""

import json
import glob
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from dailies import ledger, regen, take, watch  # noqa: E402
from dailies.cli import main  # noqa: E402
from test_regen import write_bad_driver, write_driver  # noqa: E402

FFMPEG = shutil.which("ffmpeg")


def make_kill(dirpath, name, shot="shot-07", ident=1, seeds="default"):
    """A fabricated mechanically killed take on disk, no ffmpeg."""
    clip = os.path.join(dirpath, name)
    with open(clip, "wb") as f:
        f.write(b"dead pixels " + name.encode())
    t = take.load(clip)
    if seeds == "default":
        seeds = {"3": 424242 + ident}
    t.update({"take_id": "sha256:%064x" % ident, "shot": shot,
              "created": "2026-08-09T00:00:00Z",
              "recipe": {"seeds": seeds} if seeds else None,
              "review": {"mechanical": {"kill_reasons": ["black"]},
                         "vlm": None, "verdict": "kill",
                         "rank_in_shot": 1}})
    take.save(clip, t)
    return clip, t


def passing(shot="shot-07"):
    """A fabricated surviving take, dict only: the gate never needs its
    clip on disk."""
    return {"take_id": "sha256:%064x" % 999, "shot": shot,
            "parent": None, "recipe": None,
            "review": {"mechanical": {"kill_reasons": []},
                       "vlm": None, "verdict": "review",
                       "rank_in_shot": 1}}


def write_instant_driver(dirpath, spool, src):
    """A driver whose backend is instant: submit lands the clip before
    printing the id; poll answers done. Models fast hosted generation,
    where the clip appears without anyone polling."""
    script = os.path.join(dirpath, "instant.py")
    body = (
        "import json, os, shutil, sys\n"
        "SPOOL = %r\n"
        "SRC = %r\n"
        "if sys.argv[1] == 'submit':\n"
        "    job = json.load(sys.stdin)\n"
        "    jid = 'job-%%03d' %% len(os.listdir(SPOOL))\n"
        "    with open(os.path.join(SPOOL, jid), 'w') as f:\n"
        "        json.dump(job, f)\n"
        "    shutil.copy(SRC, job['clip'])\n"
        "    print(jid)\n"
        "else:\n"
        "    with open(os.path.join(SPOOL, sys.argv[2])) as f:\n"
        "        job = json.load(f)\n"
        "    print(json.dumps({'state': 'done',\n"
        "                      'output': job['clip']}))\n"
    ) % (spool, src)
    with open(script, "w") as f:
        f.write(body)
    return '"%s" "%s"' % (sys.executable, script)


class ParseWantTests(unittest.TestCase):
    def test_pairs_parse(self):
        self.assertEqual(watch.parse_want(["shot-07=3", "a=b=2"]),
                         {"shot-07": 3, "a=b": 2})

    def test_empty_and_none(self):
        self.assertEqual(watch.parse_want([]), {})
        self.assertEqual(watch.parse_want(None), {})

    def test_malformed_pairs_raise(self):
        for bad in ("x", "=3", "s=", "s=0", "s=-1", "s=abc", "s=1.5"):
            with self.assertRaises(ValueError, msg=bad):
                watch.parse_want([bad])


class RegenBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dailies-watch-regen-")
        self.addCleanup(shutil.rmtree, self.dir)
        self.spool = os.path.join(self.dir, "spool")
        os.makedirs(self.spool)
        self.src = os.path.join(self.dir, "src.mp4")
        with open(self.src, "wb") as f:
            f.write(b"fresh pixels")
        self.driver = write_driver(self.dir, self.spool, self.src)
        self.ledger_path = os.path.join(self.dir, "dailies-night.json")

    def regen_stubs(self):
        return [p for p in glob.glob(os.path.join(self.dir,
                                                  "*.take.json"))
                if "-regen-" in os.path.basename(p)]


class DryRunTests(RegenBase):
    def test_prints_mutation_and_touches_nothing(self):
        clip, t = make_kill(self.dir, "a.mp4")
        rgn = watch.Regenerator("/no/such/driver", self.ledger_path,
                                dry_run=True)
        ev = rgn.on_kill(t, clip, {clip: t})
        self.assertEqual(ev["action"], "dry-run")
        self.assertEqual(ev["shot"], "shot-07")
        self.assertEqual(set(ev["seeds"]), {"3"})
        self.assertNotEqual(ev["seeds"]["3"], t["recipe"]["seeds"]["3"])
        self.assertFalse(os.path.exists(self.ledger_path))
        self.assertEqual(self.regen_stubs(), [])

    def test_gate_still_consulted(self):
        clip, t = make_kill(self.dir, "a.mp4")
        takes = {clip: t, "ok.mp4": passing()}
        rgn = watch.Regenerator("/no/such/driver", self.ledger_path,
                                dry_run=True)
        ev = rgn.on_kill(t, clip, takes)
        self.assertEqual(ev["action"], "skipped")
        self.assertIn("complete", ev["reason"])
        self.assertFalse(os.path.exists(self.ledger_path))

    def test_dry_run_never_reconciles_a_stale_ledger(self):
        led = ledger.fresh()
        ledger.record_submit(led, "/no/such/driver", "shot-07",
                             {"job": "j1",
                              "clip": os.path.join(self.dir, "no.mp4"),
                              "parent": "p", "recipe": None})
        ledger.save(self.ledger_path, led)
        with open(self.ledger_path) as f:
            before = f.read()
        watch.Regenerator("/no/such/driver", self.ledger_path,
                          dry_run=True)
        with open(self.ledger_path) as f:
            self.assertEqual(f.read(), before)


class SubmitTests(RegenBase):
    def test_kill_submits_and_records(self):
        clip, t = make_kill(self.dir, "a.mp4")
        rgn = watch.Regenerator(self.driver, self.ledger_path)
        ev = rgn.on_kill(t, clip, {clip: t})
        self.assertEqual(ev["action"], "submitted")
        self.assertEqual(ev["job"], "job-000")
        self.assertIn("-regen-", os.path.basename(ev["clip"]))
        self.assertNotEqual(ev["seeds"]["3"], t["recipe"]["seeds"]["3"])
        stub = take.load(ev["clip"])
        self.assertEqual(stub["parent"], t["take_id"])
        self.assertEqual(stub["recipe"]["seeds"], ev["seeds"])
        led = ledger.load(self.ledger_path)
        job = led["jobs"]["job-000"]
        self.assertEqual((job["state"], job["shot"], job["clip"]),
                         ("queued", "shot-07", ev["clip"]))
        self.assertEqual(job["seeds"], ev["seeds"])
        self.assertEqual(led["attempts"], 1)

    def test_want_overrides_merge_into_the_ledger(self):
        rgn = watch.Regenerator(self.driver, self.ledger_path,
                                want={"shot-07": 3})
        self.assertEqual(rgn.led["want"], {"shot-07": 3})
        self.assertEqual(ledger.load(self.ledger_path)["want"],
                         {"shot-07": 3})

    def test_refusal_names_the_rule_and_saves_state(self):
        clip, t = make_kill(self.dir, "a.mp4")
        takes = {clip: t, "ok.mp4": passing()}
        rgn = watch.Regenerator(self.driver, self.ledger_path)
        ev = rgn.on_kill(t, clip, takes)
        self.assertEqual(ev["action"], "skipped")
        self.assertIn("shot-07 complete", ev["reason"])
        led = ledger.load(self.ledger_path)
        self.assertEqual(led["shots"]["shot-07"]["status"], "complete")
        self.assertEqual(led["jobs"], {})

    def test_landed_clips_marked_done_on_the_next_kill(self):
        clip, t = make_kill(self.dir, "a.mp4")
        rgn = watch.Regenerator(self.driver, self.ledger_path)
        ev = rgn.on_kill(t, clip, {clip: t})
        with open(ev["clip"], "wb") as f:
            f.write(b"landed")
        clip2, t2 = make_kill(self.dir, "b.mp4", shot="shot-08",
                              ident=2)
        rgn.on_kill(t2, clip2, {clip: t, clip2: t2})
        job = ledger.load(self.ledger_path)["jobs"][ev["job"]]
        self.assertEqual(job["state"], "done")
        self.assertTrue(job["resolved"])


class RateCapTests(RegenBase):
    def test_cap_skips_then_window_reopens(self):
        clock = [1000.0]
        rgn = watch.Regenerator(self.driver, self.ledger_path, rate=1,
                                now=lambda: clock[0])
        takes = {}
        clips = []
        for i, shot in enumerate(("shot-a", "shot-b", "shot-c")):
            clip, t = make_kill(self.dir, "%s.mp4" % shot, shot=shot,
                                ident=i + 1)
            takes[clip] = t
            clips.append((clip, t))
        ev = rgn.on_kill(clips[0][1], clips[0][0], takes)
        self.assertEqual(ev["action"], "submitted")
        ev = rgn.on_kill(clips[1][1], clips[1][0], takes)
        self.assertEqual(ev["action"], "skipped")
        self.assertIn("rate cap 1/min", ev["reason"])
        clock[0] += 61.0
        ev = rgn.on_kill(clips[2][1], clips[2][0], takes)
        self.assertEqual(ev["action"], "submitted")

    def test_failed_submits_count_against_the_cap(self):
        bad = write_bad_driver(self.dir, "dies.py",
                               "import sys\n"
                               "sys.stderr.write('gpu on fire')\n"
                               "sys.exit(1)\n")
        rgn = watch.Regenerator(bad, self.ledger_path, rate=1,
                                now=lambda: 1000.0)
        clip, t = make_kill(self.dir, "a.mp4")
        ev = rgn.on_kill(t, clip, {clip: t})
        self.assertEqual(ev["action"], "driver-error")
        ev = rgn.on_kill(t, clip, {clip: t})
        self.assertEqual(ev["action"], "skipped")
        self.assertIn("rate cap", ev["reason"])


class FailureBlockTests(RegenBase):
    def test_consecutive_failures_block_the_shot(self):
        bad = write_bad_driver(self.dir, "dies.py",
                               "import sys\n"
                               "sys.stderr.write('gpu on fire')\n"
                               "sys.exit(1)\n")
        rgn = watch.Regenerator(bad, self.ledger_path, fail_block=2)
        clip, t = make_kill(self.dir, "a.mp4")
        for want_failures in (1, 2):
            ev = rgn.on_kill(t, clip, {clip: t})
            self.assertEqual(ev["action"], "driver-error")
            self.assertEqual(ev["failures"], want_failures)
            self.assertIn("gpu on fire", ev["error"])
        ev = rgn.on_kill(t, clip, {clip: t})
        self.assertEqual(ev["action"], "skipped")
        self.assertIn("2 straight driver failures", ev["reason"])
        self.assertEqual(ledger.load(self.ledger_path)["jobs"], {})

    def test_a_success_resets_the_count(self):
        flaky_spool = os.path.join(self.dir, "flaky")
        os.makedirs(flaky_spool)
        # Fails on the first submit, works after: the marker file is
        # the one-shot fuse.
        marker = os.path.join(self.dir, "failed-once")
        script = os.path.join(self.dir, "flaky.py")
        with open(script, "w") as f:
            f.write("import json, os, sys\n"
                    "MARKER = %r\n"
                    "SPOOL = %r\n"
                    "if not os.path.exists(MARKER):\n"
                    "    open(MARKER, 'w').close()\n"
                    "    sys.exit(1)\n"
                    "job = json.load(sys.stdin)\n"
                    "jid = 'job-%%03d' %% len(os.listdir(SPOOL))\n"
                    "with open(os.path.join(SPOOL, jid), 'w') as f:\n"
                    "    json.dump(job, f)\n"
                    "print(jid)\n" % (marker, flaky_spool))
        flaky = '"%s" "%s"' % (sys.executable, script)
        rgn = watch.Regenerator(flaky, self.ledger_path, fail_block=2)
        clip, t = make_kill(self.dir, "a.mp4")
        self.assertEqual(rgn.on_kill(t, clip, {clip: t})["action"],
                         "driver-error")
        self.assertEqual(rgn.on_kill(t, clip, {clip: t})["action"],
                         "submitted")
        self.assertEqual(rgn.failures["shot-07"], 0)


class CliUsageTests(unittest.TestCase):
    # All three refuse before the loop starts, so main() returns.

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dailies-watch-cli-")
        self.addCleanup(shutil.rmtree, self.dir)

    def test_dry_run_needs_regen(self):
        self.assertEqual(main(["watch", self.dir, "--dry-run"]), 2)

    def test_want_needs_regen(self):
        self.assertEqual(main(["watch", self.dir, "--want",
                               "shot-07=2"]), 2)

    def test_malformed_want_exits_2(self):
        self.assertEqual(main(["watch", self.dir, "--regen", "drv",
                               "--want", "shot-07=zero"]), 2)


class ReconcileOnStartTests(RegenBase):
    def test_landed_pending_job_settles_without_polling(self):
        landed = os.path.join(self.dir, "landed.mp4")
        with open(landed, "wb") as f:
            f.write(b"pixels")
        led = ledger.fresh()
        ledger.record_submit(led, "/no/such/driver", "shot-07",
                             {"job": "j1", "clip": landed,
                              "parent": "p", "recipe": None})
        ledger.save(self.ledger_path, led)
        watch.Regenerator("/no/such/driver", self.ledger_path)
        job = ledger.load(self.ledger_path)["jobs"]["j1"]
        self.assertEqual(job["state"], "done")
        self.assertTrue(job["resolved"])


if __name__ == "__main__":
    unittest.main()
