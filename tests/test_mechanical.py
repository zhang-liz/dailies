"""End-to-end tests over synthetic clips generated with ffmpeg.

Run from tools/dailies: python3 -m unittest discover tests
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dailies import mechanical, take  # noqa: E402
from dailies.cli import main  # noqa: E402

FFMPEG = shutil.which("ffmpeg")


def gen(path, lavfi, seconds=2, fps=12):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", lavfi, "-t", str(seconds), "-r", str(fps),
         "-pix_fmt", "yuv420p", path],
        check=True)


@unittest.skipUnless(FFMPEG, "ffmpeg not on PATH")
class MechanicalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="dailies-test-")
        cls.shot = os.path.join(cls.dir, "shot-01")
        os.makedirs(cls.shot)
        cls.normal = os.path.join(cls.shot, "normal.mp4")
        cls.black = os.path.join(cls.shot, "black.mp4")
        cls.frozen = os.path.join(cls.shot, "frozen.mp4")
        cls.garbage = os.path.join(cls.shot, "garbage.mp4")
        gen(cls.normal, "testsrc2=size=320x240:rate=12")
        gen(cls.black, "color=c=black:size=320x240:rate=12")
        gen(cls.frozen, "color=c=gray:size=320x240:rate=12")
        with open(cls.garbage, "wb") as f:
            f.write(b"not a video at all")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir)

    def test_normal_clip_survives(self):
        r = mechanical.review(self.normal)
        self.assertEqual(r["verdict"], "review")
        self.assertEqual(r["mechanical"]["kill_reasons"], [])
        self.assertTrue(r["mechanical"]["candidate_frames"])

    def test_black_clip_killed(self):
        r = mechanical.review(self.black)
        self.assertEqual(r["verdict"], "kill")
        self.assertTrue(any("black" in k for k in
                            r["mechanical"]["kill_reasons"]))

    def test_frozen_clip_killed(self):
        r = mechanical.review(self.frozen)
        self.assertEqual(r["verdict"], "kill")
        self.assertTrue(any("frozen" in k for k in
                            r["mechanical"]["kill_reasons"]))

    def test_garbage_file_killed_not_crashed(self):
        r = mechanical.review(self.garbage)
        self.assertEqual(r["verdict"], "kill")
        self.assertTrue(r["mechanical"]["kill_reasons"])

    def test_probe_reads_geometry(self):
        info = mechanical.probe(self.normal)
        self.assertEqual(info["errors"], [])
        self.assertEqual((info["width"], info["height"]), (320, 240))
        self.assertEqual(info["fps"], 12)
        self.assertEqual(info["frames"], 24)

    def test_cli_writes_sidecars_and_ranks(self):
        rc = main(["review", self.shot, "--json"])
        self.assertEqual(rc, 0)
        t = json.loads(open(take.sidecar_path(self.normal)).read())
        self.assertEqual(t["shot"], "shot-01")
        self.assertTrue(t["take_id"].startswith(("sha256:", "blake3:")))
        # The only surviving take ranks first; kills rank behind it.
        self.assertEqual(t["review"]["rank_in_shot"], 1)
        dead = json.loads(open(take.sidecar_path(self.black)).read())
        self.assertGreater(dead["review"]["rank_in_shot"], 1)

    def test_cli_cache_skips_rereview(self):
        main(["review", self.normal])
        before = os.path.getmtime(take.sidecar_path(self.normal))
        stamp = json.loads(open(take.sidecar_path(self.normal)).read())
        main(["review", self.normal])
        after = json.loads(open(take.sidecar_path(self.normal)).read())
        self.assertEqual(stamp["created"], after["created"])
        self.assertEqual(stamp["take_id"], after["take_id"])
        # mtime may update (rank rewrite); review content must not change
        self.assertEqual(stamp["review"], after["review"])
        self.assertGreaterEqual(os.path.getmtime(
            take.sidecar_path(self.normal)), before)

    def test_candidate_frames_cover_calm_stretches(self):
        # One YDIF peak at the start; a 4 second calm tail must still
        # send frames to the judge, at roughly the uniform interval.
        series = [(i * 0.25, 100.0, 20.0 if i == 1 else 0.5)
                  for i in range(17)]  # 0..4s
        times = mechanical.candidate_frames(series)
        calm = [t for t in times if t >= 1.0]
        self.assertGreaterEqual(len(calm), 5)
        self.assertLessEqual(len(times), mechanical.MAX_FRAMES)

    def test_motion_smoothness_ranks_jerky_below_smooth(self):
        jerky = os.path.join(self.shot, "jerky.mp4")
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", self.normal,
             "-vf", "shuffleframes=4 3 2 1 0", "-pix_fmt", "yuv420p",
             jerky], check=True)
        self.addCleanup(os.unlink, jerky)
        smooth_score = mechanical.motion_smoothness(self.normal, 12)
        jerky_score = mechanical.motion_smoothness(jerky, 12)
        self.assertIsNotNone(smooth_score)
        self.assertGreater(smooth_score, jerky_score)

    def test_motion_smoothness_none_without_fps(self):
        self.assertIsNone(mechanical.motion_smoothness(self.normal, None))

    def test_flicker_masks_genuine_motion(self):
        # Mostly static clip with one burst of fast action: the luma
        # jumps during the burst are motion, not flicker.
        static = [(i * 0.1, 100.0, 1.0) for i in range(20)]
        burst = [(2.0 + i * 0.1, 100.0 + 40 * (i % 2), 30.0)
                 for i in range(4)]
        self.assertLess(mechanical.flicker_score(static + burst), 0.01)

    def test_flicker_throughout_still_counts(self):
        # Luma oscillates on every frame; the median YDIF rises with it,
        # so the mask leaves the flicker visible.
        series = [(i * 0.1, 100.0 + 30 * (i % 2), 25.0) for i in range(20)]
        self.assertGreater(mechanical.flicker_score(series), 0.05)

    def test_sidecar_preserves_foreign_blocks(self):
        # slate owns "recipe"; a review pass must not clobber it.
        t = take.load(self.normal)
        t["recipe"] = {"seeds": {"3": 424242}}
        take.save(self.normal, t)
        main(["review", self.normal, "--force"])
        t2 = take.load(self.normal)
        self.assertEqual(t2["recipe"], {"seeds": {"3": 424242}})


if __name__ == "__main__":
    unittest.main()
