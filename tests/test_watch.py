import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dailies import take, watch  # noqa: E402

FFMPEG = shutil.which("ffmpeg")


def gen(path, lavfi, seconds=1, fps=8):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", lavfi, "-t", str(seconds), "-r", str(fps),
         "-pix_fmt", "yuv420p", path],
        check=True)


def wait_for(cond, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.1)
    return False


@unittest.skipUnless(FFMPEG, "ffmpeg not on PATH")
class WatchTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dailies-watch-test-")
        self.shot = os.path.join(self.dir, "shot-01")
        os.makedirs(self.shot)
        self.stop = threading.Event()
        self.events = []
        self.report = os.path.join(self.dir, "report.html")
        self.thread = threading.Thread(
            target=watch.loop,
            args=(self.dir,),
            kwargs={"interval": 0.2, "settle": 0.6,
                    "report_path": self.report, "stop": self.stop,
                    "emit": lambda t, c: self.events.append((t, c))},
            daemon=True)
        self.thread.start()

    def tearDown(self):
        self.stop.set()
        self.thread.join(timeout=10)
        shutil.rmtree(self.dir)

    def test_new_clip_reviewed_and_report_built(self):
        clip = os.path.join(self.shot, "take-001.mp4")
        gen(clip, "testsrc2=size=320x240:rate=8")
        self.assertTrue(wait_for(
            lambda: os.path.exists(take.sidecar_path(clip))))
        self.assertTrue(wait_for(lambda: len(self.events) == 1))
        t = self.events[0][0]
        self.assertEqual(t["review"]["verdict"], "review")
        self.assertEqual(t["shot"], "shot-01")
        self.assertTrue(wait_for(lambda: os.path.exists(self.report)))
        self.assertIn("shot-01", open(self.report).read())

    def test_ranks_update_as_takes_land(self):
        good = os.path.join(self.shot, "good.mp4")
        dead = os.path.join(self.shot, "dead.mp4")
        gen(dead, "color=c=black:size=320x240:rate=8")
        self.assertTrue(wait_for(
            lambda: os.path.exists(take.sidecar_path(dead))))
        gen(good, "testsrc2=size=320x240:rate=8")
        self.assertTrue(wait_for(
            lambda: os.path.exists(take.sidecar_path(good))))
        self.assertTrue(wait_for(
            lambda: take.load(good).get("review", {})
            .get("rank_in_shot") == 1))
        self.assertTrue(wait_for(
            lambda: take.load(dead).get("review", {})
            .get("rank_in_shot") == 2))

    def test_growing_file_not_reviewed_until_stable(self):
        # Simulate an encoder writing incrementally: keep appending, then
        # finish with a valid clip.
        partial = os.path.join(self.shot, "growing.mp4")
        with open(partial, "wb") as f:
            for _ in range(5):
                f.write(b"\0" * 4096)
                f.flush()
                os.fsync(f.fileno())
                time.sleep(0.2)
        # While it was growing, no sidecar may exist.
        self.assertFalse(os.path.exists(take.sidecar_path(partial)))
        gen(partial, "testsrc2=size=320x240:rate=8")
        self.assertTrue(wait_for(
            lambda: os.path.exists(take.sidecar_path(partial))))
        t = take.load(partial)
        # Final review is of the finished file, not the garbage prefix.
        self.assertEqual(t["review"]["verdict"], "review")

    def test_already_reviewed_takes_skipped_on_restart(self):
        clip = os.path.join(self.shot, "old.mp4")
        gen(clip, "testsrc2=size=320x240:rate=8")
        self.assertTrue(wait_for(lambda: len(self.events) == 1))
        # Restart the watcher; the reviewed take must not re-emit.
        self.stop.set()
        self.thread.join(timeout=10)
        self.stop = threading.Event()
        self.thread = threading.Thread(
            target=watch.loop, args=(self.dir,),
            kwargs={"interval": 0.2, "settle": 0.6, "stop": self.stop,
                    "emit": lambda t, c: self.events.append((t, c))},
            daemon=True)
        self.thread.start()
        time.sleep(1.0)
        self.assertEqual(len(self.events), 1)


if __name__ == "__main__":
    unittest.main()
