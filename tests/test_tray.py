"""Tray icon: the critter with the blob's status marks.

Run:  py -3.14 -B -m unittest discover -s tests
"""
import os
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claude_mini import gdiplus as G, model, tray     # noqa: E402


class TrayIcon(unittest.TestCase):
    def test_marks(self):
        s = model.Snapshot()
        self.assertEqual(tray.icon_marks(s), (None, False, False))
        s.needs_me = 2
        self.assertEqual(tray.icon_marks(s), ("F59E0B", False, False))         # amber dot: needs you
        s = model.Snapshot()
        s.finished = 1
        self.assertEqual(tray.icon_marks(s)[0], "3B82F6")                       # blue dot: something new
        s = model.Snapshot()
        s.warnings.append("sidebar hidden")
        self.assertEqual(tray.icon_marks(s), (None, True, False))              # can't read Claude's status
        self.assertEqual(tray.icon_marks(model.Snapshot(claude_running=False)), (None, False, True))   # faded

    def test_draws_at_tray_sizes(self):
        G.startup()
        for n in (16, 20, 24, 32):
            for marks in ((None, False, False), ("F59E0B", True, False), (None, False, True)):
                cv = G.Canvas(n, n)
                try:
                    cv.clear(0)
                    tray.draw_icon(cv, n, *marks)
                    h = cv.to_hicon()
                    self.assertTrue(h)
                finally:
                    cv.close()


if __name__ == "__main__":
    unittest.main()
