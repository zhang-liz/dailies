"""Regen driver contract, exercised against a fake driver: a small
script written to disk that spools submitted jobs and, after a couple
of polls, copies a synthetic clip into the requested destination.
Anything the fake driver can get wrong, a live one can too, so the
contract violations are each pinned."""

import json
import glob
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dailies import pipeline, regen, take  # noqa: E402

FFMPEG = shutil.which("ffmpeg")


def write_driver(dirpath, spool, src, done_at=2):
    """A contract-honoring fake: submit spools the job and prints an
    id; poll answers running until done_at, then copies src to the
    job's clip path and reports done."""
    script = os.path.join(dirpath, "driver.py")
    body = (
        "import json, os, shutil, sys\n"
        "SPOOL = %r\n"
        "SRC = %r\n"
        "DONE_AT = %d\n"
        "def submit():\n"
        "    job = json.load(sys.stdin)\n"
        "    jid = 'job-%%03d' %% len(os.listdir(SPOOL))\n"
        "    with open(os.path.join(SPOOL, jid), 'w') as f:\n"
        "        json.dump({'job': job, 'polls': 0}, f)\n"
        "    print(jid)\n"
        "def poll(jid):\n"
        "    path = os.path.join(SPOOL, jid)\n"
        "    with open(path) as f:\n"
        "        rec = json.load(f)\n"
        "    rec['polls'] += 1\n"
        "    with open(path, 'w') as f:\n"
        "        json.dump(rec, f)\n"
        "    if rec['polls'] < DONE_AT:\n"
        "        print(json.dumps({'state': 'running'}))\n"
        "        return\n"
        "    shutil.copy(SRC, rec['job']['clip'])\n"
        "    print(json.dumps({'state': 'done',\n"
        "                      'output': rec['job']['clip']}))\n"
        "if sys.argv[1] == 'submit':\n"
        "    submit()\n"
        "else:\n"
        "    poll(sys.argv[2])\n"
    ) % (spool, src, done_at)
    with open(script, "w") as f:
        f.write(body)
    return '"%s" "%s"' % (sys.executable, script)


def write_bad_driver(dirpath, name, body):
    script = os.path.join(dirpath, name)
    with open(script, "w") as f:
        f.write(body)
    return '"%s" "%s"' % (sys.executable, script)


def make_failed(dirpath):
    """A fabricated killed take with a recipe, no ffmpeg needed."""
    clip = os.path.join(dirpath, "take-031.mp4")
    with open(clip, "wb") as f:
        f.write(b"dead pixels")
    t = take.load(clip)
    t.update({"take_id": "sha256:%064x" % 7, "shot": "shot-07",
              "created": "2026-08-09T00:00:00Z",
              "recipe": {"workflow": {"3": {"class_type": "KSampler"}},
                         "seeds": {"3": 424242},
                         "prompt_text": "slow dolly"},
              "review": {"mechanical": {"kill_reasons": ["black"]},
                         "vlm": None, "verdict": "kill",
                         "rank_in_shot": 2}})
    take.save(clip, t)
    return clip, t


class MutateTests(unittest.TestCase):
    def test_every_seed_replaced_rest_verbatim(self):
        recipe = {"workflow": {"3": {"class_type": "KSampler"}},
                  "seeds": {"3": 424242, "9": 7},
                  "prompt_text": "slow dolly"}
        out = regen.mutate(recipe)
        self.assertEqual(set(out["seeds"]), {"3", "9"})
        self.assertNotEqual(out["seeds"]["3"], 424242)
        self.assertNotEqual(out["seeds"]["9"], 7)
        for v in out["seeds"].values():
            self.assertIsInstance(v, int)
        self.assertEqual(out["workflow"], recipe["workflow"])
        self.assertEqual(out["prompt_text"], "slow dolly")

    def test_input_recipe_untouched(self):
        recipe = {"workflow": {"3": {}}, "seeds": {"3": 1}}
        out = regen.mutate(recipe)
        self.assertEqual(recipe["seeds"], {"3": 1})
        out["workflow"]["3"]["poked"] = True
        self.assertNotIn("poked", recipe["workflow"]["3"])

    def test_seedless_and_null_recipes_still_get_a_seed(self):
        for recipe in (None, {}, {"prompt_text": "x"},
                       {"seeds": {}}):
            out = regen.mutate(recipe)
            self.assertEqual(list(out["seeds"]), ["seed"], recipe)
            self.assertIsInstance(out["seeds"]["seed"], int)
        self.assertEqual(regen.mutate({"prompt_text": "x"})
                         ["prompt_text"], "x")


class StubTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dailies-regen-stub-")
        self.addCleanup(shutil.rmtree, self.dir)

    def test_stub_preserves_foreign_blocks(self):
        clip = os.path.join(self.dir, "new.mp4")
        take.save(clip, {"output": {"file": "new.mp4"},
                         "slate": {"scene": 4},
                         "notes": "keep me"})
        regen.write_stub(clip, "sha256:%064x" % 1,
                         {"seeds": {"3": 9}}, shot="shot-07")
        t = take.load(clip)
        self.assertEqual(t["slate"], {"scene": 4})
        self.assertEqual(t["notes"], "keep me")
        self.assertEqual(t["parent"], "sha256:%064x" % 1)
        self.assertEqual(t["recipe"], {"seeds": {"3": 9}})
        self.assertEqual(t["shot"], "shot-07")
        self.assertTrue(t["created"])

    def test_fresh_stub_has_skeleton_and_no_review(self):
        clip = os.path.join(self.dir, "fresh.mp4")
        regen.write_stub(clip, "sha256:%064x" % 2, {"seeds": {"seed": 5}})
        t = take.load(clip)
        self.assertIsNone(t["take_id"])
        self.assertIsNone(t["review"])
        self.assertEqual(t["output"]["file"], "fresh.mp4")

    def test_new_clip_path_sibling_unique_same_ext(self):
        failed = os.path.join(self.dir, "take-031.mp4")
        open(failed, "wb").close()
        seen = set()
        for _ in range(3):
            p = regen.new_clip_path(failed)
            self.assertEqual(os.path.dirname(p),
                             os.path.dirname(os.path.abspath(failed)))
            self.assertTrue(os.path.basename(p)
                            .startswith("take-031-regen-"))
            self.assertTrue(p.endswith(".mp4"))
            open(p, "wb").close()  # occupy it; next call must move on
            self.assertNotIn(p, seen)
            seen.add(p)

    def test_new_clip_path_honors_out_dir(self):
        failed = os.path.join(self.dir, "take-031.mp4")
        target = os.path.join(self.dir, "landing")
        os.makedirs(target)
        self.assertEqual(os.path.dirname(
            regen.new_clip_path(failed, target)), target)


class FakeDriverTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dailies-regen-test-")
        self.addCleanup(shutil.rmtree, self.dir)
        self.spool = os.path.join(self.dir, "spool")
        os.makedirs(self.spool)
        self.src = os.path.join(self.dir, "src.mp4")
        with open(self.src, "wb") as f:
            f.write(b"fresh pixels")
        self.driver = write_driver(self.dir, self.spool, self.src)

    def regen_stubs(self, dirpath):
        return [p for p in glob.glob(os.path.join(dirpath,
                                                  "*.take.json"))
                if "-regen-" in os.path.basename(p)]

    def test_submit_then_poll_runs_to_done(self):
        clip = os.path.join(self.dir, "out.mp4")
        jid = regen.submit(self.driver,
                           {"clip": clip, "parent": "sha256:%064x" % 3,
                            "shot": "s", "recipe": {"seeds": {"3": 1}}})
        self.assertEqual(jid, "job-000")
        self.assertEqual(regen.poll(self.driver, jid)["state"],
                         "running")
        status = regen.poll(self.driver, jid)
        self.assertEqual(status["state"], "done")
        self.assertEqual(status["output"], clip)
        with open(clip, "rb") as f:
            self.assertEqual(f.read(), b"fresh pixels")

    def test_resubmit_writes_provenance_and_lands_clip(self):
        failed, parent = make_failed(self.dir)
        job = regen.resubmit(self.driver, failed)
        self.assertIn("-regen-", os.path.basename(job["clip"]))
        stub = take.load(job["clip"])
        self.assertEqual(stub["parent"], parent["take_id"])
        self.assertEqual(stub["shot"], "shot-07")
        self.assertIsNone(stub["take_id"])
        self.assertIsNone(stub["review"])
        self.assertNotEqual(stub["recipe"]["seeds"]["3"], 424242)
        self.assertEqual(stub["recipe"]["workflow"],
                         parent["recipe"]["workflow"])
        self.assertEqual(stub["regen"]["job"], job["job"])
        self.assertEqual(stub["regen"]["driver"], self.driver)
        self.assertTrue(stub["regen"]["submitted"])
        status = regen.wait(self.driver, job["job"], interval=0.05,
                            timeout=10)
        self.assertEqual(status["state"], "done")
        self.assertEqual(status["output"], job["clip"])
        with open(job["clip"], "rb") as f:
            self.assertEqual(f.read(), b"fresh pixels")
        # The landed clip's stub survived the wait untouched.
        self.assertEqual(take.load(job["clip"])["parent"],
                         parent["take_id"])

    def test_stub_survives_a_failing_submit(self):
        # Provenance before pixels: the stub must exist even when the
        # driver dies on submit, so nothing about the attempt is lost.
        failed, parent = make_failed(self.dir)
        bad = write_bad_driver(self.dir, "dies.py",
                               "import sys\n"
                               "sys.stderr.write('gpu on fire')\n"
                               "sys.exit(1)\n")
        with self.assertRaises(regen.DriverError) as ctx:
            regen.resubmit(bad, failed)
        self.assertIn("gpu on fire", str(ctx.exception))
        stubs = self.regen_stubs(self.dir)
        self.assertEqual(len(stubs), 1)
        with open(stubs[0]) as f:
            doc = json.load(f)
        self.assertEqual(doc["parent"], parent["take_id"])
        self.assertNotEqual(doc["recipe"]["seeds"]["3"], 424242)
        self.assertNotIn("regen", doc)  # no job id: submit never took

    def test_contract_violations_raise(self):
        cases = [
            ("empty.py", "pass\n"),  # submit prints nothing
            ("chatty.py", "print('id-1')\nprint('and more')\n"),
        ]
        for name, body in cases:
            bad = write_bad_driver(self.dir, name, body)
            with self.assertRaises(regen.DriverError, msg=name):
                regen.submit(bad, {"clip": "x", "recipe": {}})
        poll_cases = [
            ("garbage.py", "print('not json')\n"),
            ("offmenu.py", "import json\n"
             "print(json.dumps({'state': 'cooking'}))\n"),
            ("noput.py", "import json\n"
             "print(json.dumps({'state': 'done'}))\n"),
            ("alist.py", "print('[1, 2]')\n"),
        ]
        for name, body in poll_cases:
            bad = write_bad_driver(self.dir, name, body)
            with self.assertRaises(regen.DriverError, msg=name):
                regen.poll(bad, "job-000")

    def test_missing_driver_binary_raises(self):
        with self.assertRaises(regen.DriverError):
            regen.submit("/no/such/driver-binary", {"recipe": {}})

    def test_wait_times_out_on_a_stalled_job(self):
        stalled = write_driver(self.dir, self.spool, self.src,
                               done_at=10 ** 6)
        jid = regen.submit(stalled, {"clip": os.path.join(self.dir,
                                                          "never.mp4"),
                                     "recipe": {}})
        with self.assertRaises(regen.DriverError):
            regen.wait(stalled, jid, interval=0.05, timeout=0.3)


@unittest.skipUnless(FFMPEG, "ffmpeg not on PATH")
class RegenReviewIntegrationTests(unittest.TestCase):
    # The full circle: a killed take resubmits, the fake driver lands a
    # synthetic clip, and reviewing it keeps parent, recipe, and the
    # regen block intact per SPEC's preserve-unknown-keys law.

    def test_landed_clip_reviews_with_lineage_intact(self):
        d = tempfile.mkdtemp(prefix="dailies-regen-int-")
        self.addCleanup(shutil.rmtree, d)
        spool = os.path.join(d, "spool")
        os.makedirs(spool)
        src = os.path.join(d, "src.mp4")
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
             "-i", "testsrc2=size=320x240:rate=8", "-t", "1",
             "-r", "8", "-pix_fmt", "yuv420p", src],
            check=True)
        driver = write_driver(d, spool, src)
        shot_dir = os.path.join(d, "shot-07")
        os.makedirs(shot_dir)
        failed, parent = make_failed(shot_dir)
        job = regen.resubmit(driver, failed)
        status = regen.wait(driver, job["job"], interval=0.05,
                            timeout=10)
        t, cached = pipeline.review_clip(status["output"])
        self.assertFalse(cached)
        self.assertEqual(t["parent"], parent["take_id"])
        self.assertEqual(t["recipe"]["workflow"],
                         parent["recipe"]["workflow"])
        self.assertEqual(t["regen"]["job"], job["job"])
        self.assertEqual(t["shot"], "shot-07")
        self.assertEqual(t["review"]["verdict"], "review")
        self.assertTrue((t["take_id"] or "").split(":")[0]
                        in ("sha256", "blake3"))


if __name__ == "__main__":
    unittest.main()
