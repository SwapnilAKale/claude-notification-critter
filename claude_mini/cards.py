"""Activity cards: a small panel under the blob with each session's latest news, in Claude's own words.

Shown only while the pointer is on the blob, the panel, or the gap between them: it never opens by itself (a new
notification changes the critter's marks and pose instead).
"""
import time
import ctypes

from . import gdiplus as G
from . import model, win32
from .wnd import Window

W = 300.0             # panel width (DIP, before ZOOM)
ZOOM = 1.15           # the whole panel, text included: readable next to Claude's own text, not too big
PAD = 8.0
HEADER = 26.0
WARN_LINE = 17.0
CARD_H = 80.0
GAP = 6.0
MAX_CARDS = 4
FOOTER = 22.0
USAGE_HEAD = 28.0     # divider and the "Plan usage" heading
USAGE_ROW = 21.0      # one limit: its name and reset time, then its %
CONTEXT_HEAD = 26.0   # the "Context" heading
CONTEXT_ROW = 21.0    # one session: its title, then how full its context is
CLOUD_STATE = {"needs": "F59E0B", "new": "60A5FA", "working": "FFFFFF"}   # a cloud session's state, by its class

TIMER_HIDE = 1
AMBER, BLUE, RED, GREY = "F59E0B", "60A5FA", "F87171", "A8A29E"


def _accent(card):
    if card.kind in (model.PROBLEM, model.BROKEN):
        return RED
    if card.kind in (model.PAUSED, model.RESTARTED):
        return GREY
    return AMBER if card.urgent else BLUE


class CardPanel(Window):
    def __init__(self, on_open):
        Window.__init__(self, win32.WS_EX_LAYERED | win32.WS_EX_TOPMOST | win32.WS_EX_TOOLWINDOW |
                        win32.WS_EX_NOACTIVATE)
        self.on_open = on_open
        self.visible = False
        self.snapshot = model.starting()
        self.blob = None
        self.scale = 1.0
        self.hover = None             # ("card", i), ("context", i) or ("cloud", i) under the pointer
        self.zones = []               # (target, x0, y0, x1, y1) in client px
        self.tracking = False
        self.hand = win32.LoadCursorW(None, ctypes.c_void_p(win32.IDC_HAND))
        self.arrow = win32.LoadCursorW(None, ctypes.c_void_p(win32.IDC_ARROW))

    # ---------- showing ----------
    def show(self, blob, snapshot):
        self.blob = blob
        self.snapshot = snapshot
        self._render(reposition=True)
        if not self.visible:
            win32.SetWindowPos(self.hwnd, win32.HWND_TOPMOST, 0, 0, 0, 0,
                               win32.SWP_NOMOVE | win32.SWP_NOSIZE | win32.SWP_NOACTIVATE | win32.SWP_SHOWWINDOW)
            self.visible = True
        win32.SetTimer(self.hwnd, TIMER_HIDE, 250, None)

    def update(self, snapshot):
        self.snapshot = snapshot
        if not self.visible:
            return
        self._render(reposition=True)

    def hide(self):
        if self.visible:
            win32.ShowWindow(self.hwnd, win32.SW_HIDE)
            win32.KillTimer(self.hwnd, TIMER_HIDE)
            self.visible = False
        self.hover = None

    def bring_to_top(self):
        if self.visible:
            win32.set_topmost(self.hwnd, True)

    def contains_cursor(self):
        if not self.visible:
            return False
        x, y = win32.cursor_pos()
        p = win32.window_rect(self.hwnd)
        if p.contains(x, y):
            return True
        # the gap between blob and panel belongs to the hover area while the panel is open
        if self.blob is not None:
            c = self.blob.circle_rect()
            if p.top >= c.bottom:
                bridge = win32.RECT(c.left, c.top + c.height // 2, c.right, p.top + 2)
            else:
                bridge = win32.RECT(c.left, p.bottom - 2, c.right, c.top + c.height // 2)
            if bridge.contains(x, y):
                return True
        return False

    # ---------- drawing ----------
    def _height(self, cards, n_warn, more, usage_rows=0, contexts=0, cloud=0):
        h = PAD + HEADER + n_warn * WARN_LINE + ((USAGE_HEAD + usage_rows * USAGE_ROW) if usage_rows else 0)
        if contexts:
            h += (CONTEXT_HEAD if usage_rows else USAGE_HEAD) + contexts * CONTEXT_ROW
        if cloud:
            h += (CONTEXT_HEAD if usage_rows or contexts else USAGE_HEAD) + cloud * CONTEXT_ROW
        if cards:
            h += len(cards) * CARD_H + (len(cards) - 1) * GAP + 4
        else:
            h += 30
        if more:
            h += FOOTER
        return h + PAD

    def _render(self, reposition=False):
        blob = self.blob
        s = self.scale = (blob.scale if blob else 1.0) * ZOOM
        snap = self.snapshot
        cards = snap.cards[:MAX_CARDS]
        more = len(snap.cards) - len(cards)
        warns = snap.warnings
        w = int(W * s)
        now = time.time()
        usage_rows = snap.usage.rows() if snap.usage is not None else []
        h = int(self._height(cards, len(warns), more, len(usage_rows), len(snap.contexts), len(snap.cloud)) * s)
        cv = G.Canvas(w, h)
        cv.clear(0)
        cv.fill_round_rect(0.5, 0.5, w - 1, h - 1, 14 * s, G.hexc("1C1B19", 242), G.argb(46, 255, 255, 255), 1)
        self.zones = []
        y = PAD * s
        x0 = PAD * s
        inner = w - 2 * PAD * s

        # header: the one-line summary
        head = snap.summary().split("\n")[0]
        cv.draw_text(head, x0 + 4 * s, y, inner - 8 * s, HEADER * s, 12 * s, G.hexc("FFFFFF"),
                     bold=True, align=0)
        y += HEADER * s
        for line in warns:
            cv.draw_text("⚠ " + line, x0 + 4 * s, y, inner - 4 * s, WARN_LINE * s, 11 * s, G.hexc("FACC15"),
                         align=0)
            y += WARN_LINE * s

        if not cards:
            msg = "Nothing needs you right now" if snap.claude_running else "Start Claude to see your sessions"
            cv.draw_text(msg, x0 + 4 * s, y, inner, 30 * s, 12 * s, G.argb(205, 255, 255, 255), align=0)
            y += 30 * s
        for i, c in enumerate(cards):
            ch = CARD_H * s
            self._card(cv, i, c, x0, y, inner, ch, s)
            self.zones.append((("card", i), x0, y, x0 + inner, y + ch))
            y += ch + GAP * s
        if more:
            cv.draw_text("+%d more: click the blob to open Claude" % more, x0 + 4 * s, y - 2 * s, inner,
                         FOOTER * s, 11 * s, G.argb(205, 255, 255, 255), align=0)
            y += FOOTER * s
        if usage_rows:
            y = self._usage(cv, snap.usage, usage_rows, x0, y + 4 * s, inner, s, now)
        if snap.contexts:
            y = self._contexts(cv, snap.contexts, x0, y + (0 if usage_rows else 4 * s), inner, s, divider=not usage_rows)
        if snap.cloud:
            above = usage_rows or snap.contexts
            y = self._cloud(cv, snap.cloud, x0, y + (0 if above else 4 * s), inner, s, divider=not above)

        if reposition:
            x, y = self._position(w, h)
            cv.present(self.hwnd, x, y)
        else:
            cv.present(self.hwnd)
        cv.close()

    def _card(self, cv, i, c, x, y, w, h, s):
        hovered = self.hover == ("card", i)
        cv.fill_round_rect(x, y, w, h, 11 * s, G.hexc("34322E" if hovered else "2A2926", 250),
                           G.argb(26, 255, 255, 255), 1)
        accent = _accent(c)
        # left accent dot
        cv.fill_ellipse(x + 11 * s, y + 13 * s, 8 * s, 8 * s, G.hexc(accent))
        tx = x + 27 * s
        tw = w - 37 * s
        cv.draw_text(c.title, tx, y + 7 * s, tw, 18 * s, 12.5 * s, G.hexc("FFFFFF"), bold=True, align=0)
        cv.draw_text(c.headline, tx, y + 25 * s, tw, 16 * s, 11.5 * s, G.hexc(accent), bold=True, align=0)
        if c.detail:
            cv.draw_text(c.detail, tx, y + 42 * s, tw, 32 * s, 11.5 * s, G.hexc("E7E5E1"), align=0, valign=0,
                         wrap=True)

    def _usage(self, cv, u, rows, x, y, w, s, now):
        """Plan usage: a heading, then per limit its name, "25%  ·  resets in 2 days" and a bar."""
        from .blob import meter_color
        cv.fill_rects([(x + 4 * s, y, w - 8 * s, max(1, int(s)))], G.argb(40, 255, 255, 255))
        cv.draw_text("Plan usage", x + 4 * s, y + 6 * s, w - 8 * s, 16 * s, 11 * s, G.argb(205, 255, 255, 255),
                     bold=True, align=0)
        age = u.age_text(now)
        if age:
            cv.draw_text(age, x + 4 * s, y + 6 * s, w - 8 * s, 16 * s, 11 * s, G.argb(205, 255, 255, 255), align=2)
        y += (USAGE_HEAD - 4) * s
        for label, pct, resets_at in rows:
            # just the numbers (the critter's own bar shows the level): name and reset time, then the %
            colour = meter_color(pct)
            cv.draw_text("%d%%" % pct, x + 4 * s, y, w - 8 * s, 16 * s, 12 * s,
                         G.hexc(colour if pct >= 70 else "FFFFFF"), bold=True, align=2)
            cv.draw_text(label, x + 4 * s, y, w - 60 * s, 16 * s, 12 * s, G.argb(240, 255, 255, 255), align=0)
            if resets_at:
                lw = cv.measure_text(label, 12 * s)[0]
                cv.draw_text("· resets in " + model.duration_text(resets_at - now), x + 4 * s + lw, y + 1 * s,
                             w - 60 * s - lw, 16 * s, 10.5 * s, G.argb(170, 255, 255, 255), align=0)
            y += USAGE_ROW * s
        return y

    def _contexts(self, cv, contexts, x, y, w, s, divider):
        """Context: per session that replied lately, its title and how full its context window is."""
        if divider:
            cv.fill_rects([(x + 4 * s, y, w - 8 * s, max(1, int(s)))], G.argb(40, 255, 255, 255))
            y += 6 * s
        else:
            y += 4 * s
        cv.draw_text("Context", x + 4 * s, y, w - 8 * s, 16 * s, 11 * s, G.argb(205, 255, 255, 255), bold=True,
                     align=0)
        y += (CONTEXT_HEAD - 6) * s
        now = time.time()
        for i, c in enumerate(contexts):
            self._line_hover(cv, ("context", i), x, y, w, s)
            # title, then "· cache 42 min" (how long its prompt cache lives on), then how full its context is
            if c.pct is not None:
                colour = "EF4444" if c.pct >= 85 else ("F59E0B" if c.pct >= 70 else "FFFFFF")
                right = "%d%%" % c.pct
            elif c.tokens is not None:
                colour, right = "FFFFFF", "%dk" % round(c.tokens / 1000.0)
            else:
                colour, right = "FFFFFF", "?"
            cv.draw_text(right, x + 4 * s, y, w - 8 * s, 16 * s, 12 * s, G.hexc(colour), bold=True, align=2)
            # How long its cache lives on; "?" when that can't be read, a warning when the timer disagreed with the
            # API's own cache reads (see usage.cache_check) instead of quietly showing a wrong time.
            cache_colour = G.argb(170, 255, 255, 255)
            if c.compacted:
                cache = "  · compacted"        # its old cache doesn't fit the new conversation
            elif c.cache_ok is False:
                cache, cache_colour = "  \u00b7 cache timer off \u26a0", G.hexc("F59E0B")
            elif c.cache_until is None:
                cache = "  \u00b7 cache ?"
            else:
                left = c.cache_until - now
                cache = "  \u00b7 cache " + ("<1 min" if left < 60 else model.duration_text(left))
            # the cache text as a column just left of the %, the title in the room that's left
            pw = 52 * s
            cw = cv.measure_text(cache.strip(), 10.5 * s)[0] + 6 * s
            cv.draw_text(cache.strip(), x + 4 * s, y + 1 * s, w - 8 * s - pw, 16 * s, 10.5 * s, cache_colour, align=2)
            cv.draw_text(c.title, x + 4 * s, y, w - 8 * s - pw - cw, 16 * s, 12 * s, G.argb(240, 255, 255, 255),
                         align=0)
            self.zones.append((("context", i), x, y - 2 * s, x + w, y + (CONTEXT_ROW - 2) * s))   # click: open it
            y += CONTEXT_ROW * s
        return y

    def _cloud(self, cv, cloud, x, y, w, s, divider):
        """Cloud: per cloud session, its title and its state in Claude's words ("Working", "Needs input", "Merged").
        Their transcripts live on Anthropic's servers, so there's no context size or cache time to show."""
        if divider:
            cv.fill_rects([(x + 4 * s, y, w - 8 * s, max(1, int(s)))], G.argb(40, 255, 255, 255))
            y += 6 * s
        else:
            y += 4 * s
        cv.draw_text("Cloud", x + 4 * s, y, w - 8 * s, 16 * s, 11 * s, G.argb(205, 255, 255, 255), bold=True,
                     align=0)
        y += (CONTEXT_HEAD - 6) * s
        for i, (title, state, cls) in enumerate(cloud):
            self._line_hover(cv, ("cloud", i), x, y, w, s)
            colour = CLOUD_STATE.get(cls)
            sw = cv.measure_text(state, 11 * s)[0] + 8 * s
            cv.draw_text(state, x + 4 * s, y + 1 * s, w - 8 * s, 16 * s, 11 * s,
                         G.hexc(colour) if colour else G.argb(170, 255, 255, 255), bold=colour is not None, align=2)
            cv.draw_text(title, x + 4 * s, y, w - 8 * s - sw, 16 * s, 12 * s, G.argb(240, 255, 255, 255), align=0)
            self.zones.append((("cloud", i), x, y - 2 * s, x + w, y + (CONTEXT_ROW - 2) * s))     # click: open it
            y += CONTEXT_ROW * s
        return y

    def _line_hover(self, cv, target, x, y, w, s):
        """Behind a Context / Cloud line under the pointer: the hovered card's shade, so you can see which one a
        click opens. Drawn within the line's own height: nothing moves."""
        if self.hover == target:
            cv.fill_round_rect(x, y - 2 * s, w, (CONTEXT_ROW - 2) * s, 6 * s, G.hexc("34322E", 250))

    def _clicked(self, target):
        """The card a click on `target` opens: a card itself, or a session's line under Context or Cloud (opened like
        a card for that session)."""
        if target is None:
            return None
        kind, i = target
        snap = self.snapshot
        if kind == "card" and 0 <= i < len(snap.cards):
            return snap.cards[i]
        if kind == "context" and 0 <= i < len(snap.contexts):
            title = snap.contexts[i].title
        elif kind == "cloud" and 0 <= i < len(snap.cloud):
            title = snap.cloud[i][0]
        else:
            return None
        return model.Card(title, model.FINISHED, "", title)

    def _position(self, w, h):
        c = self.blob.circle_rect()
        cx = (c.left + c.right) // 2
        mon = win32.monitor_info(win32.monitor_at(cx, (c.top + c.bottom) // 2)).rcWork
        gap = int(8 * self.scale)
        x = max(mon.left + gap, min(cx - w // 2, mon.right - w - gap))
        y = c.bottom + gap
        if y + h > mon.bottom:
            y = c.top - gap - h            # no room below: open upwards
        y = max(mon.top + gap, y)
        return x, y

    # ---------- input ----------
    def _zone_at(self, px, py):
        for target, x0, y0, x1, y1 in self.zones:
            if x0 <= px <= x1 and y0 <= py <= y1:
                return target
        return None

    def on_message(self, msg, wparam, lparam):
        if msg == win32.WM_MOUSEACTIVATE:
            return win32.MA_NOACTIVATE
        if msg == win32.WM_SETCURSOR:
            win32.SetCursor(self.hand if self.hover is not None else self.arrow)
            return 1
        if msg == win32.WM_MOUSEMOVE:
            if not self.tracking:
                tme = win32.TRACKMOUSEEVENT(ctypes.sizeof(win32.TRACKMOUSEEVENT), win32.TME_LEAVE, self.hwnd, 0)
                win32.TrackMouseEvent(ctypes.byref(tme))
                self.tracking = True
            target = self._zone_at(win32.signed16(lparam), win32.signed16(lparam >> 16))
            if target != self.hover:
                self.hover = target
                self._render()
            return 0
        if msg == win32.WM_MOUSELEAVE:
            self.tracking = False
            if self.hover is not None:
                self.hover = None
                self._render()
            return 0
        if msg == win32.WM_LBUTTONUP:
            card = self._clicked(self._zone_at(win32.signed16(lparam), win32.signed16(lparam >> 16)))
            if card is not None:
                self.hide()
                self.on_open(card)
            return 0
        if msg == win32.WM_TIMER and wparam == TIMER_HIDE:
            # Close once the pointer is off the blob, the panel and the gap between them.
            if not self.contains_cursor() and not (self.blob and self.blob.contains_cursor()):
                self.hide()
            return 0
        return None
