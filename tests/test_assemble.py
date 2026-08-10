"""assemble tests over synthetic clips: two shots with mixed geometry
and rates, so normalization is exercised on every run."""

import csv
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dailies import assemble, mechanical, take  # noqa: E402
from dailies.cli import main  # noqa: E402

FFMPEG = shutil.which("ffmpeg")


def gen(path, lavfi, seconds=1, fps=8):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", lavfi, "-t", str(seconds), "-r", str(fps),
         "-pix_fmt", "yuv420p", path],
        check=True)


def read_rows(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


@unittest.skipUnless(FFMPEG, "ffmpeg not on PATH")
class AssembleTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dailies-assemble-test-")
        s1 = os.path.join(self.dir, "shot-01")
        s2 = os.path.join(self.dir, "shot-02")
        os.makedirs(s1)
        os.makedirs(s2)
        self.good1 = os.path.join(s1, "good.mp4")
        self.good2 = os.path.join(s2, "good.mp4")
        gen(self.good1, "testsrc2=size=320x240:rate=8")
        gen(os.path.join(s1, "dead.mp4"), "color=c=black:size=320x240:rate=8")
        # Different size and rate: the cut must normalize, not glitch.
        gen(self.good2, "testsrc2=size=480x270:rate=16", fps=16)
        main(["review", self.dir])
        self.cut = os.path.join(self.dir, "cut.mp4")

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_cut_joins_best_take_per_shot(self):
        rc = main(["assemble", self.dir, "-o", self.cut])
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(self.cut))
        rows = read_rows(os.path.join(self.dir, "cut.csv"))
        self.assertEqual([r["shot"] for r in rows], ["shot-01", "shot-02"])
        self.assertEqual([os.path.basename(r["file"]) for r in rows],
                         ["good.mp4", "good.mp4"])
        # The kill never reaches the cut, and survivors carry their rank.
        self.assertNotIn("dead.mp4", [os.path.basename(r["file"])
                                      for r in rows])
        self.assertEqual([r["rank"] for r in rows], ["1", "1"])
        self.assertEqual([r["verdict"] for r in rows], ["review", "review"])
        # Timecodes chain: each segment starts where the last ended, and
        # the cut's probed duration matches the mapped total.
        self.assertEqual(rows[0]["record_in"], "0.0")
        self.assertEqual(rows[0]["record_out"], rows[1]["record_in"])
        info = mechanical.probe(self.cut)
        self.assertEqual(info["errors"], [])
        total = float(rows[-1]["record_out"])
        self.assertAlmostEqual(info["duration"], total, delta=0.5)
        self.assertAlmostEqual(total, 2.0, delta=0.5)
        # First cut take sets the geometry; shot-02 was normalized to it.
        self.assertEqual((info["width"], info["height"]), (320, 240))

    def test_csv_take_ids_match_sidecars(self):
        main(["assemble", self.dir, "-o", self.cut])
        rows = read_rows(os.path.join(self.dir, "cut.csv"))
        self.assertEqual(rows[0]["take_id"],
                         take.load(self.good1)["take_id"])
        self.assertEqual(rows[1]["take_id"],
                         take.load(self.good2)["take_id"])

    def test_alts_appends_runners_up(self):
        alt = os.path.join(self.dir, "shot-01", "alt.mp4")
        gen(alt, "testsrc=size=320x240:rate=8")
        main(["review", self.dir])
        main(["assemble", self.dir, "-o", self.cut, "--alts", "1"])
        rows = read_rows(os.path.join(self.dir, "cut.csv"))
        self.assertEqual([r["shot"] for r in rows],
                         ["shot-01", "shot-01", "shot-02"])
        self.assertEqual([r["rank"] for r in rows[:2]], ["1", "2"])

    def test_shot_list_sets_the_order(self):
        shots = os.path.join(self.dir, "shots.txt")
        with open(shots, "w") as f:
            f.write("# reel order\nshot-02\nshot-01\n")
        main(["assemble", self.dir, "-o", self.cut, "--shots", shots])
        rows = read_rows(os.path.join(self.dir, "cut.csv"))
        self.assertEqual([r["shot"] for r in rows], ["shot-02", "shot-01"])

    def test_select_excludes_kills_and_ranks(self):
        def fake(shot, verdict, rank):
            return {"shot": shot, "take_id": "sha256:%d" % rank,
                    "review": {"verdict": verdict, "rank_in_shot": rank}}
        takes = [fake("s1", "kill", 3), fake("s1", "review", 2),
                 fake("s1", "keep", 1)]
        picked = assemble.select(takes, alts=1)
        self.assertEqual([t["review"]["rank_in_shot"]
                          for t in picked["s1"]], [1, 2])

    def test_slate_text_names_the_top_defect_on_review(self):
        t = {"shot": "shot-07", "take_id": "sha256:abcdef0123456789",
             "review": {"verdict": "review", "vlm": {"defects": [
                 {"rule": "physics.gravity", "severity": 2},
                 {"rule": "anatomy.hands", "severity": 4,
                  "confidence": 0.67}]}}}
        self.assertEqual(assemble.slate_text(t),
                         "shot-07  abcdef01  review  anatomy.hands")
        t["review"]["verdict"] = "keep"
        self.assertEqual(assemble.slate_text(t),
                         "shot-07  abcdef01  keep")

    def test_drawtext_escape_guards_filter_specials(self):
        out = assemble.drawtext_escape("shot's cut: a,b")
        self.assertEqual(out, "shot\\'s cut\\: a\\,b")

    def test_empty_dir_errors_cleanly(self):
        empty = tempfile.mkdtemp(prefix="dailies-assemble-empty-")
        self.addCleanup(shutil.rmtree, empty)
        rc = main(["assemble", empty, "-o", self.cut])
        self.assertEqual(rc, 2)
        self.assertFalse(os.path.exists(self.cut))


if __name__ == "__main__":
    unittest.main()
