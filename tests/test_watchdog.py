"""The watchdog's reading of why the critter stopped, and the in-memory log.

Run:  py -3.14 -B -m unittest discover -s tests
"""
import os
import sys
import threading
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claude_mini import diag, watchdog as WD     # noqa: E402


class WhyItStopped(unittest.TestCase):
    def test_exit_codes(self):
        self.assertEqual(WD.explain(0xC0000005, []), "it crashed (access violation)")
        self.assertIn("Stop-Process", WD.explain(0xFFFFFFFF, []))
        self.assertIn("0x0000002A", WD.explain(42, []))

    def test_exit_code_1_is_a_python_error_only_with_a_traceback(self):
        self.assertEqual(WD.explain(1, ["! Traceback (most recent call last):", "! ValueError: x"]),
                         "it hit a Python error")
        self.assertIn("another program ended it", WD.explain(1, ["read rows: -"]))


class Restarting(unittest.TestCase):
    """The watchdog's loop, with a fake critter process: no window, process or mutex is really made."""

    def run_watchdog(self, codes):
        runs, sleeps, boxes = [], [], []

        class Pipe(object):
            def __init__(self, run):
                self.run = run

            def write(self, data):
                self.run["handed"] = data.decode("utf-8")
                self.run["got"].set()

            def close(self):
                pass

        class Proc(object):
            def __init__(self, args, **kw):
                run = {"args": args, "got": threading.Event()}
                runs.append(run)
                self.stdin, self.stdout = Pipe(run), iter([b"line from run %d\n" % len(runs)])
                self.code = codes[len(runs) - 1]

            def wait(self):
                return self.code

        class Win32(object):
            CreateMutexW = staticmethod(lambda *a: 1)
            CloseHandle = staticmethod(lambda h: None)
            GetSystemMetrics = staticmethod(lambda i: 0)
            MessageBoxW = staticmethod(lambda *a: boxes.append(a[1]))

        saved = (WD.subprocess.Popen, WD.win32, WD.time.sleep, WD.ctypes.get_last_error)
        WD.subprocess.Popen, WD.win32, WD.time.sleep = Proc, Win32, sleeps.append
        WD.ctypes.get_last_error = lambda: 0
        try:
            result = WD.main("ClaudeMini.pyw", [])
        finally:
            WD.subprocess.Popen, WD.win32, WD.time.sleep, WD.ctypes.get_last_error = saved
        for run in runs:
            self.assertTrue(run["got"].wait(5.0))      # the log is handed over on a thread of its own
        return result, runs, sleeps, boxes

    def test_restart_from_the_tray_starts_it_again_at_once_with_the_log(self):
        result, runs, sleeps, boxes = self.run_watchdog([WD.RESTART_CODE, WD.RESTART_CODE, WD.RESTART_CODE,
                                                         WD.RESTART_CODE, 0])
        self.assertEqual((result, len(runs), sleeps, boxes), (0, 5, [], []))   # no delay, never "keeps stopping"
        self.assertFalse(any("--restarted" in r["args"] for r in runs))       # no "it stopped" notice
        self.assertIn("line from run 1", runs[1]["handed"])                    # the log carries on
        self.assertIn("restarted from the tray menu", runs[1]["handed"])

    def test_an_unexpected_stop_is_still_explained(self):
        result, runs, sleeps, boxes = self.run_watchdog([0xC0000005, 0])
        self.assertEqual((result, len(runs), sleeps), (0, 2, [WD.RESTART_DELAY]))
        self.assertEqual(runs[1]["args"][-2:], ["--restarted", "it crashed (access violation)"])


class Log(unittest.TestCase):
    def test_lines_are_kept_in_memory_with_the_earlier_runs(self):
        saved = diag.earlier
        try:
            diag.earlier = "01-11 10:00:00 started\n"
            diag.log("hello")
            text = diag.text()
            self.assertTrue(text.startswith("01-11 10:00:00 started\n--- this run ---\n"))
            self.assertTrue(text.endswith(" hello"))
        finally:
            diag.earlier = saved

    def test_error_output_becomes_log_lines(self):
        err = diag._Errors()
        err.write("Traceback (most recent call last):\n  File")
        err.write(' "x.py"\n')
        self.assertEqual(diag.text().split("\n")[-2:], [diag.text().split("\n")[-2], diag.text().split("\n")[-1]])
        self.assertTrue(diag.text().endswith('!   File "x.py"'))


if __name__ == "__main__":
    unittest.main()
