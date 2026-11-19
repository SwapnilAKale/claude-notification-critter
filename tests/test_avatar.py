"""Avatar tests: snapshot -> state mapping, every state draws, frames stay inside the blob and are cheap.

Run:  py -3.14 -B -m unittest discover -s tests
"""
import math
import os
import sys
import time
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claude_mini import avatar as A, blob, model                    # noqa: E402
from claude_mini import gdiplus as G                                # noqa: E402
from claude_mini.app import DEMO_COUNT, demo_state                  # noqa: E402

KINDS = (model.PERMISSION, model.PLAN, model.QUESTION, model.PROBLEM, model.WAITING, model.FINISHED, model.UPDATE)
PLAIN = set(A.CRITTER) - {"d"}         # a plain critter facing you: body, legs, highlight, eyes and their shine


def snap(level, kinds=(), warnings=()):
    s = model.Snapshot(claude_running=level != model.UNAVAILABLE)
    if level == model.NEEDS_ME:
        s.needs_me = 1
    elif level == model.NEW:
        s.finished = 1
    elif level == model.WORKING:
        s.working = 1
    s.cards = [model.Card("t%d" % i, k, "detail") for i, k in enumerate(kinds)]
    s.warnings = list(warnings)
    return s


class Mapping(unittest.TestCase):
    def test_levels(self):
        self.assertEqual(A.avatar_state(snap(model.UNAVAILABLE)), A.AWAY)
        self.assertEqual(A.avatar_state(snap(model.IDLE)), A.IDLE)
        self.assertEqual(A.avatar_state(snap(model.IDLE, warnings=["sidebar hidden"])), A.UNREADABLE)
        self.assertEqual(A.avatar_state(model.starting()), A.UNREADABLE)
        self.assertEqual(A.avatar_state(snap(model.WORKING)), A.WORKING)
        self.assertEqual(A.avatar_state(snap(model.WORKING, warnings=["x"])), A.WORKING)

    def test_needs_me_follows_the_most_urgent_card(self):
        for kind in model.URGENT:
            self.assertEqual(A.avatar_state(snap(model.NEEDS_ME, [kind, model.FINISHED])), kind)
        self.assertEqual(A.avatar_state(snap(model.NEEDS_ME)), A.WAITING)                    # no card yet
        self.assertEqual(A.avatar_state(snap(model.NEEDS_ME, [model.FINISHED])), A.WAITING)

    def test_new(self):
        self.assertEqual(A.avatar_state(snap(model.NEW, [model.FINISHED])), A.FINISHED)
        self.assertEqual(A.avatar_state(snap(model.NEW, [model.UPDATE, model.FINISHED])), A.UPDATE)
        self.assertEqual(A.avatar_state(snap(model.NEW)), A.FINISHED)
        s = model.Snapshot()
        s.new = 1
        self.assertEqual(A.avatar_state(s), A.UPDATE)

    def test_every_kind_and_state_is_covered(self):
        self.assertEqual(set(KINDS) | {A.WORKING, A.IDLE, A.UNREADABLE}, set(A.STATES))
        for table in (A.LOOP, A.FRAME_MS):
            self.assertEqual(set(table), set(A.STATES))
        self.assertTrue(all(A.FRAME_MS[s] >= 100 for s in (A.IDLE, A.UNREADABLE)))  # calm states stay cheap

    def test_demo_cycles_through_every_state(self):
        self.assertEqual(set(A.avatar_state(demo_state(i)) for i in range(DEMO_COUNT)), set(A.STATES) | {A.AWAY})


def frame_times(state):
    n = int((A.INTRO.get(state, 0) + A.LOOP[state]) * 1000 / A.FRAME_MS[state]) + 1
    return [i * A.FRAME_MS[state] / 1000.0 for i in range(n)]


class Frames(unittest.TestCase):
    def test_frames_animate(self):
        for state in A.STATES:
            frames = set(tuple(A.frame(state, t).items) for t in frame_times(state))
            self.assertGreater(len(frames), 1, state)

    def test_mirror_is_a_reflection(self):
        f = A.frame(A.QUESTION, 0.5)
        m = A.frame(A.QUESTION, 0.5, mirror=True)
        span = lambda items: (min(i[0] for i in items), max(i[0] + i[2] for i in items))  # noqa: E731
        lo, hi = span(f.items)
        self.assertEqual(span(m.items), (A.W - hi, A.W - lo))

    def test_critter_fits_the_window_and_clears_the_badge(self):
        # Every painted cell (3D depth and shadow included) of every frame stays inside the window box, and nothing
        # slides under the badge / blue dot, at the scales people use, also when tucked into a screen edge.
        painted = {}                                    # (state, t, mirror) -> [(row, [painted cells])]
        for state in A.STATES:
            for t in frame_times(state):
                for mirror in (False, True):
                    rows, _ = A.raster(A.frame(state, t, mirror).items, shadow=True, depth=True)
                    painted[state, t, mirror] = [(j, [i for i, v in enumerate(row) if v])
                                                 for j, row in enumerate(rows) if any(row)]
        for scale in (1.0, 1.25, 1.5, 2.0):
            u = blob.unit_px(scale)
            n = blob.box_px(scale)
            bs = blob.mark_scale(scale)
            for tucked in (blob.NONE, blob.LEFT, blob.RIGHT, blob.TOP, blob.BOTTOM):
                cx, cy, size, mirror = blob.avatar_layout(scale, tucked)
                f = size / A.W
                self.assertEqual(f, u)                  # whole pixels per grid unit: crisp
                ang = math.radians(blob._DECOR[tucked][0])
                bx, by = n / 2.0 + blob.BADGE_U * u * math.cos(ang), n / 2.0 + blob.BADGE_U * u * math.sin(ang)
                for state in A.STATES:
                    # the count badge (half its height, plus the rim) when it needs you, the blue dot for news
                    badge_r = 9 * bs + 1 if state in model.URGENT else (
                        5.5 * bs + 1 if state in (A.FINISHED, A.UPDATE) else 0)
                    for t in frame_times(state):
                        where = "%s t=%.2f tucked=%s scale=%s" % (state, t, tucked, scale)
                        for j, cells in painted[state, t, mirror]:
                            y0 = cy + (j + A.GY0 - A.H / 2.0) * f
                            x0 = cx + (cells[0] + A.GX0 - A.W / 2.0) * f
                            x1 = cx + (cells[-1] + 1 + A.GX0 - A.W / 2.0) * f
                            self.assertTrue(0 <= x0 and x1 <= n and 0 <= y0 and y0 + f <= n, where)
                            if not badge_r or abs(y0 + f / 2 - by) > badge_r:
                                continue
                            for i in cells:
                                x = cx + (i + 0.5 + A.GX0 - A.W / 2.0) * f
                                self.assertGreater(math.hypot(x - bx, y0 + f / 2 - by), badge_r,
                                                   "%s: under the badge" % where)


def keys(fr):
    return set(i[4] for i in fr.items if i[5] > 0)


class Transitions(unittest.TestCase):
    def test_eases_out_then_in(self):
        an = A.Animator()
        an.set(A.IDLE, 0.0)
        an.set(A.PERMISSION, 5.0)
        an.frame(5.1)
        self.assertEqual(an.state, A.IDLE)                                     # idle settles first
        self.assertTrue(an.busy(5.1))
        self.assertEqual(an.state, A.IDLE)
        fr = an.frame(5.0 + A.EXIT_S + 0.1)                                     # then the key fades in
        self.assertEqual(an.state, A.PERMISSION)
        key = [i for i in fr.items if i[4] == "y"]
        self.assertTrue(key and all(0 < i[5] < 1 for i in key))
        fr = an.frame(5.0 + A.EXIT_S + A.ENTER_S + 0.1)
        self.assertTrue(all(i[5] == 1 for i in fr.items if i[4] == "y"))
        self.assertFalse(an.busy(5.0 + A.EXIT_S + A.ENTER_S + 0.1))

    def test_a_settled_frame_is_a_plain_standing_critter(self):
        for state in A.STATES:
            for t in (0.0, 0.7, 1.3):
                self.assertEqual(keys(A.frame(state, t, presence=0.0)) - PLAIN, set(), state)

    def test_working_packs_the_laptop_away_when_leaving(self):
        an = A.Animator()
        an.set(A.WORKING, 0.0)
        self.assertIn("m", keys(an.frame(10.0)))                               # at the laptop
        an.set(A.IDLE, 10.0)
        out = A.INTRO[A.WORKING] / A.OUTRO_SPEED
        self.assertIn("m", keys(an.frame(10.0 + out * 0.5)))                   # still putting it away
        self.assertEqual(an.state, A.WORKING)
        an.frame(10.0 + out + 0.01)
        self.assertEqual(an.state, A.IDLE)

    def test_coming_back_while_leaving_turns_around(self):
        an = A.Animator()
        an.set(A.WORKING, 0.0)
        an.set(A.IDLE, 10.0)
        an.set(A.WORKING, 10.5)
        an.frame(11.0)
        self.assertEqual((an.state, an.leave), (A.WORKING, None))
        self.assertLess(11.0 - an.start, A.INTRO[A.WORKING])                   # partway back into the intro

    def test_settles_into_standing_before_walking_off(self):
        an = A.Animator()
        an.set(A.WORKING, 0.0)
        an.set(None, 10.0)                                                      # Claude quit
        self.assertFalse(an.settled(10.5))                                      # packing the laptop away
        self.assertTrue(an.settled(10.0 + A.INTRO[A.WORKING] / A.OUTRO_SPEED + 0.01))
        self.assertEqual(keys(an.frame(20.0)), PLAIN)                           # plain standing critter
        an.set(A.IDLE, 30.0)                                                    # Claude back, critter arrived
        self.assertEqual(an.state, A.IDLE)
        self.assertTrue(an.busy(30.1))

    def test_walk_frames(self):
        left = A.walk_frame(0.0)
        right = A.walk_frame(0.0, right=True)
        self.assertNotEqual(left.items, right.items)
        self.assertNotEqual(A.walk_frame(0.0).items, A.walk_frame(0.13).items)  # legs step
        self.assertIn("d", keys(left))                                          # side-on
        self.assertEqual(keys(A.walk_frame(0.0, turning=True)) - PLAIN - {"d"}, set())

    def test_every_change_of_state_plays_through(self):
        for a in A.STATES:
            for b in A.STATES:
                if a == b:
                    continue
                an = A.Animator()
                an.set(a, 0.0)
                an.frame(3.0)
                an.set(b, 3.0)
                for k in range(80):
                    an.frame(3.0 + k * 0.05)
                self.assertEqual((an.state, an.leave), (b, None), (a, b))


class Depth(unittest.TestCase):
    def cells(self, rows, pal):
        return {(i + A.GX0, j + A.GY0): pal[v][0] for j, row in enumerate(rows) for i, v in enumerate(row) if v}

    def test_extrusion_adds_a_top_and_a_side_behind_the_front(self):
        fr = A.standing_frame()
        flat = self.cells(*A.raster(fr.items))
        solid = self.cells(*A.raster(fr.items, depth=True))
        self.assertEqual({k: v for k, v in solid.items() if k in flat}, flat)        # the front is untouched
        extra = {k: v for k, v in solid.items() if k not in flat}
        self.assertEqual(set(extra.values()), {"h^", "c^", "c_", "a_"})             # body + arms, legs' sides
        xs, ys = [x for x, _ in solid], [y for _, y in solid]
        self.assertEqual((max(xs) + 1, min(ys)), (A.W + A.DEPTH, -A.DEPTH))       # up and to the right
        self.assertEqual((min(xs), max(ys) + 1), (0, A.H))
        self.assertEqual(extra[(10, -1)], "h^")                                     # above the body: its top
        self.assertEqual(extra[(30, 3)], "c_")                                      # right of it: its side
        self.assertTrue(all(c in A.COLORS for c in solid.values()))

    def test_props_are_thinner_and_glints_flat(self):
        f = A.Frame()
        f.rect(0, 0, 4, 4, "k")
        f.rect(10, 0, 4, 4, "j")
        cells = self.cells(*A.raster(f.items, depth=True))
        self.assertEqual(sum(1 for (x, _) in cells if x >= 10), 16)                 # the glint has no depth
        self.assertEqual(max(x for (x, _) in cells if x < 10) + 1, 4 + A.PROP_DEPTH)
        self.assertEqual(min(y for (_, y) in cells), -A.PROP_DEPTH)

    def test_fading_props_keep_their_alpha_in_depth(self):
        fr = A.frame(A.PERMISSION, 1.0, presence=0.5)
        rows, pal = A.raster(fr.items, depth=True)
        used = {pal[v] for row in rows for v in row if v}
        self.assertTrue(any(k == "y^" or k == "y_" for k, a in used if 0 < a < 1) or
                        any(k.startswith("k") and k != "k" and 0 < a < 1 for k, a in used))

    def test_ground_shadow_follows_hops(self):
        self.assertEqual(A.frame(A.IDLE, 0.0).air, 0)
        self.assertGreater(A.frame(A.FINISHED, 0.3).air, 0)                          # mid-jump


class MarkFades(unittest.TestCase):
    def test_badge_fades_in_and_out(self):
        m = blob.Marks()
        m.update(snap(model.IDLE), 0.0)
        self.assertEqual(m.now["badge"], 0.0)
        s = snap(model.NEEDS_ME, [model.PERMISSION])
        s.needs_me = 3
        self.assertAlmostEqual(m.update(s, blob.MARK_S / 2)["badge"], 0.5)
        self.assertFalse(m.settled(s))
        self.assertEqual(m.update(s, blob.MARK_S)["badge"], 1.0)
        self.assertTrue(m.settled(s))
        self.assertEqual(m.text, "3")
        m.update(snap(model.WORKING), blob.MARK_S * 1.5)
        self.assertAlmostEqual(m.now["badge"], 0.5)
        self.assertEqual(m.text, "3")                       # keeps its number while it fades out

    def test_dimming_eases(self):
        m = blob.Marks()
        m.update(snap(model.UNAVAILABLE), 0.0)
        self.assertEqual(m.now["critter"], 0.6)
        self.assertAlmostEqual(m.update(snap(model.WORKING), blob.MARK_S * 0.2)["critter"], 0.8)


class Walking(unittest.TestCase):
    def test_offscreen_puts_the_critter_past_the_edge(self):
        for scale in (1.0, 1.25, 1.5, 2.0):
            box, u = blob.box_px(scale), blob.unit_px(scale)
            cw = A.W * u
            x = blob.offscreen_x(blob.RIGHT, 0, 1920, scale)
            self.assertGreaterEqual(x + (box - cw) // 2, 1920)                  # critter's left edge past the right
            x = blob.offscreen_x(blob.LEFT, 0, 1920, scale)
            self.assertLessEqual(x + (box + cw) // 2 + (A.DEPTH + 1) * u, 0)    # its depth + shadow past the left

    def test_walks_are_short(self):
        self.assertLessEqual(blob.walk_duration(0, 0, 3000, 0, 1.25), blob.WALK_MAX_S)
        self.assertGreaterEqual(blob.walk_duration(0, 0, 1, 0, 1.25), 0.3)

    def test_no_marks_while_walking(self):
        m = blob.Marks()
        m.update(snap(model.UNAVAILABLE), 0.0, here=False)
        self.assertEqual(m.now, {"critter": 1.0, "badge": 0.0, "dot": 0.0, "warn": 0.0, "usage": 0.0})


class Drawing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        G.startup()

    def test_every_state_draws(self):
        cv = G.Canvas(128, 128)
        try:
            for state in A.STATES:
                for t in frame_times(state):
                    for mirror in (False, True):
                        A.draw(cv, 64, 64, 72, A.frame(state, t, mirror), alpha=0.6)
        finally:
            cv.close()

    def test_blob_frame_is_cheap(self):
        # The whole blob frame (critter, shadows, badge / dot / mark), as the 30 fps timer draws it, plus walking.
        cv = G.Canvas(128, 128)
        try:
            worst = 0.0
            for i in range(DEMO_COUNT):
                s = demo_state(i)
                state = A.avatar_state(s)
                times = frame_times(state if state != A.AWAY else A.IDLE)
                start = time.perf_counter()
                for t in times:
                    fr = A.walk_frame(t) if state == A.AWAY else None
                    blob.paint(cv, 0, 0, 1.25, s, t, state=state, fr=fr)
                worst = max(worst, (time.perf_counter() - start) / len(times))
        finally:
            cv.close()
        self.assertLess(worst, 0.005, "%.2f ms per frame" % (worst * 1000))


if __name__ == "__main__":
    unittest.main()


class UsageMeter(unittest.TestCase):
    def test_meter_stays_in_the_window_and_clear_of_the_marks(self):
        for scale in (1.0, 1.25, 1.5, 2.0):
            u, n, bs = blob.unit_px(scale), blob.box_px(scale), blob.mark_scale(scale)
            for tucked in (blob.NONE, blob.LEFT, blob.RIGHT, blob.TOP, blob.BOTTOM):
                ax, ay, _, _ = blob.avatar_layout(scale, tucked)
                mw, mh = blob.METER_W_U * u, blob.METER_H_U * u
                x0, y0 = ax - mw / 2, ay + (A.H / 2.0 + blob.METER_GAP_U) * u
                self.assertTrue(0 <= x0 and x0 + mw <= n and y0 + mh <= n, (scale, tucked))
                for ang in blob._DECOR[tucked]:          # badge / blue dot, and the warning mark
                    mx, my = blob._at(ang, blob.BADGE_U * u, n / 2.0, n / 2.0)
                    r = 10 * bs + 1
                    nx, ny = min(max(mx, x0), x0 + mw), min(max(my, y0), y0 + mh)
                    self.assertGreater(math.hypot(mx - nx, my - ny), r, (scale, tucked, ang))

    def test_meter_colours_and_fading(self):
        self.assertEqual([blob.meter_color(p) for p in (10, 69, 70, 89, 90, 100)],
                         ["D97757", "D97757", "F59E0B", "F59E0B", "EF4444", "EF4444"])
        now = 10000.0
        self.assertEqual(blob.usage_alpha(None, now), 0.0)
        self.assertEqual(blob.usage_alpha(model.Usage(43, "Session limit", now + 60, now - 5), now), 1.0)
        self.assertLess(blob.usage_alpha(model.Usage(43, "Session limit", now + 60, now - 3600), now), 1.0)

    def test_panel_line(self):
        now = 10000.0
        u = model.Usage(43, "Session limit", now + 3660, now)
        self.assertEqual(u.line(now), "Session limit: 43% used \u00b7 resets in 1 hr 1 min")
        self.assertEqual(u.line(now + 60 * 20), "Session limit: 43% used \u00b7 resets in 41 min")
        self.assertEqual(u.line(now + 60 * 40), "Session limit: 43% used \u00b7 resets in 21 min (as of 40 min ago)")

    def test_every_meter_level_draws(self):
        s = model.Snapshot()
        cv = G.Canvas(blob.box_px(1.25), blob.box_px(1.25))
        try:
            for pct in (0, 43, 75, 95, 120):
                s.usage = model.Usage(pct, "Session limit", time.time() + 600, time.time())
                blob.paint(cv, 0, 0, 1.25, s, 0.5)
        finally:
            cv.close()
