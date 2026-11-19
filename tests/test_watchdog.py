"""The watchdog's reading of why the critter stopped, and the in-memory log.

Run:  py -3.14 -B -m unittest discover -s tests
"""
import os
import sys
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
