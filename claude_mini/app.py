"""Claude mini: entry point and controller."""
import ctypes
import os
import sys
import threading
import time

from . import diag, model, win32
from .watchdog import RESTART_CODE

TIMER_DEMO = 10
TIMER_ALIVE = 11
ALIVE_EVERY = 300           # s between "still running" lines in the log, so a silent end shows when it happened
NOTICE_S = 1800             # how long the panel says why Claude mini was restarted
# MB_YESNO | MB_ICONWARNING | MB_DEFBUTTON2 (No is the default) | MB_SETFOREGROUND | MB_TOPMOST
QUIT_BOX = 0x4 | 0x30 | 0x100 | 0x10000 | 0x40000
IDYES = 6
WM_SNAPSHOT = win32.WM_APP + 2


def _set_dpi_awareness():
    # Per-monitor v2 (-4). Must happen before any window is created.
    win32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    return win32.GetAwarenessFromDpiAwarenessContext(win32.GetThreadDpiAwarenessContext())


def next_session(candidates, visited):
    """Pick the session a blob click opens, cycling through everything waiting on you (A -> B -> C -> A) instead
    of bouncing between the top two: opening a session takes it off the list, which would otherwise promote the
    previous one back to the top. Returns (choice, visited)."""
    visited = [v for v in visited if v in candidates]    # forget sessions that no longer need you
    for c in candidates:
        if c not in visited:
            return c, visited + [c]
    if not candidates:
        return None, []
    return candidates[0], [candidates[0]]                # went all the way round: start again


DEMO_COUNT = 12


def demo_state(i):
    """Made-up snapshots for --demo: one per avatar state (permission, question, plan, problem, waiting, finished,
    update, working, idle, unreadable, offline, sleeping)."""
    s = model.Snapshot()
    C = model.Card
    if i == 0:
        s.needs_me, s.finished, s.working = 2, 1, 3
        s.cards = [C("Fix login flow", model.PERMISSION, 'Allow Claude to run npm test -- --watch=false?', when=3,
                     toast_id=3),
                   C("API docs", model.QUESTION, "Should the examples use the v2 client or keep v1 for now?", when=2,
                     toast_id=2),
                   C("Refactor auth", model.FINISHED, "Split the token refresh into its own module; all 48 tests pass.",
                     when=1, toast_id=1)]
    elif i == 1:
        s.needs_me, s.working = 1, 1
        s.cards = [C("API docs", model.QUESTION, "Should the examples use the v2 client or keep v1 for now?", when=2,
                     toast_id=2)]
    elif i == 2:
        s.needs_me = 1
        s.cards = [C("Billing export", model.PLAN, "Add a CSV export endpoint, then wire it to the settings page.",
                     when=4, toast_id=4)]
    elif i == 3:
        s.needs_me = 1
        s.cards = [C("Nightly sync", model.PROBLEM, "The session ended with an error: API timeout.", when=5,
                     toast_id=5)]
    elif i == 4:
        s.needs_me = 1
        s.cards = [C("Release notes", model.WAITING, "Awaiting input", "Release notes")]
    elif i == 5:
        s.finished, s.working = 1, 1
        s.cards = [C("Refactor auth", model.FINISHED, "Split the token refresh into its own module.", when=1,
                     toast_id=1)]
    elif i == 6:
        s.new = 1
        s.cards = [C("Design system", model.UPDATE, "New post in the project thread.", when=6, toast_id=6)]
    elif i == 7:
        s.working = 2
    elif i == 9:
        s.warnings.append("Claude's sidebar is hidden or the window is too narrow")
    elif i == 10:
        s.claude_running = False
    elif i == 11:
        s.asleep = True
    return s


class App(object):
    def __init__(self, demo=False, restarted=None, watched=False):
        from . import gdiplus
        from .blob import Blob
        from .cards import CardPanel
        from .tray import Tray
        from .wnd import Window

        gdiplus.startup()
        self.demo = demo
        self.quitting = False
        self.exit_code = 0                   # what run() returns once quitting (RESTART_CODE: start again)
        self.demo_i = 0
        self.snapshot = model.starting()
        self.seen_cards = None          # notification ids already logged as new cards (None until the first reading)
        self.click_round = []           # sessions blob clicks have opened this round
        # The watchdog restarted us: say why on the panel (clicking it copies the log).
        self.notice = model.Card("Claude mini", model.RESTARTED, "%s%s. Click to copy its log." % (
            restarted[:1].upper(), restarted[1:])) if restarted else None
        self.notice_until = time.time() + NOTICE_S
        self.health_told = set()        # broken sources already announced (once each, until they work again)
        app = self

        class Controller(Window):
            def on_message(self, msg, wparam, lparam):
                return app.on_message(msg, wparam, lparam)

        self.ctl = Controller(win32.WS_EX_TOOLWINDOW, title="Claude mini controller")
        self.blob = Blob(on_click=self.on_blob_click, on_hover=self.on_blob_hover,
                         on_drag_start=self.on_drag_start, on_drag_end=self.on_drag_end,
                         on_leave=self.on_blob_leave)
        self.cards = CardPanel(on_open=self.on_card_open)
        self.tray = Tray(on_open=lambda: self.open_claude(None), on_reset=self.on_reset, on_quit=self.confirm_quit,
                         on_copy_log=self.copy_log, on_hide=self.set_hidden,
                         on_restart=self.restart if watched else None)   # only the watchdog can start it again
        win32.SetTimer(self.ctl.hwnd, TIMER_ALIVE, ALIVE_EVERY * 1000, None)
        self.monitor = None
        if demo:
            self.show(demo_state(0))
            win32.SetTimer(self.ctl.hwnd, TIMER_DEMO, 6000, None)
        else:
            from .monitor import Monitor
            ctl_hwnd = self.ctl.hwnd
            self.monitor = Monitor(notify=lambda: win32.PostMessageW(ctl_hwnd, WM_SNAPSHOT, 0, 0))
            self.monitor.log = self.log
            self.monitor.start()

    def log(self, text):
        diag.log(text)

    def copy_log(self):
        ok = win32.copy_text(self.ctl.hwnd, diag.text())
        self.log("diagnostic log copied to the clipboard" if ok else "couldn't copy the diagnostic log")

    # ---------- status ----------
    def show(self, snap):
        if self.notice is not None:
            if time.time() > self.notice_until:
                self.notice = None
            elif self.notice not in snap.cards:
                snap.cards = snap.cards + [self.notice]
        self.snapshot = snap
        # Something the reading depends on broke (e.g. an update changed Claude's sidebar): say so loudly, once.
        keys = {k for k, _ in snap.health}
        broke = [(k, text) for k, text in snap.health if k not in self.health_told]
        self.health_told = (self.health_told & keys) | {k for k, _ in broke}
        for k, text in broke:
            self.tray.alert("Claude mini: Claude changed something", text)
        self.blob.apply(snap)
        self.tray.update(snap)
        # Logged as a new card only for a genuinely new Claude notification, not for a card that comes back because you
        # left a session that still needs you.
        toast_ids = set(c.toast_id for c in snap.cards if c.toast_id is not None)
        fresh = [] if self.seen_cards is None else [c for c in snap.cards
                                                    if c.toast_id is not None and c.toast_id not in self.seen_cards]
        self.seen_cards = (self.seen_cards or set()) | toast_ids
        # The panel opens only when you hover the critter (asked 2026-11-25: no panel popping up by itself when
        # something happens); a broken source still sends its Windows notification.
        self.cards.update(snap)
        names = {0: "DIMMED", 1: "NEUTRAL", 2: "WORKING", 3: "BLUE", 4: "AMBER"}
        self.log("%-7s %s" % (names[snap.level], snap.summary().replace("\n", " / ")))
        for c in fresh:
            self.log("   new card: %s | %s | %s" % (c.title, c.headline, c.detail[:80]))

    def on_message(self, msg, wparam, lparam):
        if msg == win32.WM_TIMER and wparam == TIMER_DEMO:
            self.demo_i = (self.demo_i + 1) % DEMO_COUNT
            self.show(demo_state(self.demo_i))
            return 0
        if msg == win32.WM_TIMER and wparam == TIMER_ALIVE:
            me = win32.GetCurrentProcess()
            self.log("alive: %d threads, %d GDI / %d USER objects" % (
                threading.active_count(), win32.GetGuiResources(me, 0), win32.GetGuiResources(me, 1)))
            return 0
        if msg == WM_SNAPSHOT:
            with self.monitor.lock:
                snap = self.monitor.latest
            self.show(snap)
            return 0
        return None

    # ---------- blob and cards ----------
    def on_blob_hover(self):
        self.cards.show(self.blob, self.snapshot)

    def on_blob_click(self):
        self.cards.hide()
        snap = self.snapshot
        candidates = list(snap.needs_titles)
        for c in snap.cards:                    # then finished/updated sessions
            if c.session and c.session not in candidates:
                candidates.append(c.session)
        for t in snap.open_needs:               # the one(s) you're looking at stay in the round
            if t not in candidates:
                candidates.append(t)
        target, self.click_round = next_session(candidates, self.click_round)
        self.open_claude(target)

    def on_card_open(self, card):
        if card.kind == model.BROKEN:
            self.copy_log()                     # the details for whoever fixes it
            return
        if card is self.notice:
            self.copy_log()
            self.notice = None
            return
        self.open_claude(card.session)

    def open_claude(self, session):
        """Open Claude normally (full size, in front) and, if possible, on the given session."""
        if self.demo:
            self.log("open Claude on %r (demo)" % session)
            return
        from .claude_window import bring_to_front, claude_window_state
        hwnd, state = claude_window_state()
        if state == "visible":
            bring_to_front(hwnd)                # while we still have the foreground rights from your click
        else:
            from .claude_window import launch_claude
            launch_claude()
        if session and self.monitor is not None:
            ok = self.monitor.select_session(session)
            self.log("open Claude on %r: %s" % (session, "ok" if ok else "couldn't select it"))
        else:
            self.log("open Claude")
        if self.monitor is not None:
            self.monitor.acknowledge_unmatched()

    def on_blob_leave(self):
        self.cards.hide()

    def on_drag_start(self):
        self.cards.hide()

    def on_drag_end(self):
        self.blob.bring_to_top()

    def on_reset(self):
        self.cards.hide()
        self.blob.reset_position()

    # ---------- lifetime ----------
    def set_hidden(self, hidden):
        """Hide critter / Show critter in the tray menu: the critter walks off (or back); the status reading, the tray
        icon and the log carry on."""
        self.cards.hide()
        self.blob.set_hidden(hidden)
        self.tray.critter_hidden = hidden
        self.log("critter hidden from the tray menu" if hidden else "critter shown again")

    def confirm_quit(self):
        # The log lives only in memory: quitting loses it (reported 2026-11-04: an accidental Quit wiped it).
        answer = win32.MessageBoxW(
            self.ctl.hwnd, "Quit Claude mini?\n\nIts diagnostic log is kept only in memory and will be wiped out. To "
            "keep it, choose No, then Copy diagnostic log in the tray menu first.", "Claude mini",
            QUIT_BOX)
        if answer == IDYES:
            self.quit()
        else:
            self.log("quit cancelled")

    def restart(self):
        """Restart in the tray menu: the watchdog starts the critter again at once from its files on disk, so an
        update to them takes effect. The diagnostic log carries on (the watchdog holds it)."""
        self.log("restart from the tray menu")
        self.exit_code = RESTART_CODE
        self.quit(logged=True)

    def quit(self, logged=False):
        if not logged:
            self.log("quit from the tray menu")
        self.quitting = True
        if self.monitor is not None:
            self.monitor.stop()
        self.cards.destroy()
        self.tray.destroy()
        self.blob.destroy()
        self.ctl.destroy()
        win32.PostQuitMessage(0)

    def run(self):
        """The message loop. Only Quit in the tray menu ends it (exit code 0, or RESTART_CODE for Restart); returns 2 if
        Windows keeps failing to deliver messages, so the watchdog restarts us."""
        msg = win32.MSG()
        failures = 0
        while True:
            r = win32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r == 0:
                if self.quitting:
                    return self.exit_code
                self.log("ignored a quit message sent from outside")
                continue
            if r < 0:
                failures += 1
                self.log("GetMessage failed (error %d)" % ctypes.get_last_error())
                if failures >= 20:
                    return 2
                time.sleep(0.5)
                continue
            failures = 0
            win32.TranslateMessage(ctypes.byref(msg))
            win32.DispatchMessageW(ctypes.byref(msg))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    child = "--child" in argv
    if not child and "--demo" not in argv and "--no-watchdog" not in argv:
        from . import watchdog
        return watchdog.main(os.path.abspath(sys.argv[0]), argv)
    diag.start(child=child, echo="--verbose" in argv)
    restarted = argv[argv.index("--restarted") + 1] if "--restarted" in argv[:-1] else None
    awareness = _set_dpi_awareness()
    mutex = win32.CreateMutexW(None, True, "Local\\ClaudeMiniBlob")
    if ctypes.get_last_error() == 183:      # ERROR_ALREADY_EXISTS: another blob is running
        diag.log("another Claude mini is already running")
        return 0
    app = App(demo="--demo" in argv, restarted=restarted, watched=child)
    app.log("started, pid %d%s (dpi awareness %d, blob dpi %d)" % (
        os.getpid(), ", after it stopped: " + restarted if restarted else "", awareness,
        win32.GetDpiForWindow(app.blob.hwnd)))
    try:
        return app.run()
    finally:
        win32.CloseHandle(mutex)
