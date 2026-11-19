"""The floating blob: Claude Code's pixel critter in a per-pixel-alpha, always-on-top, no-taskbar window.

There's no disc behind it any more: the critter stands on the desktop by itself, the count badge (amber = needs you),
the blue dot (something new) and the warning mark say the rest. Fully clear pixels click through, so an almost
invisible disc around the critter keeps the whole area clickable and hoverable.

It's only around while Claude is running (window open or in the tray): when Claude starts it walks in from the
screen edge to its spot, and when Claude quits it settles (packs up its laptop...) and walks off the nearest edge.
"""
import ctypes
import math
import time

from . import avatar, model, win32
from . import gdiplus as G
from .wnd import Window

UNIT = 2.6          # DIP per critter grid unit, rounded to whole pixels so the pixel art stays crisp (3 px at 125%)
BOX_U = 70          # window size (units): the critter plus room for its props, its 3D depth, the badge and the mark
HIT_U = 46          # the clickable disc around the critter (units); "the blob" for hover, cards and tucking
BADGE_U = 32        # badge / warning distance from the centre (units)
DROP_U = 3          # the critter sits this far below the centre: its props and its depth reach up, not down (units)
TUCK_U = 4          # critter nudged toward the visible half when tucked into a screen edge (units)
MARK_K = 1.25       # the badge, the blue dot and the warning mark are this much bigger than at the old critter size
MARK_S = 0.3        # the badge, the blue dot, the warning mark and the dimming fade in / out this fast (s)
METER_W_U, METER_H_U = 24, 2    # the usage meter under the critter (units)
METER_GAP_U = 4     # between the critter's feet and the meter (units), below its ground shadow
WALK_SPEED = 70.0   # DIP/s when walking on or off screen ...
WALK_MAX_S = 3.0    # ... but never for longer than this
TURN_S = 0.12       # half-turned at the start and end of a walk

AWAY, ARRIVING, HERE, SETTLING, LEAVING = "away", "arriving", "here", "settling", "leaving"

TIMER_ANIM, TIMER_HOVER, TIMER_TOPMOST = 1, 2, 3
NONE, LEFT, RIGHT, TOP, BOTTOM = "none", "left", "right", "top", "bottom"

# angles (degrees, screen coords) for the badge and warning mark, kept on the visible half when tucked; tucked into a
# side edge the props come to that half too (top), so the badge goes to its bottom corner
_DECOR = {NONE: (-45, 45), LEFT: (45, -45), RIGHT: (135, -135), TOP: (45, 135), BOTTOM: (-45, -135)}


def unit_px(scale):
    return max(1, int(round(UNIT * scale)))


def box_px(scale):
    return BOX_U * unit_px(scale)


def mark_scale(scale):
    """Size factor for the badge, the blue dot and the warning mark (they follow the critter's whole pixels)."""
    return unit_px(scale) / UNIT * MARK_K


def avatar_layout(scale, tucked=NONE):
    """Where the critter goes in the window box: (cx, cy, size_px, mirror), in box pixels."""
    u = unit_px(scale)
    c = box_px(scale) / 2.0
    tx, ty = {LEFT: (TUCK_U, 0), RIGHT: (-TUCK_U, 0), TOP: (0, TUCK_U), BOTTOM: (0, -TUCK_U)}.get(tucked, (0, 0))
    # props go on the left, except when tucked into the left edge (the left half is off screen)
    return c + tx * u, c + (ty + DROP_U) * u, avatar.W * u, tucked == LEFT


def badge_text(n):
    return "9+" if n > 9 else str(n)


def marks_for(snap):
    """What a snapshot shows around the critter, as opacities: the critter itself (dimmed or not), the count badge,
    the blue dot and the warning mark."""
    return {"critter": 0.6 if snap.dimmed else 1.0,
            "badge": 1.0 if snap.level == model.NEEDS_ME and snap.needs_me > 0 else 0.0,
            "dot": 1.0 if snap.level == model.NEW else 0.0,
            "warn": 1.0 if snap.degraded else 0.0,
            "usage": usage_alpha(snap.usage, time.time())}


def usage_alpha(usage, now):
    """The meter is solid while Claude's figure is fresh, faded once it's old (Claude isn't showing it right now)."""
    if usage is None:
        return 0.0
    return 1.0 if now - usage.seen_at < model.STALE_USAGE_S else 0.45


def meter_color(pct):
    """Claude's terracotta while there's plenty left, amber from 70%, red from 90%."""
    return "EF4444" if pct >= 90 else ("F59E0B" if pct >= 70 else "D97757")


class Marks(object):
    """Eases the marks toward what the latest snapshot shows, so nothing pops in or out. Pure: pass it the clock."""

    def __init__(self):
        self.now = None
        self.text = ""          # the badge's text, kept while it fades out
        self.t = 0.0

    def update(self, snap, t, here=True):
        """here=False (walking on / off screen): no marks, no dimming."""
        target = marks_for(snap) if here else {"critter": 1.0, "badge": 0.0, "dot": 0.0, "warn": 0.0, "usage": 0.0}
        if target["badge"] and here:
            self.text = badge_text(snap.needs_me)
        if self.now is None:
            self.now = dict(target)
        else:
            step = max(0.0, t - self.t) / MARK_S
            for k, v in target.items():
                cur = self.now[k]
                self.now[k] = min(v, cur + step) if v > cur else max(v, cur - step)
        self.t = t
        return self.now

    def settled(self, snap, here=True):
        return self.now == (marks_for(snap) if here else
                            {"critter": 1.0, "badge": 0.0, "dot": 0.0, "warn": 0.0, "usage": 0.0})


def paint(cv, x0, y0, scale, snap, t, tucked=NONE, state=None, anim=None, marks=None, text=None, fr=None):
    """Draw one frame of the blob into cv with its box's top-left at (x0, y0). t: seconds on the blob's clock.
    The critter is `fr` (an avatar.Frame) if given, else from `anim` (an avatar.Animator), else `state` (or the
    snapshot's) at t; the marks' opacities from `marks` (see Marks) if given, else straight from the snapshot."""
    if marks is None:
        marks, text = marks_for(snap), badge_text(snap.needs_me)
    u = unit_px(scale)
    s = mark_scale(scale)
    c = box_px(scale) / 2.0
    cx, cy = x0 + c, y0 + c

    hr = HIT_U / 2.0 * u            # alpha 1: invisible, but not click-through
    cv.fill_ellipse(cx - hr, cy - hr, 2 * hr, 2 * hr, G.argb(1, 0, 0, 0))

    ax, ay, size, mirror = avatar_layout(scale, tucked)
    ax, ay = x0 + ax, y0 + ay
    fade = marks["critter"]
    if fr is None:
        fr = anim.frame(t, mirror) if anim else avatar.frame(state or avatar.avatar_state(snap), t, mirror)
    # soft ground shadow under its 3D footprint (the feet, and the depth behind them); smaller and lighter mid-hop
    k = max(0.55, 1.0 - fr.air / 12.0)
    d = avatar.DEPTH
    gw, gh = (30 + d) * k * u, (5 + d) * k * u
    gx, gy = ax + d / 2.0 * u, ay + (avatar.H / 2.0 - d / 2.0) * u
    cv.fill_ellipse_radial(gx - gw / 2, gy - gh / 2, gw, gh, G.argb(int(85 * fade * k), 0, 0, 0), G.argb(0, 0, 0, 0))
    avatar.draw(cv, ax, ay, size, fr, alpha=fade)

    a = marks.get("usage", 0.0)
    if a > 0 and snap.usage is not None:
        # a slim meter under its feet, like a health bar: how much of the plan's limit is used
        mw, mh = METER_W_U * u, METER_H_U * u
        mx, my = ax - mw / 2, ay + (avatar.H / 2.0 + METER_GAP_U) * u
        cv.fill_round_rect(mx, my, mw, mh, mh / 2, G.argb(int(150 * a), 28, 27, 25), G.argb(int(210 * a), 255, 255, 255),
                           max(1.0, 0.5 * u))
        fw = mw * min(100, max(0, snap.usage.pct)) / 100.0
        if fw > 0:
            cv.fill_round_rect(mx, my, max(mh, fw), mh, mh / 2, G.hexc(meter_color(snap.usage.pct), int(255 * a)))

    b_ang, w_ang = _DECOR[tucked]
    bx, by = _at(b_ang, BADGE_U * u, cx, cy)
    a = marks["dot"]
    if a > 0:
        d = 11 * s                          # Claude's own "unread" blue dot
        cv.fill_ellipse(bx - d / 2, by - d / 2, d, d, G.hexc("3B82F6", int(255 * a)))
        cv.draw_ellipse(bx - d / 2, by - d / 2, d, d, G.hexc("FFFFFF", int(255 * a)), 1.5 * s)
    a = marks["badge"]
    if a > 0 and text:
        bw = max(18.0, 10 + 7 * len(text)) * s
        bh = 18 * s
        pulse = 0.5 + 0.5 * math.sin(t * 2 * math.pi / 2.2)
        gr = bh * (0.9 + 0.25 * pulse)      # gentle amber pulse around the badge
        cv.fill_ellipse_radial(bx - gr - (bw - bh) / 2, by - gr, 2 * gr + bw - bh, 2 * gr,
                               G.argb(int((70 + 90 * pulse) * a), 245, 158, 11), G.argb(0, 245, 158, 11))
        cv.fill_round_rect(bx - bw / 2, by - bh / 2, bw, bh, bh / 2, G.hexc("F59E0B", int(255 * a)),
                           G.hexc("FFFFFF", int(255 * a)), 1.5 * s)
        cv.draw_text(text, bx - bw / 2, by - bh / 2 + 0.5 * s, bw, bh, 10.5 * s, G.hexc("1C1B19", int(255 * a)),
                     bold=True)

    a = marks["warn"]
    if a > 0:
        wx, wy = _at(w_ang, BADGE_U * u, cx, cy)
        pts = [(wx, wy - 8 * s), (wx + 9 * s, wy + 8 * s), (wx - 9 * s, wy + 8 * s)]
        cv.fill_polygon(pts, G.hexc("FACC15", int(255 * a)), G.hexc("1C1B19", int(255 * a)), 1.2 * s)
        cv.draw_text("!", wx - 6 * s, wy - 5 * s, 12 * s, 14 * s, 10.5 * s, G.hexc("1C1B19", int(255 * a)),
                     bold=True)


def offscreen_x(side, mon_left, mon_right, scale):
    """Window x that puts the critter (its 3D depth and its shadows too) just past the monitor's left or right edge."""
    box, cw, u = box_px(scale), avatar.W * unit_px(scale), unit_px(scale)
    if side == RIGHT:
        return mon_right - (box - cw) // 2
    return mon_left - (box + cw) // 2 - (avatar.DEPTH + 1) * u


def walk_duration(x0, y0, x1, y1, scale):
    return max(0.3, min(WALK_MAX_S, math.hypot(x1 - x0, y1 - y0) / (WALK_SPEED * scale)))


def _at(angle_deg, dist_px, cx, cy):
    ang = math.radians(angle_deg)
    return cx + dist_px * math.cos(ang), cy + dist_px * math.sin(ang)


class Blob(Window):
    def __init__(self, on_click=None, on_hover=None, on_drag_start=None, on_drag_end=None, on_leave=None):
        Window.__init__(self, win32.WS_EX_LAYERED | win32.WS_EX_TOPMOST | win32.WS_EX_TOOLWINDOW |
                        win32.WS_EX_NOACTIVATE)
        self.on_click, self.on_hover = on_click, on_hover
        self.on_drag_start, self.on_drag_end = on_drag_start, on_drag_end
        self.on_leave = on_leave                # called when it starts going away (Claude quit)
        self.snapshot = model.starting()
        self.tucked = NONE
        self.pointer_down = False
        self.dragging = False
        self.down_cursor = (0, 0)
        self.down_rect = None
        self.tracking_leave = False
        self.hovering = False
        self.hover_blocked = False      # after a click, hovering only re-opens the cards once you leave and come back
        self.anim = avatar.Animator()
        self.marks = Marks()
        self.presence = AWAY                    # hidden until a snapshot says Claude is running
        self.hidden = False                     # put away from the tray menu: off screen even while Claude runs
        self.spot = None                        # (x, y, tucked) where it stood when put away, to come back to
        self.come_back_tucked = NONE
        self.walk = None                        # (x0, y0, x1, y1, start, duration) while walking
        self.anim_ms = 0
        self.t0 = time.time()
        self.scale = win32.GetDpiForWindow(self.hwnd) / 96.0
        self.hand = win32.LoadCursorW(None, ctypes.c_void_p(win32.IDC_HAND))
        self.place_initially()
        win32.SetTimer(self.hwnd, TIMER_TOPMOST, 1500, None)

    # ---------- geometry (physical pixels) ----------
    @property
    def box_px(self):
        return box_px(self.scale)

    def rect(self):
        return win32.window_rect(self.hwnd)

    def circle_rect(self):
        r = self.rect()
        inset = (BOX_U - HIT_U) // 2 * unit_px(self.scale)
        return win32.RECT(r.left + inset, r.top + inset, r.right - inset, r.bottom - inset)

    def contains_cursor(self):
        x, y = win32.cursor_pos()
        c = self.circle_rect()
        cx, cy, rad = (c.left + c.right) / 2.0, (c.top + c.bottom) / 2.0, c.width / 2.0 + 4
        return (x - cx) ** 2 + (y - cy) ** 2 <= rad ** 2

    def move_to(self, x, y):
        win32.SetWindowPos(self.hwnd, win32.HWND_TOPMOST, int(x), int(y), 0, 0,
                           win32.SWP_NOSIZE | win32.SWP_NOACTIVATE)

    def bring_to_top(self):
        if not self.dragging and self.presence != AWAY:
            win32.set_topmost(self.hwnd, True)

    def home(self):
        """The default spot (window top-left): right side of the main screen, a bit below the middle."""
        mi = win32.monitor_info(win32.monitor_at(0, 0))
        return mi.rcWork.right - self.box_px - int(16 * self.scale), mi.rcWork.top + int(mi.rcWork.height * 0.62)

    def place_initially(self):
        self.move_to(*self.home())

    def reset_position(self):
        if self.presence != HERE:
            return
        self.tucked = NONE
        self._render()
        self.place_initially()

    @property
    def here(self):
        """On screen and settled in (not away, not walking)."""
        return self.presence == HERE

    def ensure_on_screen(self):
        if self.presence == AWAY:
            return
        c = self.circle_rect()
        cx, cy = (c.left + c.right) // 2, (c.top + c.bottom) // 2
        if not win32.monitor_at(cx, cy, nearest=False):
            self.reset_position()

    # ---------- state ----------
    def apply(self, snapshot):
        self.snapshot = snapshot
        self._update_presence()
        self._update_animation()
        if self.presence != AWAY:
            self._render()

    def _now(self):
        return time.time() - self.t0

    # ---------- coming and going with Claude ----------
    def set_hidden(self, hidden):
        """Put the critter away (it walks off) or bring it back to where it stood. Everything else keeps running."""
        if hidden == self.hidden:
            return
        if hidden and self.presence == HERE:
            r = self.rect()
            self.spot = (r.left, r.top, self.tucked)
        self.hidden = hidden
        self.apply(self.snapshot)

    def _update_presence(self):
        running = self.snapshot.claude_running and not self.hidden
        if running:
            if self.presence == AWAY:
                self._walk_in(from_edge=True)
            elif self.presence == LEAVING:
                self._walk_in(from_edge=False)          # Claude is back: turn round and come back
            elif self.presence == SETTLING:
                self.presence = HERE
        elif self.presence in (HERE, SETTLING):
            if self.presence == HERE and self.on_leave:
                self.on_leave()
            self.presence = SETTLING                    # finish what it's doing, then walk off
        elif self.presence == ARRIVING:
            self._walk_out()

    def _walk_in(self, from_edge):
        hx, hy, self.come_back_tucked = self.spot or self.home() + (NONE,)
        self.spot = None
        now = self._now()
        if from_edge:
            mon = win32.monitor_info(win32.monitor_at(hx + self.box_px // 2, hy + self.box_px // 2)).rcMonitor
            mid = hx + self.box_px // 2
            side = RIGHT if mon.right - mid <= mid - mon.left else LEFT
            x0, y0 = offscreen_x(side, mon.left, mon.right, self.scale), hy
            self.move_to(x0, y0)
            win32.ShowWindow(self.hwnd, win32.SW_SHOWNA)
        else:
            r = self.rect()
            x0, y0 = r.left, r.top
        self.tucked = NONE
        self.walk = (x0, y0, hx, hy, now, walk_duration(x0, y0, hx, hy, self.scale))
        self.presence = ARRIVING

    def _walk_out(self):
        r = self.rect()
        cx, cy = r.left + self.box_px // 2, r.top + self.box_px // 2
        mon = win32.monitor_info(win32.monitor_at(cx, cy)).rcMonitor
        side = RIGHT if mon.right - cx <= cx - mon.left else LEFT
        x1 = offscreen_x(side, mon.left, mon.right, self.scale)
        self.tucked = NONE
        self.walk = (r.left, r.top, x1, r.top, self._now(), walk_duration(r.left, r.top, x1, r.top, self.scale))
        self.presence = LEAVING

    def _walk_pos(self, now):
        x0, y0, x1, y1, start, dur = self.walk
        k = min(1.0, (now - start) / dur)
        return int(round(x0 + (x1 - x0) * k)), int(round(y0 + (y1 - y0) * k)), now - start >= dur

    def _step(self):
        """Advance arriving / settling / leaving; called every animation tick."""
        now = self._now()
        if self.presence == SETTLING and self.anim.settled(now):
            self._walk_out()
        if self.presence in (ARRIVING, LEAVING) and self._walk_pos(now)[2]:
            x, y, _ = self._walk_pos(now)
            self.move_to(x, y)
            self.walk = None
            if self.presence == ARRIVING:
                self.presence = HERE
                if self.come_back_tucked != NONE:      # put away while tucked into an edge: tucked again
                    self.tucked = self.come_back_tucked
                    self._render()
                self._update_animation()                # its current animation starts (eases in)
            else:
                self.presence = AWAY
                win32.ShowWindow(self.hwnd, win32.SW_HIDE)

    def _update_animation(self):
        state = avatar.avatar_state(self.snapshot)
        self.anim.set(state if self.presence == HERE and state != avatar.AWAY else None, self._now())
        self._retime()

    def _retime(self):
        # Every state animates; walking, changes between states and the badge's pulse get the full frame rate.
        now = self._now()
        if self.presence == AWAY:
            ms = 0
        elif self.presence != HERE:
            ms = avatar.TRANSITION_MS
        elif self.snapshot.level == model.NEEDS_ME:
            ms = 33
        elif self.anim.busy(now) or not self.marks.settled(self.snapshot) or self.anim.state is None:
            ms = avatar.TRANSITION_MS
        else:
            ms = avatar.FRAME_MS[self.anim.state]
        if ms != self.anim_ms:
            if ms:
                win32.SetTimer(self.hwnd, TIMER_ANIM, ms, None)
            else:
                win32.KillTimer(self.hwnd, TIMER_ANIM)
            self.anim_ms = ms

    # ---------- drawing ----------
    def _render(self):
        n = self.box_px
        cv = G.Canvas(n, n)
        cv.clear(0)
        now = self._now()
        here = self.presence in (HERE, SETTLING)
        marks = self.marks.update(self.snapshot, now, here=self.presence == HERE)
        fr, x, y = None, None, None
        if not here and self.walk is not None:
            x, y, _ = self._walk_pos(now)
            x0, _, x1, _, start, dur = self.walk
            t = now - start
            fr = avatar.walk_frame(t, right=x1 > x0, turning=t < TURN_S or t > dur - TURN_S)
        paint(cv, 0, 0, self.scale, self.snapshot, now, self.tucked, anim=self.anim, marks=marks,
              text=self.marks.text, fr=fr)
        cv.present(self.hwnd, x, y)                     # moves and redraws in one go while walking
        cv.close()

    # ---------- input ----------
    def on_message(self, msg, wparam, lparam):
        if msg == win32.WM_MOUSEACTIVATE:
            return win32.MA_NOACTIVATE
        if msg == win32.WM_SETCURSOR:
            win32.SetCursor(self.hand)
            return 1
        if msg == win32.WM_LBUTTONDOWN:
            if self.presence != HERE:
                return 0                                # walking on / off screen: not now
            self.pointer_down = True
            self.dragging = False
            self.down_cursor = win32.cursor_pos()
            self.down_rect = self.rect()
            win32.SetCapture(self.hwnd)
            return 0
        if msg == win32.WM_MOUSEMOVE:
            self._on_move()
            return 0
        if msg == win32.WM_LBUTTONUP:
            if self.pointer_down:
                self._end_pointer(released=True)
            return 0
        if msg == win32.WM_CAPTURECHANGED:
            if self.pointer_down and win32.hwnd_value(lparam) != self.hwnd:
                self._end_pointer(released=False)
            return 0
        if msg == win32.WM_MOUSELEAVE:
            self.tracking_leave = False
            self.hovering = False
            self.hover_blocked = False
            win32.KillTimer(self.hwnd, TIMER_HOVER)
            return 0
        if msg == win32.WM_TIMER:
            if wparam == TIMER_ANIM:
                self._step()
                if self.presence != AWAY:
                    self._render()
                self._retime()
            elif wparam == TIMER_HOVER:
                win32.KillTimer(self.hwnd, TIMER_HOVER)
                if self.hovering and not self.dragging and self.on_hover:
                    self.on_hover()
            elif wparam == TIMER_TOPMOST:
                self.bring_to_top()
            return 0
        if msg == win32.WM_DPICHANGED:
            self._on_dpi(win32.loword(wparam))
            return 0
        if msg == win32.WM_DISPLAYCHANGE:
            self.ensure_on_screen()
            return None
        return None

    def _on_move(self):
        if self.presence != HERE:
            return
        if not self.tracking_leave:
            tme = win32.TRACKMOUSEEVENT(ctypes.sizeof(win32.TRACKMOUSEEVENT), win32.TME_LEAVE, self.hwnd, 0)
            win32.TrackMouseEvent(ctypes.byref(tme))
            self.tracking_leave = True
        if not self.pointer_down:
            if not self.hovering and not self.hover_blocked:
                self.hovering = True
                win32.SetTimer(self.hwnd, TIMER_HOVER, 300, None)
            return
        x, y = win32.cursor_pos()
        dx, dy = x - self.down_cursor[0], y - self.down_cursor[1]
        if not self.dragging:
            slop = int(math.ceil(5 * self.scale))
            if abs(dx) < slop and abs(dy) < slop:
                return
            self.dragging = True
            win32.KillTimer(self.hwnd, TIMER_HOVER)
            if self.tucked != NONE:
                self.tucked = NONE
                self._render()
            if self.on_drag_start:
                self.on_drag_start()
        self.move_to(self.down_rect.left + dx, self.down_rect.top + dy)

    def _end_pointer(self, released):
        was_dragging = self.dragging
        self.pointer_down = False
        self.dragging = False
        self.hovering = False           # so hovering can open the cards again after a drag
        if win32.hwnd_value(win32.GetCapture()) == self.hwnd:
            win32.ReleaseCapture()
        if was_dragging:
            self._settle_after_drag()
            if self.on_drag_end:
                self.on_drag_end()
        elif released and self.on_click:
            self.hover_blocked = True
            win32.KillTimer(self.hwnd, TIMER_HOVER)
            self.on_click()

    # The blob only tucks if YOU dragged it past a screen edge; anywhere else it stays exactly where dropped.
    def _settle_after_drag(self):
        c = self.circle_rect()
        cx, cy = (c.left + c.right) // 2, (c.top + c.bottom) // 2
        cur = win32.cursor_pos()
        mon = win32.monitor_info(win32.monitor_at(cur[0], cur[1])).rcMonitor
        best, edge = 0, NONE
        for over, px, py, e in ((mon.left - c.left, mon.left - 1, cy, LEFT),
                                (c.right - mon.right, mon.right, cy, RIGHT),
                                (mon.top - c.top, cx, mon.top - 1, TOP),
                                (c.bottom - mon.bottom, cx, mon.bottom, BOTTOM)):
            if over <= 0 or over <= best:
                continue
            if win32.monitor_at(px, py, nearest=False):
                continue        # another monitor continues past this edge: not a screen edge
            best, edge = over, e
        if edge == NONE:
            return
        rad = c.width // 2
        w = self.rect()
        nx, ny = cx, cy
        if edge in (LEFT, RIGHT):
            nx = mon.left if edge == LEFT else mon.right
            ny = max(mon.top + rad, min(cy, mon.bottom - rad))
        else:
            ny = mon.top if edge == TOP else mon.bottom
            nx = max(mon.left + rad, min(cx, mon.right - rad))
        self.tucked = edge
        self._render()
        self.move_to(nx - w.width // 2, ny - w.height // 2)

    def _on_dpi(self, dpi):
        c = self.circle_rect()
        cx, cy = (c.left + c.right) // 2, (c.top + c.bottom) // 2
        self.scale = dpi / 96.0
        self._render()
        b = self.box_px
        self.move_to(cx - b // 2, cy - b // 2)
