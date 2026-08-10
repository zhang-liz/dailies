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


@unittest.skipUnless(FFMPEG, "ffmpeg not on PATH")
class DoomedBreakerTests(unittest.TestCase):
    # Lowered confidence so three mechanical kills trip the breaker;
    # the default eight-kill trip is pinned in test_breaker.

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dailies-doom-test-")
        self.shot = os.path.join(self.dir, "shot-09")
        os.makedirs(self.shot)
        self.stop = threading.Event()
        self.events = []
        self.doomed = []
        self.thread = threading.Thread(
            target=watch.loop, args=(self.dir,),
            kwargs={"interval": 0.2, "settle": 0.6, "stop": self.stop,
                    "doom_confidence": 0.6,
                    "emit": lambda t, c: self.events.append((t, c)),
                    "on_doomed":
                        lambda s, st: self.doomed.append((s, st))},
            daemon=True)
        self.thread.start()

    def tearDown(self):
        self.stop.set()
        self.thread.join(timeout=10)
        shutil.rmtree(self.dir)

    def test_kills_trip_breaker_once(self):
        for i in range(3):
            gen(os.path.join(self.shot, "dead-%d.mp4" % i),
                "color=c=black:size=320x240:rate=8")
        self.assertTrue(wait_for(lambda: len(self.doomed) == 1))
        s, st = self.doomed[0]
        self.assertEqual(s, "shot-09")
        self.assertEqual(st["mechanical_kills"], 3)
        self.assertTrue(st["doomed"])
        self.assertTrue(st["worst"].endswith(".mp4"))
        self.assertTrue(os.path.exists(take.sidecar_path(st["worst"])))
        # The take that tripped it carries the ephemeral flag, and the
        # flag never lands in the sidecar file.
        self.assertTrue(wait_for(lambda: len(self.events) == 3))
        self.assertTrue(self.events[-1][0]["_shot_doomed"])
        self.assertNotIn("_shot_doomed", take.load(self.events[-1][1]))
        # A fourth kill must not fire the hook again.
        gen(os.path.join(self.shot, "dead-3.mp4"),
            "color=c=black:size=320x240:rate=8")
        self.assertTrue(wait_for(lambda: len(self.events) == 4))
        self.assertEqual(len(self.doomed), 1)

    def test_healthy_shot_not_flagged(self):
        other = os.path.join(self.dir, "shot-10")
        os.makedirs(other)
        gen(os.path.join(other, "good.mp4"),
            "testsrc2=size=320x240:rate=8")
        self.assertTrue(wait_for(lambda: len(self.events) == 1))
        self.assertFalse(self.events[0][0]["_shot_doomed"])
        self.assertEqual(self.doomed, [])


@unittest.skipUnless(FFMPEG, "ffmpeg not on PATH")
class WatchCliDoomedTests(unittest.TestCase):
    # End to end through the CLI at default thresholds: eight black
    # clips, one doomed event line, one hook invocation.

    def test_json_lines_and_hook(self):
        d = tempfile.mkdtemp(prefix="dailies-cli-doom-test-")
        try:
            shot = os.path.join(d, "shot-13")
            os.makedirs(shot)
            for i in range(8):
                gen(os.path.join(shot, "dead-%d.mp4" % i),
                    "color=c=black:size=320x240:rate=8")
            hook_out = os.path.join(d, "hook.txt")
            script = os.path.join(d, "hook.py")
            with open(script, "w") as f:
                f.write("import sys\n"
                        "open(%r, 'w').write('|'.join(sys.argv[1:]))\n"
                        % hook_out)
            proc = subprocess.Popen(
                [sys.executable, "-m", "dailies", "watch", d,
                 "--interval", "0.2", "--json",
                 "--on-doomed", '"%s" "%s"' % (sys.executable, script)],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True,
                cwd=os.path.join(os.path.dirname(__file__), ".."))
            lines = []

            def reader():
                for line in proc.stdout:
                    lines.append(json.loads(line))

            threading.Thread(target=reader, daemon=True).start()
            try:
                self.assertTrue(wait_for(
                    lambda: any(l.get("event") == "doomed"
                                for l in lines), timeout=60))
            finally:
                proc.terminate()
                proc.wait(timeout=10)
            event = [l for l in lines if l.get("event") == "doomed"][0]
            self.assertEqual(event["shot"], "shot-13")
            self.assertEqual(event["mechanical_kills"], 8)
            self.assertTrue(event["worst_sidecar"].endswith(".take.json"))
            take_lines = [l for l in lines if "clip" in l]
            self.assertFalse(take_lines[0]["shot_doomed"])
            self.assertTrue(take_lines[-1]["shot_doomed"])
            self.assertTrue(wait_for(
                lambda: os.path.exists(hook_out)
                and "|" in open(hook_out).read()))
            self.assertEqual(open(hook_out).read(),
                             "shot-13|" + event["worst_sidecar"])
        finally:
            shutil.rmtree(d)


class HookTests(unittest.TestCase):
    def test_hook_receives_shot_and_sidecar(self):
        d = tempfile.mkdtemp(prefix="dailies-hook-test-")
        try:
            out = os.path.join(d, "out.txt")
            script = os.path.join(d, "hook.py")
            with open(script, "w") as f:
                f.write("import sys\n"
                        "open(%r, 'w').write('|'.join(sys.argv[1:]))\n"
                        % out)
            watch.run_hook('"%s" "%s"' % (sys.executable, script),
                           "shot-07", "/takes/worst.mp4.take.json")
            self.assertTrue(wait_for(
                lambda: os.path.exists(out)
                and "|" in open(out).read()))
            self.assertEqual(open(out).read(),
                             "shot-07|/takes/worst.mp4.take.json")
        finally:
            shutil.rmtree(d)

    def test_bad_hook_does_not_raise(self):
        watch.run_hook("/no/such/hook-binary", "shot-07", "x.take.json")


if __name__ == "__main__":
    unittest.main()
