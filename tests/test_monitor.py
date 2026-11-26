"""Scenario tests for the status rules, with fake sidebar/toast sources and a controllable clock.

Run:  py -3.14 -B -m unittest discover -s tests
"""
import os
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claude_mini import model, monitor as M          # noqa: E402
from claude_mini.notifications import ClaudeToast    # noqa: E402
from claude_mini.uia import Reading, Row, parse_caption      # noqa: E402


class Clock(object):
    def __init__(self):
        self.t = 1000.0

    def time(self):
        return self.t


class FakeUia(object):
    def __init__(self, world):
        self.world = world

    def read(self, hwnd):
        r = Reading()
        r.window_found = True
        if self.world.sidebar_visible:
            r.mode_found = True
            r.code_selected = True
            r.sidebar_found = True
            # (title, status) or (title, status, caption): a cloud row's "Cloud · Working"
            r.rows = [Row(x[1], x[0], False, False, *(parse_caption(x[2]) if len(x) > 2 else (None, None)))
                      for x in self.world.rows]
        elif self.world.chat_tab:
            r.mode_found = not self.world.no_mode
            r.code_selected = False if r.mode_found else None
            r.aggregate = self.world.aggregate if r.mode_found else ""
            r.sidebar_found = True
            r.rows = [Row(s, t, False, beside=True) for t, s in self.world.chat_rows]
            for t, texts in self.world.plain_rows:      # Claude 5.11+ layout: texts after the title, as uia reads them
                row = Row(texts[0] if texts else "Idle", t, False)
                row.texts = tuple(texts)
                r.rows.append(row)
        r.open_titles = set(self.world.open)
        r.usage = self.world.usage
        return r

    def open_titles_now(self, hwnd):
        return set(self.world.open)

    def select_session(self, hwnd, matches):
        hit = next((t for t, _ in self.world.rows + self.world.chat_rows + self.world.chats if matches(t)), None)
        self.world.shown.append(hit)
        return hit is not None

    def permission_options(self, hwnd, title):
        self.world.shown.append(title)
        return list(self.world.perm_buttons.get(title, [])) or None

    def permission_prompt(self, hwnd, title, timeout=2.5):
        self.world.shown.append(title)
        b = self.world.perm_buttons.get(title)
        return (list(b), "Allow Claude to run %s?" % title) if b else None

    def press_permission(self, hwnd, title, label):
        self.world.shown.append(title)
        self.world.pressed.append((title, label))
        return label in self.world.perm_buttons.get(title, [])


class FakeToasts(object):
    def __init__(self, world):
        self.world = world

    def access_status(self):
        return 1 if self.world.access else 2

    def read(self):
        return dict(self.world.toasts)


class FakeTranscripts(object):
    def __init__(self, world):
        self.world = world
        self.ok = True
        self.records_ok = True

    def local_titles(self):
        return set(self.world.local)

    def last_activity(self):
        return self.world.last_activity

    def turn_ends_after(self, t):
        return sorted(at for at in self.world.turn_ends if at > t)

    def poll(self, now=None):
        return self.world.hooked

    def contexts(self, now=None):
        return list(self.world.contexts)

    def activity(self):
        return list(self.world.activity)

    def limit_stops(self):
        return dict(self.world.limit_stops)

    def working(self, now=None):
        return set(self.world.working)


class FakeHistory(object):
    def __init__(self, world):
        self.world = world

    def poll(self, now=None):
        return self.world.sample


class World(object):
    def __init__(self):
        self.running = True
        self.hidden = False       # Claude's window closed to the tray (still running): nothing on screen to read
        self.sidebar_visible = True
        self.chat_tab = False
        self.aggregate = ""
        self.rows = []            # (title, status)
        self.usage = ""           # Claude's usage ring label, if a Code session is open
        self.hooked = None        # newest model.Usage from the get_usage Stop hook's records
        self.contexts = []        # model.Context of sessions that replied lately
        self.activity = []        # (session title, time of its last request) from the transcripts
        self.limit_stops = {}     # session title -> its last request, refused at the usage limit
        self.sample = None        # Claude's own newest usage sample: (when, {"5-hour": pct, "weekly": pct})
        self.working = set()      # titles of sessions whose transcript shows a turn under way
        self.local = set()        # titles of the local sessions the transcripts speak for
        self.last_activity = None  # when any session last made a request
        self.turn_ends = []       # when main-thread turns ended (Stop hooks ran)
        self.chat_rows = []       # (title, status) what the chat tab lists with a status: chats, project sessions
        self.chats = []           # (title, None) plain chats in the chat tab's Recents
        self.plain_rows = []      # (title, texts) chat-tab rows in Claude 5.11+'s layout: status/subtitle texts alike
        self.no_mode = False      # the Claude | Code switch isn't found in this reading
        self.exe = None           # the Claude.exe owning the window (its bundle holds the error titles)
        self.open = []
        self.toasts = {}          # id -> ClaudeToast
        self.access = True
        self.in_view = True        # you can see Claude's window (it's in front, or mostly uncovered)
        self.in_use = False       # Claude is the foreground window
        self.perm_buttons = {}    # session -> buttons its permission prompt shows
        self.shown = []           # sessions the monitor switched Claude to
        self.pressed = []
        self.next_id = 1

    def toast(self, title, body):
        t = ClaudeToast(self.next_id, title, body, float(self.next_id))    # later toasts are newer
        self.toasts[self.next_id] = t
        self.next_id += 1
        return t.id


class MonitorScenarios(unittest.TestCase):
    def setUp(self):
        self.world = w = World()
        self.clock = Clock()
        self._saved = (M.claude_window_state, M.UiaReader, M.NotificationReader, M.time.time, M.claude_in_view,
                       M.TranscriptUsage, M.PlanHistory, M.claude_exe, M._ERROR_TITLE)
        M.claude_exe = lambda hwnd: w.exe
        M.TranscriptUsage = lambda: FakeTranscripts(w)
        M.PlanHistory = lambda: FakeHistory(w)
        M.claude_window_state = lambda: (1234, "hidden" if w.hidden else "visible") if w.running else (0, "not running")
        M.claude_in_view = lambda hwnd: w.in_view
        M.UiaReader = lambda: FakeUia(w)
        M.NotificationReader = lambda: FakeToasts(w)
        M.time.time = self.clock.time
        self.m = M.Monitor(notify=lambda: None)

    def tearDown(self):
        (M.claude_window_state, M.UiaReader, M.NotificationReader, M.time.time, M.claude_in_view,
         M.TranscriptUsage, M.PlanHistory, M.claude_exe, M._ERROR_TITLE) = self._saved

    def poll(self, seconds=1.0):
        self.clock.t += seconds
        self.m._publish(self.m._poll())      # same path as the real loop, including the warning debounce
        return self.m.latest

    def test_open_sessions_left_out_like_claudes_own_pill(self):
        w = self.world
        w.rows = [("This chat", "Awaiting input"), ("Plan", "Awaiting input"), ("Question", "Awaiting input"),
                  ("Other pane", "Awaiting input"), ("Done one", "Unread response"), ("Busy", "Running")]
        w.open = ["This chat", "Other pane"]
        s = self.poll()
        self.assertEqual((s.needs_me, s.finished, s.working), (2, 1, 1))
        self.assertEqual(s.level, model.NEEDS_ME)
        self.assertEqual(s.summary(), "2 need you · 1 finished · 1 working")

    def test_open_session_drops_out_and_comes_back_when_you_leave_it(self):
        w = self.world
        w.rows = [("Perm", "Running"), ("Other", "Idle")]
        self.poll()                                    # baseline
        w.rows = [("Perm", "Awaiting input"), ("Other", "Idle")]
        w.toast("Claude-mini · Perm", "Allow Claude to run npm test?")
        self.assertEqual(self.poll().needs_me, 1)
        w.open = ["Perm"]                              # you clicked its card: Claude shows it
        s = self.poll()
        self.assertEqual((s.needs_me, s.cards), (0, []))
        w.open = ["Other"]                             # you went elsewhere without answering
        s = self.poll()
        self.assertEqual(s.needs_me, 1)
        self.assertEqual(s.cards[0].kind, model.PERMISSION)

    def test_open_session_counts_when_claude_is_out_of_view(self):
        w = self.world
        w.rows = [("This chat", "Awaiting input")]
        w.open = ["This chat"]
        self.assertEqual(self.poll().needs_me, 0)      # you're looking at it
        w.in_view = False                              # Chrome in front, Claude minimized or in the tray
        self.assertEqual(self.poll().needs_me, 1)

    def test_open_session_auto_continuing_behind_another_app_shows_working(self):
        # The reported case: usage limit resets, auto-continue restarts the session that's open in Claude while
        # you're in Chrome. The blob must show it working, not stand still.
        w = self.world
        w.rows = [("This chat", "Idle")]
        w.open = ["This chat"]
        w.in_view = False
        self.assertEqual(self.poll().level, model.IDLE)
        w.rows = [("This chat", "Running")]
        s = self.poll()
        self.assertEqual((s.level, s.working), (model.WORKING, 1))
        w.in_view = True                               # you switch back to Claude and watch it
        self.assertEqual(self.poll().working, 0)

    def test_unreadable_sidebar_keeps_last_reading_not_all_quiet(self):
        w = self.world
        w.rows = [("Plan", "Awaiting input"), ("Question", "Awaiting input"), ("Done", "Unread response")]
        self.poll()
        w.sidebar_visible = False
        s = self.poll()
        self.assertEqual((s.needs_me, s.finished), (2, 1))
        self.assertEqual(s.warnings, [])               # brief blips aren't warnings
        s = self.poll(5)
        self.assertEqual((s.needs_me, s.finished), (2, 1))
        self.assertEqual(len(s.warnings), 1)
        self.assertIn("last reading", s.warnings[0])
        self.assertNotEqual(s.summary().split("\n")[0], "All quiet")

    def test_no_warning_while_panel_open_or_just_after_folding(self):
        w = self.world
        w.rows = [("Plan", "Awaiting input")]
        self.poll()
        self.m.set_expanded(True)
        w.sidebar_visible = False                      # narrow panel collapses the sidebar
        for _ in range(10):
            s = self.poll()
            self.assertEqual(s.warnings, [])
            self.assertEqual(s.needs_me, 1)
        self.m.set_expanded(False)
        s = self.poll()
        self.assertEqual(s.warnings, [])
        w.sidebar_visible = True
        s = self.poll()
        self.assertEqual(s.warnings, [])

    def test_opening_the_panel_keeps_toasts_for_known_sessions(self):
        w = self.world
        w.rows = [("Plan", "Running")]
        self.poll()
        w.sidebar_visible = False
        w.toast("Claude-mini · Plan", "Allow Claude to ExitPlanMode Reply hello?")
        s = self.poll()
        self.assertEqual(s.needs_me, 1)                # stale row says Running, toast says it needs you
        self.m._acknowledge()                          # you clicked the blob
        s = self.poll()
        self.assertEqual(s.needs_me, 1)                # still there until the sidebar shows it handled

    def test_toast_cleared_only_when_the_row_clears(self):
        w = self.world
        w.rows = [("Question", "Running")]
        self.poll()
        w.toast("Question", "Should I go with 1 and 2?")
        s = self.poll()
        self.assertEqual(s.needs_me, 1)                # toast counts even before the row catches up
        s = self.poll()
        self.assertEqual(s.needs_me, 1)                # within the grace period: not cleared by a lagging row
        w.rows = [("Question", "Awaiting input")]
        s = self.poll(5)
        self.assertEqual(s.needs_me, 1)                # one session, not double counted
        w.rows = [("Question", "Running")]
        s = self.poll()
        self.assertEqual(s.needs_me, 0)

    def test_project_thread_toast_without_a_sidebar_row(self):
        w = self.world
        w.rows = [("Some session", "Idle")]
        self.poll()
        w.toast("Website redesign", "Claude ran into a problem")
        w.toast("Website redesign", "Claude posted an update")
        w.toast("Launch plan", "Claude posted an update")
        s = self.poll()
        self.assertEqual((s.needs_me, s.new), (1, 1))  # one per thread; error wins over news
        self.m._acknowledge()                          # you opened Claude
        s = self.poll()
        self.assertEqual((s.needs_me, s.new), (0, 0))

    def test_a_toast_with_a_shortened_title_finds_its_row(self):
        # Reported 2026-10-31: a permission card stayed after it was accepted. Claude had cut the long session title
        # in its notification short with "…", so the toast never found its sidebar row.
        w = self.world
        w.rows = [("Local Voice setup and agent connection (fork)", "Running"), ("Other", "Idle")]
        self.poll()
        w.toast("SwapnilAKale/Confab · Local Voice setup and agent connection (…", "Allow Claude to run git add?")
        w.rows[0] = ("Local Voice setup and agent connection (fork)", "Awaiting input")
        s = self.poll()
        self.assertEqual([c.title for c in s.cards], ["Local Voice setup and agent connection (fork)"])
        w.rows[0] = ("Local Voice setup and agent connection (fork)", "Running")      # accepted
        self.assertEqual(self.poll().needs_me, 0)

    def test_a_request_after_the_toast_means_it_was_answered(self):
        w = self.world
        w.rows = [("Some session", "Idle")]
        self.poll()
        w.activity = [("Local Voice setup", 0.5)]
        nid = w.toast("SwapnilAKale/Confab · Local Voice setup", "Allow Claude to run git add?")
        self.assertEqual(self.poll().needs_me, 1)      # no sidebar row to clear it
        w.activity = [("Local Voice setup", w.toasts[nid].created + 1)]   # the request that asked
        self.assertEqual(self.poll().needs_me, 1)
        w.activity = [("Local Voice setup", w.toasts[nid].created + 30)]  # carried on: answered
        self.assertEqual(self.poll().needs_me, 0)

    def test_a_session_refused_at_the_limit_isnt_working(self):
        # Reported 2026-11-04: the weekly limit was hit and every turn stopped, yet a row still said "Running" and
        # the critter kept typing at its laptop.
        w = self.world
        now = M.time.time()
        w.hooked = model.Usage(100, "Weekly · all models", now + 5400, now)
        w.rows = [("BRIEF.md implementation end-to-end", "Running"), ("Other", "Idle")]
        w.limit_stops = {"BRIEF.md implementation end-to-end": {
            "at": now - 60, "text": "You've hit your weekly limit · resets 4:30pm", "resets_at": now + 5400}}
        s = self.poll()
        self.assertEqual((s.level, s.working, s.paused), (model.IDLE, 0, 1))
        w.limit_stops = {}                                 # after the reset a request got through: working again
        w.hooked = model.Usage(1, "Weekly · all models", now + 7 * 86400, now + 5500)
        s = self.poll(5500)
        self.assertEqual((s.level, s.working, s.paused), (model.WORKING, 1, 0))

    def test_on_the_chat_tab_code_sessions_arent_in_front_of_you(self):
        # Reported 2026-11-04: on the chat tab the critter didn't react to a Code session it took for "open".
        w = self.world
        w.rows = [("Perm", "Running"), ("Other", "Idle")]
        w.open = ["Perm"]
        self.poll()
        w.sidebar_visible, w.chat_tab, w.aggregate = False, True, "awaiting your input"
        w.toast("Claude-mini · Perm", "Allow Claude to run npm test?")
        s = self.poll()
        self.assertEqual((s.needs_me, s.open_needs), (1, []))   # its pane is still there, but not on screen

    def test_cloud_sessions_count_by_the_state_in_their_caption(self):
        # Reported 2026-11-03: cloud sessions were ignored. Their status icon can stay empty while the row's caption
        # shows their state ("Cloud · Working").
        w = self.world
        w.rows = [("Deploy to Oracle", "Idle", "Cloud \u00b7 Working"), ("Visitor analytics", "Idle", "Cloud \u00b7 Merged")]
        s = self.poll()
        self.assertEqual((s.level, s.working), (model.WORKING, 1))
        self.assertEqual(s.cloud, [("Deploy to Oracle", "Working", M.WORKING)])     # merged before we looked: old
        w.rows = [("Deploy to Oracle", "Idle", "Cloud \u00b7 Needs input"), ("Visitor analytics", "Idle", "Cloud \u00b7 Merged")]
        s = self.poll()
        self.assertEqual((s.needs_me, [(c.title, c.kind) for c in s.cards]), (1, [("Deploy to Oracle", model.WAITING)]))
        w.rows = [("Deploy to Oracle", "Idle", "Cloud \u00b7 Ready for review"), ("Visitor analytics", "Idle", "Cloud \u00b7 Merged")]
        s = self.poll()
        self.assertEqual((s.needs_me, s.finished), (0, 1))
        w.rows = [("Deploy to Oracle", "Idle", "Cloud \u00b7 Open"), ("Visitor analytics", "Idle", "Cloud \u00b7 Merged")]
        s = self.poll()
        self.assertEqual((s.level, s.cloud[0][1:]), (model.IDLE, ("Open", M.IDLE)))     # a PR state: nothing to do

    def test_a_cloud_session_leaves_the_list_an_hour_after_its_last_activity(self):
        # Reported 2026-11-03: cloud sessions from hours and days ago stayed on the list.
        w = self.world
        w.rows = [("Deploy to Oracle", "Idle", "Cloud · Working")]
        self.poll()
        w.rows = [("Deploy to Oracle", "Idle", "Cloud · Merged")]                  # it finished: a change
        self.assertEqual(self.poll().cloud, [("Deploy to Oracle", "Merged", M.IDLE)])
        self.assertEqual(len(self.poll(M.CLOUD_SHOWN_S - 10).cloud), 1)
        self.assertEqual(self.poll(20).cloud, [])                                         # an hour with nothing new
        w.rows = [("Deploy to Oracle", "Idle", "Cloud · Needs input")]              # you sent it a message
        self.assertEqual(self.poll().cloud, [("Deploy to Oracle", "Needs input", M.NEEDS)])
        self.assertEqual(len(self.poll(3 * M.CLOUD_SHOWN_S).cloud), 1)                    # still waiting on you

    def test_a_redrawn_row_isnt_cloud_activity(self):
        # Reported 2026-11-11: merged cloud sessions from a week ago stayed listed. Claude briefly drew a row as plain
        # "Cloud" while redrawing; each Idle <-> Merged flicker restarted the hour.
        w = self.world
        w.rows = [("Docker Compose deployment on Oracle Cloud", "Idle", "Cloud · Merged")]
        self.assertEqual(self.poll().cloud, [])
        w.rows = [("Docker Compose deployment on Oracle Cloud", "Idle", "Cloud")]
        self.assertEqual(self.poll().cloud, [])
        w.rows = [("Docker Compose deployment on Oracle Cloud", "Idle", "Cloud · Merged")]
        self.assertEqual(self.poll().cloud, [])

    def test_a_cloud_rows_status_icon_still_wins(self):
        w = self.world
        w.rows = [("Deploy to Oracle", "Awaiting input", "Cloud \u00b7 Merged"), ("Odd", "Idle", "Cloud \u00b7 Something new")]
        s = self.poll()
        self.assertEqual(s.needs_me, 1)                # the icon's "Awaiting input", not the caption's PR state
        self.assertEqual(s.cloud, [("Deploy to Oracle", "Awaiting input", M.NEEDS)])
        self.assertEqual(s.warnings, [])               # a caption state it doesn't know isn't taken for a problem

    def test_the_critter_dozes_once_every_cache_is_gone(self):
        # Asked 2026-11-25: when nothing is going on and every prompt cache has expired, the critter sleeps.
        w = self.world
        w.rows = [("Fix login", "Idle")]
        w.contexts = [model.Context("Fix login", 20, 200000, 1000000, None, 0.0, M.time.time() + 600, None)]
        self.assertFalse(self.poll().asleep)           # a cache is still warm
        w.contexts = []
        self.assertTrue(self.poll().asleep)            # all gone: asleep
        w.rows = [("Fix login", "Running")]
        self.assertFalse(self.poll().asleep)           # something works: awake
        w.rows = [("Fix login", "Idle")]
        w.toast("Claude-mini · Fix login", "Allow Claude to run npm test?")
        self.assertFalse(self.poll().asleep)           # something needs you: awake

    def test_finished_or_new_wakes_it(self):
        w = self.world
        w.rows = [("Fix login", "Idle")]
        self.assertTrue(self.poll().asleep)
        w.rows = [("Fix login", "Unread response")]   # a session finished
        s = self.poll()
        self.assertEqual((s.finished, s.asleep), (1, False))
        w.rows = [("Fix login", "Idle")]
        self.assertTrue(self.poll().asleep)
        w.toast("Website redesign", "Claude posted an update")      # news (no sidebar row)
        s = self.poll()
        self.assertEqual((s.new, s.asleep), (1, False))

    def test_a_session_open_on_screen_that_waits_or_works_keeps_it_awake(self):
        # Found by the checker 2026-11-25: open sessions aren't counted, so one on screen waiting for your permission
        # (or still working past its cache's lifetime) didn't keep the critter awake.
        w = self.world
        w.rows = [("Fix login", "Awaiting input")]
        w.open = ["Fix login"]
        s = self.poll()
        self.assertEqual((s.needs_me, s.open_needs, s.asleep), (0, ["Fix login"], False))
        w.rows = [("Fix login", "Running")]           # on screen, working
        self.assertFalse(self.poll().asleep)
        w.rows = [("Fix login", "Idle")]
        w.working = {"Fix login"}                      # its transcript shows a turn under way
        self.assertFalse(self.poll().asleep)
        w.working = set()
        self.assertTrue(self.poll().asleep)

    def test_a_turn_under_way_keeps_it_awake_whatever_its_row_says(self):
        # Found by the checker 2026-11-25: nothing guarded the transcripts' own "a turn is under way". An open session
        # whose row says "Unread response" isn't counted (you're looking at it) and its row isn't rewritten to working;
        # only its transcript says it has started again.
        w = self.world
        w.rows = [("Fix login", "Unread response")]
        w.open = ["Fix login"]
        self.assertTrue(self.poll().asleep)
        w.working = {"Fix login"}
        self.assertFalse(self.poll().asleep)

    def test_no_sleep_when_reading_the_transcripts_fails(self):
        w = self.world
        w.rows = [("Fix login", "Idle")]
        self.assertTrue(self.poll().asleep)

        def broken(now=None):
            raise OSError("the transcripts folder can't be read")
        self.m.transcripts.poll = broken
        self.assertFalse(self.poll().asleep)

    def test_no_sleep_when_the_transcripts_cant_be_read(self):
        # An empty Context list is evidence that every cache expired only if the transcripts were read.
        w = self.world
        w.rows = [("Fix login", "Idle")]
        self.assertTrue(self.poll().asleep)
        self.m.transcripts.ok = False
        self.assertFalse(self.poll().asleep)

    def test_toast_dismissed_in_action_center(self):
        w = self.world
        self.poll()
        nid = w.toast("Launch plan", "Claude posted an update")
        self.assertEqual(self.poll().new, 1)
        del w.toasts[nid]
        self.assertEqual(self.poll().new, 0)

    def test_history_toasts_ignored(self):
        w = self.world
        w.toast("Old", "Allow Claude to run ls?")
        s = self.poll()
        self.assertEqual(s.needs_me, 0)

    def test_chat_tab_uses_codes_aggregate(self):
        w = self.world
        w.sidebar_visible = False
        w.chat_tab = True
        w.aggregate = "awaiting your input"
        s = self.poll()
        self.assertEqual(s.needs_me, 1)
        s = self.poll(5)
        self.assertEqual(s.warnings, [])               # the chat tab is a normal place to be, not a problem

    def test_chat_tab_shows_project_sessions_with_their_status(self):
        w = self.world
        w.rows = [("Voice plugin", "Idle"), ("Parakeet layer", "Running")]
        self.poll()                                    # seen on the Code tab
        w.sidebar_visible = False
        w.chat_tab = True
        w.chat_rows = [("Voice plugin", "Running"), ("Parakeet layer", "Unread response")]
        w.aggregate = "working"
        s = self.poll(5)
        self.assertEqual((s.finished, s.working, s.warnings), (1, 1, []))
        w.chat_rows = [("Voice plugin", "Awaiting input"), ("Parakeet layer", "Idle")]
        s = self.poll()
        self.assertEqual((s.level, s.needs_me, s.finished), (model.NEEDS_ME, 1, 0))
        self.assertEqual(s.needs_titles, ["Voice plugin"])

    def test_usage_follows_claudes_ring_and_goes_at_the_reset(self):
        w = self.world
        w.usage = "Usage: 43% of session limit, Resets in 1 hr 1 min"
        s = self.poll()
        self.assertEqual((s.usage.pct, s.usage.limit), (43, "Session limit"))
        w.usage = "Usage: 71% of session limit, Resets in 40 min"
        self.assertEqual(self.poll(60).usage.pct, 71)
        w.usage = ""                                   # no Code session open: keep the last figure ...
        self.assertEqual(self.poll(60).usage.pct, 71)
        self.assertEqual(self.poll(40 * 60).usage.pct, 0)   # ... until it resets: empty again
        w.usage = "Usage: Context 40%"                 # the ring showing the context window instead
        self.assertEqual(self.poll().usage.pct, 0)

    def test_a_reset_given_as_a_clock_time_is_not_minutes_away(self):
        # Reported 2026-11-04: "Resets Wed 4:30 PM" was read as 4 min 30 s; five minutes later the weekly limit
        # showed 0% (taken as reset), until Claude's ring was opened again.
        w = self.world
        now = M.time.time()
        w.hooked = model.Usage(83, "Weekly · all models", now + 11 * 3600, now - 60, [("5-hour limit", 44, now + 4380)])
        self.poll()
        w.usage = "Usage: Weekly · all models: 84%, Resets Wed 4:30 PM"
        self.poll()
        w.usage = "Usage: Weekly · all models: 85%, Resets Wed 4:30 PM"    # changed: Claude fetched it anew
        s = self.poll()
        weekly = dict((l, (p, r)) for l, p, r in s.usage.rows())["Weekly · all models"]
        self.assertEqual(weekly[0], 85)
        self.assertGreater(weekly[1] - M.time.time(), 3600)               # not "resets in 4 min"
        self.assertEqual(dict((l, p) for l, p, _ in self.poll(600).usage.rows())["Weekly · all models"], 85)

    def test_a_higher_figure_on_the_ring_is_newer_even_unchanged(self):
        # Reported 2026-11-04: Claude's ring said 98% while the blob kept an earlier 84%: the ring hadn't changed
        # since the blob started, so it wasn't taken for new.
        w = self.world
        now = M.time.time()
        reset = now + 4 * 3600 + 24 * 60
        w.hooked = model.Usage(84, "5-hour limit", reset, now - 300, [("Weekly · all models", 12, now + 6 * 86400)])
        w.usage = "Usage: 98% of 5-hour limit, Resets in 4 hr 24 min"
        s = self.poll()
        self.assertEqual(dict((l, p) for l, p, _ in s.usage.rows())["5-hour limit"], 98)
        w.hooked = model.Usage(0, "5-hour limit", now + 5 * 3600, now + 30)       # a new window after a reset ...
        w.usage = "Usage: 98% of 5-hour limit, Resets in 4 hr 24 min"          # ... and the ring still says the old one
        self.assertEqual(self.poll(60).usage.pct, 0)

    def test_claudes_own_samples_keep_the_figures_current(self):
        # Reported 2026-11-04: no reply had finished for a while, so the 5-hour figure stayed at 70% while Claude
        # was at 91%. Claude samples both figures every ~15 minutes on its own.
        w = self.world
        now = M.time.time()
        w.hooked = model.Usage(70, "5-hour limit", now + 1260, now - 600, [("Weekly · all models", 97, now + 7200)])
        s = self.poll()
        self.assertEqual(s.usage.pct, 70)
        w.sample = (now - 30, {"5-hour": 91, "weekly": 100})
        s = self.poll()
        got = dict((l, (p, r)) for l, p, r in s.usage.rows())
        self.assertEqual((got["5-hour limit"], got["Weekly · all models"]), ((91, now + 1260), (100, now + 7200)))
        w.hooked = model.Usage(93, "5-hour limit", now + 1260, now + 10, [("Weekly · all models", 100, now + 7200)])
        self.assertEqual(dict((l, p) for l, p, _ in self.poll().usage.rows())["5-hour limit"], 93)   # a newer reply wins
        w.sample = (now - 20, {"5-hour": 95, "weekly": 100})                   # but not an older sample
        self.assertEqual(dict((l, p) for l, p, _ in self.poll().usage.rows())["5-hour limit"], 93)

    def test_a_sample_after_the_reset_starts_the_limit_again(self):
        w = self.world
        now = M.time.time()
        w.hooked = model.Usage(100, "Weekly · all models", now + 60, now - 60, [("5-hour limit", 0, None)])
        self.poll()
        w.sample = (now + 120, {"5-hour": 0, "weekly": 0})
        s = self.poll(150)
        got = dict((l, (p, r)) for l, p, r in s.usage.rows())
        self.assertEqual(got["Weekly · all models"], (0, now + 60 + 7 * 86400))    # same time, a week on
        self.assertEqual(got["5-hour limit"][1], None)                             # starts with your next use

    def test_after_a_reset_the_ring_brings_the_new_window(self):
        # Reported 2026-11-04 (23:2x): after the 9:30 PM reset Claude's sample said 2% with no reset time, and the
        # ring's fresher 4% ("Resets Thu 4:10 AM") was left out for want of a reset time to compare.
        w = self.world
        now = M.time.time()
        w.hooked = model.Usage(100, "5-hour limit", now + 60, now - 60, [("Weekly · all models", 14, now + 6 * 86400)])
        w.usage = "Usage: 100% of 5-hour limit, Resets in 1 min"            # the ring, before the reset
        self.poll()
        w.sample = (now + 120, {"5-hour": 2, "weekly": 15})
        s = self.poll(150)
        self.assertEqual(dict((l, (p, r)) for l, p, r in s.usage.rows())["5-hour limit"], (2, None))
        self.assertEqual(self.poll(60).usage.rows()[0][1], 2)                 # the old 100% on the ring isn't news
        w.usage = "Usage: 4% of 5-hour limit, Resets in 4 hr 50 min"         # you opened it: the new window
        row = self.poll().usage.rows()[0]
        self.assertEqual((row[0], row[1], round((row[2] - M.time.time()) / 60)), ("5-hour limit", 4, 290))

    def test_every_limit_empties_at_its_own_reset(self):
        # Reported 2026-10-25: weekly 27% was the fullest, so the 5-hour limit's reset was never noticed and the
        # panel kept "5-hour limit 22% · resets in 1 min" after Claude itself showed 0%.
        w = self.world
        now = M.time.time()
        w.hooked = model.Usage(27, "Weekly · all models", now + 2 * 86400, now,
                               [("5-hour limit", 22, now + 60)])
        s = self.poll()
        self.assertEqual(dict((l, p) for l, p, _ in s.usage.rows()), {"5-hour limit": 22, "Weekly · all models": 27})
        s = self.poll(61)
        self.assertEqual(dict((l, p) for l, p, _ in s.usage.rows()), {"5-hour limit": 0, "Weekly · all models": 27})
        self.assertNotIn("resets in 1 min", s.usage.line(M.time.time()))
        w.hooked = model.Usage(30, "Weekly · all models", now + 2 * 86400, M.time.time(),
                               [("5-hour limit", 1, M.time.time() + 5 * 3600)])     # the next reply's reading
        self.assertEqual(dict((l, p) for l, p, _ in self.poll().usage.rows())["5-hour limit"], 1)

    def test_the_hooks_reading_wins_over_claudes_stale_ring(self):
        w = self.world
        w.usage = "Usage: 43% of session limit, Resets in 1 hr 1 min"          # the ring, not refreshed
        w.hooked = model.Usage(63, "5-hour limit", M.time.time() + 3600, M.time.time(),
                               [("Weekly \u00b7 all models", 25, M.time.time() + 86400)])
        s = self.poll()
        self.assertEqual((s.usage.pct, s.usage.limit), (63, "5-hour limit"))
        self.assertIn("Weekly \u00b7 all models 25%", s.usage.line(M.time.time()))

    def test_news_for_a_session_you_have_open_clears_once_you_see_it(self):
        # Reported 2026-10-26: a session open in a pane finished while Claude wasn't the window in use; its row never
        # showed "Unread" (you're looking at it), so the "Finished" card stayed even after you looked.
        w = self.world
        w.rows = [("New driver session handoff", "Running")]
        w.open = ["New driver session handoff"]
        w.in_view = False                                  # you're in Chrome
        self.poll()
        w.toast("New driver session handoff", "stopped all jobs; 6 apps collected, 3 skipped")
        w.rows = [("New driver session handoff", "Idle")]
        self.assertEqual(self.poll().finished, 1)
        w.in_view = True                                   # back in Claude, looking at it
        self.assertEqual(self.poll().finished, 0)
        self.poll(1)
        self.poll(3)
        w.in_view = False                                  # and away again
        self.assertEqual(self.poll().finished, 0)

    def test_a_glance_is_not_reading_it(self):
        w = self.world
        w.rows = [("Done", "Running")]
        w.open = ["Done"]
        w.in_view = False
        self.poll()
        w.toast("Done", "All tests pass.")
        w.rows = [("Done", "Idle")]
        self.poll()
        w.in_view = True
        self.poll(1)                                       # just a second
        w.in_view = False
        self.assertEqual(self.poll().finished, 1)

    def test_claudes_ring_counts_when_you_open_it(self):
        # Reported 2026-10-26: Claude's card said 100% while the blob still showed an old 6%.
        w = self.world
        now = M.time.time()
        w.hooked = model.Usage(30, "Weekly \u00b7 all models", now + 86400, now - 6000,
                               [("5-hour limit", 6, now + 7900)])
        w.usage = "Usage: 6% of session limit, Resets in 2 hr 12 min"
        s = self.poll()
        self.assertEqual(s.usage.pct, 30)
        w.usage = "Usage: 6% of session limit, Resets in 2 hr 11 min"     # only its countdown ticked
        self.assertEqual(self.poll(60).usage.seen_at, now - 6000)
        w.usage = "Usage: 100% of session limit, Resets in 2 hr 10 min"   # you opened it: Claude fetched it anew
        s = self.poll(60)
        self.assertEqual((s.usage.pct, s.usage.limit), (100, "5-hour limit"))
        self.assertEqual(dict((l, p) for l, p, _ in s.usage.rows())["Weekly \u00b7 all models"], 30)
        self.assertEqual(s.usage.age_text(M.time.time()), "")

    def test_a_session_stopped_at_the_limit_is_paused_not_a_problem(self):
        # Reported 2026-10-27: the session hit the 5-hour limit (Claude: "Session limit reached, auto-resuming at
        # 7:01 PM") and its row read "Something went wrong"; with another app on top the blob kept raising an alarm.
        w = self.world
        now = M.time.time()
        w.hooked = model.Usage(100, "5-hour limit", now + 3 * 3600, now, [("Weekly · all models", 78, now + 86400)])
        w.rows = [("BRIEF.md implementation end-to-end", "Something went wrong"), ("Other", "Idle")]
        w.open = ["BRIEF.md implementation end-to-end"]
        w.in_view = False                                  # another app on top
        s = self.poll(5)
        self.assertEqual((s.level, s.needs_me, s.paused, s.open_needs), (model.IDLE, 0, 1, []))
        self.assertEqual([(c.kind, c.detail) for c in s.cards],
                         [(model.PAUSED, "Stopped at the usage limit · resets in 2 hr 59 min")])
        self.assertEqual(s.summary(), "1 paused at the limit")
        w.in_view = True
        self.assertEqual(self.poll().level, model.IDLE)    # no flip-flopping as Claude comes and goes
        # after the reset: Claude resumes it by itself ...
        w.rows = [("BRIEF.md implementation end-to-end", "Running"), ("Other", "Idle")]
        w.in_view = False
        s = self.poll(3 * 3600 + 60)
        self.assertEqual((s.paused, s.working), (0, 1))
        # ... and an error once the limit has room again is a real problem
        w.rows = [("BRIEF.md implementation end-to-end", "Something went wrong"), ("Other", "Idle")]
        s = self.poll()
        self.assertEqual((s.needs_me, s.paused, s.cards[0].kind), (1, 0, model.PROBLEM))

    def test_a_session_still_stopped_after_the_reset_stays_paused(self):
        # Reported 2026-11-03: the session hit the 5-hour limit at 00:14, the limit reset at 1:10, Claude never carried
        # it on, and from then on its "Something went wrong" showed as a problem.
        w = self.world
        now = M.time.time()
        w.hooked = model.Usage(71, "Weekly · all models", now + 86400, now, [("5-hour limit", 0, None)])
        w.rows = [("BRIEF.md implementation end-to-end", "Something went wrong"), ("Other", "Idle")]
        w.limit_stops = {"BRIEF.md implementation end-to-end": {
            "at": now - 3600, "text": "You've hit your session limit · resets 1:10am", "resets_at": now - 600}}
        s = self.poll()
        self.assertEqual((s.level, s.needs_me, s.paused), (model.IDLE, 0, 1))
        self.assertEqual(s.cards[0].detail,
                         "Stopped at the usage limit, which has reset since. Send it a message to carry on")
        w.limit_stops = {}                                 # you sent a message and it got through ...
        s = self.poll()                                    # ... yet it still errors: a real problem
        self.assertEqual((s.needs_me, s.paused, s.cards[0].kind), (1, 0, model.PROBLEM))

    def test_a_refused_request_pauses_before_the_hook_reports_the_full_limit(self):
        w = self.world
        now = M.time.time()
        w.hooked = model.Usage(96, "5-hour limit", now + 3600, now)     # last reading before the limit filled
        w.rows = [("Busy one", "Something went wrong")]
        w.limit_stops = {"Busy one": {"at": now, "text": "You've hit your session limit", "resets_at": now + 1801}}    # the poll moves the clock 1 s
        s = self.poll()
        self.assertEqual([(c.kind, c.detail) for c in s.cards],
                         [(model.PAUSED, "Stopped at the usage limit · resets in 30 min")])

    def test_the_waiting_notification_of_a_session_at_the_limit_is_paused_too(self):
        # Reported 2026-10-28: at the limit Claude posted "Claude is waiting for your input" for the stopped session.
        w = self.world
        now = M.time.time()
        w.rows = [("BRIEF.md implementation end-to-end", "Running")]
        self.poll()
        w.hooked = model.Usage(100, "5-hour limit", now + 2 * 3600, now, [("Weekly · all models", 91, now + 18 * 3600)])
        w.toast("UI-Detector-v2 · BRIEF.md implementation end-to-end", "Claude is waiting for your input")
        w.rows = [("BRIEF.md implementation end-to-end", "Awaiting input")]
        s = self.poll(5)
        self.assertEqual((s.level, s.needs_me, s.paused), (model.IDLE, 0, 1))
        self.assertEqual(s.cards[0].kind, model.PAUSED)

    def test_a_real_request_at_the_limit_still_needs_you(self):
        w = self.world
        now = M.time.time()
        w.rows = [("Fix login", "Running")]
        self.poll()
        w.hooked = model.Usage(100, "5-hour limit", now + 2 * 3600, now)
        w.toast("Fix login", "Allow Claude to run npm test?")
        w.rows = [("Fix login", "Awaiting input")]
        s = self.poll(5)
        self.assertEqual((s.needs_me, s.paused, s.cards[0].kind), (1, 0, model.PERMISSION))

    def test_chats_are_left_out(self):
        # Claude only learns a chat has replied when it next checks, and marks nothing when it does: chats running
        # on the chat tab never make the critter work, and nothing lights up for them.
        w = self.world
        w.rows = [("Some session", "Idle")]
        self.poll()
        w.sidebar_visible = False
        w.chat_tab = True
        w.open = ["Other chat"]
        w.chat_rows = [("Weather today", "Running")]
        s = self.poll(5)
        self.assertEqual((s.level, s.cards), (model.IDLE, []))
        w.chat_rows = []
        self.assertEqual(self.poll(60).level, model.IDLE)

    def test_a_reading_without_the_mode_switch_doesnt_make_chats_code_sessions(self):
        # Reported 2026-11-26: one reading on the chat tab without the Claude | Code switch took its chats for Code
        # sessions; from then on the chat tab kept them, with their subtitles as "Ran into a problem".
        w = self.world
        w.rows = [("Some session", "Idle")]
        self.poll()
        w.sidebar_visible, w.chat_tab = False, True
        w.plain_rows = [("X account growth analysis", ("Running",)), ("Morning AI digest + follow list", ("Daily",))]
        w.no_mode = True                               # the switch is being redrawn
        s = self.poll()
        self.assertEqual((s.level, s.working, s.cards), (model.IDLE, 0, []))
        w.no_mode = False
        w.plain_rows = [("X account growth analysis", ("Includes an artifact",)),
                        ("Morning AI digest + follow list", ("Daily",))]
        s = self.poll()
        self.assertEqual((s.level, s.needs_me, s.cards), (model.IDLE, 0, []))

    def test_without_the_switch_a_session_with_a_transcript_still_counts(self):
        # Chats have no Claude Code transcript, so a row whose title the transcripts speak for is a Code session even
        # if it was never seen on the Code tab (the switch missing from the start, e.g. after an update).
        w = self.world
        w.sidebar_visible, w.chat_tab, w.no_mode = False, True, True
        w.local = {"New code"}
        w.plain_rows = [("New code", ("Awaiting input",)), ("Some chat", ("Awaiting input",))]
        s = self.poll()
        self.assertEqual((s.needs_me, [c.title for c in s.cards]), (1, ["New code"]))

    def test_error_titles_from_claudes_own_files(self):
        # A title a Claude update adds is read from its installed bundle (once per Claude.exe), so it counts as a problem
        # rather than a subtitle; when the bundle can't be read the built-in titles still count.
        import shutil
        import tempfile
        if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))    # run on its own, not via discover
        from test_claude_bundle import make_asar, table
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        os.makedirs(os.path.join(d, "app", "resources"))
        make_asar(os.path.join(d, "app", "resources", "ion-dist.asar"),
                  {"assets/v1/e.js": b"x=" + table(["Connection lost", "Gremlins ate {thing}"] +
                                                   ["T%d" % i for i in range(60)])})
        logs = []
        self.m.log = logs.append
        w = self.world
        w.rows = [("Deploy", "Idle"), ("Docs", "Idle")]
        self.poll()
        w.sidebar_visible, w.chat_tab = False, True
        w.plain_rows = [("Deploy", ("Gremlins ate my session",)), ("Docs", ("Session was interrupted",))]

        def cards(s):
            return sorted((c.title, c.kind) for c in s.cards)
        # First a Claude whose bundle can't be read: the built-in titles still count, a title only in the bundle isn't
        # known yet (a subtitle).
        w.exe = os.path.join(d, "gone", "Claude.exe")
        s = self.poll()
        self.assertTrue(any(l.startswith("can't read Claude's error titles") for l in logs))
        self.assertEqual(cards(s), [("Docs", model.PROBLEM)])
        self.assertIn("Gremlins ate my session", self.m.not_statuses)
        # An update: a new Claude.exe whose bundle can be read. Its new title counts from then on.
        w.exe = os.path.join(d, "app", "Claude.exe")
        s = self.poll()
        self.assertEqual(cards(s), [("Deploy", model.PROBLEM), ("Docs", model.PROBLEM)])
        self.assertIn("Claude's error titles: 62 in its files, 61 of them not built in", logs)
        logs[:] = []
        self.poll()
        self.assertFalse([l for l in logs if "error titles" in l])          # read once per Claude.exe
        # Another update with no readable bundle: what is known stays known, the built-in titles included.
        w.exe = os.path.join(d, "gone2", "Claude.exe")
        s = self.poll()
        self.assertTrue(any(l.startswith("can't read Claude's error titles") for l in logs))
        self.assertEqual(cards(s), [("Deploy", model.PROBLEM), ("Docs", model.PROBLEM)])

    def test_a_subtitle_isnt_a_problem(self):
        # Claude 5.11+ shows a status and a subtitle alike, as plain text after the title: a known session listed with
        # "Daily" (a scheduled task) or "Includes an artifact" isn't in trouble; one of Claude's error titles is.
        w = self.world
        w.rows = [("Morning AI digest + follow list", "Idle"), ("Deploy", "Idle")]
        self.poll()
        w.sidebar_visible, w.chat_tab = False, True
        w.plain_rows = [("Morning AI digest + follow list", ("Daily",)), ("Deploy", ("Includes an artifact",))]
        s = self.poll()
        self.assertEqual((s.level, s.needs_me, s.cards), (model.IDLE, 0, []))
        self.assertEqual(self.m.not_statuses, {"Daily", "Includes an artifact"})
        w.plain_rows = [("Morning AI digest + follow list", ("Daily",)), ("Deploy", ("Couldn’t connect to devbox",))]
        s = self.poll()
        self.assertEqual((s.needs_me, [(c.title, c.kind) for c in s.cards]), (1, [("Deploy", model.PROBLEM)]))

    def test_chat_tab_remembers_code_sessions_it_doesnt_list(self):
        w = self.world
        w.rows = [("Voice plugin", "Running"), ("Claude mini", "Running"), ("Old one", "Unread response")]
        self.poll()
        w.sidebar_visible = False                      # you switch to the chat tab
        w.chat_tab = True
        w.chat_rows = [("Voice plugin", "Running")]
        w.aggregate = "working"                        # nothing in Code is unread or waiting any more
        s = self.poll(5)
        self.assertEqual((s.working, s.finished, s.warnings), (2, 0, []))
        w.chat_rows = [("Voice plugin", "Idle")]
        w.local = {"Voice plugin", "Claude mini", "Old one"}     # local sessions: their transcripts speak for them
        w.aggregate = ""                               # no summary (Claude 5.11 never shows one): transcripts tell
        s = self.poll()
        self.assertEqual((s.working, s.finished), (0, 1))   # no turn under way; "Old one" still unread as far as we know

    def test_chat_tab_shows_code_sessions_working_from_their_transcripts(self):
        # Reported 2026-11-19 (Claude 5.11492): on the chat tab the critter never showed a Code session working. Claude
        # shows no status there any more, nor a "Code, working" summary.
        w = self.world
        w.rows = [("BRIEF.md implementation end-to-end", "Idle"), ("Claude desktop companion blob app", "Running")]
        self.poll()
        w.sidebar_visible, w.chat_tab, w.aggregate = False, True, ""
        w.chat_rows = []
        w.local = {"BRIEF.md implementation end-to-end", "Claude desktop companion blob app",
                   "Started while on the chat tab"}
        w.working = {"BRIEF.md implementation end-to-end", "Started while on the chat tab"}
        s = self.poll()
        self.assertEqual((s.level, s.working), (model.WORKING, 2))
        w.working = set()                              # both turns ended
        s = self.poll()
        self.assertEqual((s.level, s.working), (model.IDLE, 0))

    def test_chat_tab_toast_for_an_unlisted_session(self):
        w = self.world
        w.rows = [("Claude mini", "Running")]
        self.poll()
        w.sidebar_visible = False
        w.chat_tab = True
        w.aggregate = "working"
        w.in_view = False                              # you're in Chrome; Claude's summary lags its toast
        w.toast("Claude-mini · Claude mini", "Fixed the chat tab.")
        for _ in range(20):
            s = self.poll()
            self.assertEqual(s.level, model.NEW)
        w.aggregate = "unread activity"
        self.assertEqual(self.poll().finished, 1)      # one session, not counted twice
        w.in_view = True                               # back in Claude: you open it from the Code tab
        w.sidebar_visible = True
        w.chat_tab = False
        w.rows = [("Claude mini", "Idle")]
        self.assertEqual(self.poll().level, model.IDLE)

    def test_chat_tab_summary_clears_a_toast_once_claude_is_in_view(self):
        w = self.world
        w.rows = [("Claude mini", "Running")]
        self.poll()
        w.sidebar_visible = False
        w.chat_tab = True
        w.aggregate = "unread activity"
        w.toast("Claude-mini · Claude mini", "Fixed the chat tab.")
        self.assertEqual(self.poll().finished, 1)
        w.aggregate = "working"                        # you read it (e.g. in another window); Claude's dot is gone
        self.assertEqual(self.poll(1).finished, 1)     # a moment's grace for Claude to catch up
        self.assertEqual(self.poll(3).finished, 0)
        w.toast("Claude-mini · Claude mini", "Fixed it again.")
        w.aggregate = ""                               # no summary at all (Claude 5.11): it can't clear the news
        self.assertEqual(self.poll(5).finished, 1)

    def test_claude_not_running_then_restarted(self):
        w = self.world
        w.rows = [("Plan", "Awaiting input")]
        self.poll()
        w.running = False
        s = self.poll()
        self.assertEqual(s.level, model.UNAVAILABLE)
        self.assertTrue(s.dimmed)
        w.toast("Plan", "Allow Claude to run ls?")     # toast left behind from before the restart
        w.running = True
        w.rows = [("Plan", "Idle")]
        s = self.poll()
        self.assertEqual((s.level, s.needs_me), (model.IDLE, 0))

    def test_notification_access_denied_is_visible(self):
        w = self.world
        w.access = False
        w.rows = [("A", "Idle")]
        self.assertEqual(self.poll().warnings, [])     # not flashed for a moment...
        s = self.poll(5)                               # ...but shown once it persists
        self.assertTrue(any("Notification access is off" in x for x in s.warnings))
        self.assertTrue(s.degraded)

    def test_error_titles_show_as_a_problem_in_claudes_words(self):
        w = self.world
        w.rows = [("A", "Connection lost"), ("B", "Session was interrupted"), ("C", "#12 · Merged")]
        s = self.poll(5)
        self.assertEqual((s.needs_me, s.warnings), (2, []))
        self.assertEqual(sorted((c.title, c.kind, c.detail) for c in s.cards),
                         [("A", model.PROBLEM, "Connection lost"), ("B", model.PROBLEM, "Session was interrupted")])

    def test_brief_read_hiccup_never_flashes_a_warning(self):
        w = self.world
        w.rows = [("Plan", "Awaiting input")]
        self.poll()
        w.sidebar_visible = False                      # Claude rebuilding its page while switching sessions
        s = self.poll()
        self.assertEqual((s.warnings, s.needs_me), ([], 1))
        w.sidebar_visible = True
        s = self.poll()
        self.assertEqual((s.warnings, s.needs_me), ([], 1))

    def test_needs_titles_prefer_the_one_that_just_asked(self):
        w = self.world
        w.rows = [("Older", "Awaiting input"), ("Newer", "Running")]
        self.poll()
        w.rows = [("Older", "Awaiting input"), ("Newer", "Awaiting input")]
        w.toast("Newer", "Allow Claude to run ls?")
        s = self.poll()
        self.assertEqual(s.needs_titles[0], "Newer")


class _Broken(object):
    def read(self, hwnd):
        from claude_mini import com
        raise com.ComError(-2147220991, "element not available")     # UIA_E_ELEMENTNOTAVAILABLE


class LoudBreaks(MonitorScenarios.__base__):
    """Asked 2026-11-19: every Claude update broke the critter silently. It now carries on from Claude Code's own
    records and says loudly what stopped working."""
    setUp = MonitorScenarios.setUp
    tearDown = MonitorScenarios.tearDown
    poll = MonitorScenarios.poll

    def test_a_changed_sidebar_layout_is_said_and_work_still_counts(self):
        w = self.world
        w.local = {"BRIEF.md implementation end-to-end", "Claude desktop companion blob app"}
        w.rows = []                                    # the sidebar is there, but no row can be read
        w.working = {"BRIEF.md implementation end-to-end"}
        s = self.poll()
        self.assertEqual((s.level, s.working, s.health), (model.WORKING, 1, []))    # not before it has lasted
        self.assertFalse(any("hidden" in x for x in s.warnings))
        s = self.poll(M.HEALTH_AFTER + 1)
        self.assertEqual([k for k, _ in s.health], ["sidebar"])
        self.assertEqual((s.cards[0].kind, s.cards[0].headline), (model.BROKEN, "Claude changed something"))
        self.assertIn("still show", s.cards[0].detail)
        self.assertTrue(s.degraded)
        self.assertEqual(s.working, 1)
        w.rows = [("BRIEF.md implementation end-to-end", "Running")]               # readable again
        s = self.poll()
        self.assertEqual((s.health, [c.kind for c in s.cards]), ([], []))

    def test_a_session_on_screen_is_known_even_when_the_sidebar_cant_be_read(self):
        # Reported 2026-11-25 (Claude 6.28899): the sidebar's rows couldn't be read, so the list of open sessions was
        # never updated; a permission card for the session you were looking at kept saying it needed you.
        w = self.world
        w.local = {"Realtime voice agent integration"}
        w.rows = []                                    # the sidebar's rows can't be read
        w.open = ["Realtime voice agent integration"]  # its pane header is on screen
        self.poll()
        w.toast("SwapnilAKale/Confab · Realtime voice agent integration", "Allow Claude to run git status?")
        s = self.poll()
        self.assertEqual((s.needs_me, s.open_needs), (0, ["Realtime voice agent integration"]))
        w.open = []                                    # you switch to another session: it needs you again
        self.assertEqual(self.poll().needs_me, 1)

    def test_a_row_from_the_transcripts_for_a_session_on_screen(self):
        # The log's case: rows unreadable, the session working (its row comes from its transcript), its pane open.
        w = self.world
        w.local = {"Realtime voice agent integration"}
        w.working = {"Realtime voice agent integration"}
        w.rows = []
        w.open = ["realtime voice agent  integration"]   # matched like titles elsewhere (case, spacing)
        self.poll()
        w.toast("SwapnilAKale/Confab · Realtime voice agent integration", "Allow Claude to run git status?")
        s = self.poll()
        self.assertEqual((s.needs_me, s.open_needs), (0, ["Realtime voice agent integration"]))

    def test_on_the_chat_tab_a_listed_session_open_on_screen_is_matched_like_elsewhere(self):
        # Found by the checker 2026-11-25: on the chat tab the open list was filtered by exact title, so a pane header
        # differing from its row in case or spacing made an open session count as needing you.
        w = self.world
        w.rows = [("Realtime voice agent integration", "Running")]
        self.poll()
        w.sidebar_visible, w.chat_tab = False, True
        w.chat_rows = [("Realtime voice agent integration", "Awaiting input")]
        w.open = ["realtime voice agent  integration"]
        w.toast("SwapnilAKale/Confab · Realtime voice agent integration", "Allow Claude to run git status?")
        s = self.poll()
        self.assertEqual((s.needs_me, s.open_needs), (0, ["Realtime voice agent integration"]))

    def test_an_open_session_waiting_on_you_is_listed_once(self):
        # Found by the checker 2026-11-25: a session already listed as open-and-waiting from its row could be listed
        # again from a notification that wasn't tied to that row, spelled like its pane header.
        w = self.world
        w.rows = [("Realtime voice agent integration", "Awaiting input")]
        w.open = ["realtime voice agent  integration"]
        self.poll()
        w.toast("SwapnilAKale/Confab · Realtime voice agent integration", "Allow Claude to run git status?")
        saved = M.best_row
        M.best_row = lambda rows, item: None           # the notification couldn't be tied to its row
        try:
            s = self.poll()
        finally:
            M.best_row = saved
        self.assertEqual((s.needs_me, s.open_needs), (0, ["Realtime voice agent integration"]))

    def test_news_seen_on_screen_is_read_without_a_sidebar_row(self):
        w = self.world
        w.rows = []
        w.open = ["Phase 1 project build"]
        self.poll()
        w.toast("Finder · Phase 1 project build", "Build finished, all tests pass")
        self.assertEqual(self.poll().new, 0)
        self.poll(M.GRACE + 1)                         # on screen for a few seconds: read
        w.open = []                                    # you move on
        self.assertEqual(self.poll().new, 0)           # it doesn't come back

    def test_the_open_list_is_forgotten_when_not_read(self):
        w = self.world
        w.rows = [("Perm", "Awaiting input")]
        w.open = ["Perm"]
        self.assertEqual(self.poll().needs_me, 0)
        self.m.uia = None
        M.UiaReader = lambda: _Broken()                # Claude's window can't be read any more
        self.assertEqual(self.poll().needs_me, 0)      # a moment's blip: still taken as open
        self.assertEqual(self.poll(M.OPEN_STALE_S + 1).needs_me, 1)

    def test_one_break_is_announced_once_while_claude_comes_and_goes(self):
        # Reported 2026-11-25: "sidebar works again" / "sidebar broke" every few seconds as Claude went behind other
        # apps; each "break" could notify again.
        w = self.world
        logs = []
        self.m.log = logs.append
        w.local = {"A"}
        w.rows = []
        self.poll()
        self.assertEqual([k for k, _ in self.poll(M.HEALTH_AFTER + 1).health], ["sidebar"])
        for hidden, in_view in [(True, False), (False, False), (False, True), (True, False), (False, False)]:
            w.hidden, w.in_view = hidden, in_view      # closed to the tray / behind another app / back, still broken
            self.assertEqual([k for k, _ in self.poll(30).health], ["sidebar"])    # still broken, not "fixed"
        w.hidden, w.in_view = False, True
        w.rows = [("A", "Idle")]                       # rows read again: fixed
        self.assertEqual(self.poll().health, [])
        said = [x for x in logs if x.startswith("health: sidebar")]
        self.assertEqual([x.split(":")[1].strip() for x in said], ["sidebar broke", "sidebar works again"])   # once each

    def test_a_day_old_usage_sample_is_not_todays_figure(self):
        # Reported 2026-11-25: at startup a sample from the day before showed 100% / 100% as current.
        w = self.world
        self.clock.t += 3 * 86400                      # the test clock starts near 0: make "a day ago" a real time
        now = M.time.time()
        w.sample = (now - 86400, {"5-hour": 100, "weekly": 100})
        self.assertIsNone(self.poll().usage)
        w.sample = (now - 60, {"5-hour": 3, "weekly": 40})
        self.assertEqual(dict((l, p) for l, p, _ in self.poll().usage.rows())["5-hour limit"], 3)

    def test_work_counts_while_claude_is_closed_to_the_tray(self):
        w = self.world
        w.local = {"Fix login"}
        w.rows = [("Fix login", "Idle")]
        self.poll()
        w.sidebar_visible = False                      # nothing on screen to read
        w.working = {"Fix login"}
        self.assertEqual(self.poll().working, 1)
        w.working = set()
        self.assertEqual(self.poll().working, 0)

    def test_a_ring_it_cant_read_is_said(self):
        w = self.world
        w.usage = "Usage: plenty left until Thursday"
        self.poll()
        s = self.poll(M.HEALTH_AFTER + 1)
        self.assertEqual([k for k, _ in s.health], ["ring"])
        w.usage = "Usage: Context 40%"                 # the ring showing the context window: fine
        self.assertEqual(self.poll().health, [])

    def test_a_break_then_a_compaction_is_not_a_failure(self):
        # Reported 2026-11-19: after an hour away, one compaction (a request, but not a reply: the usage hook doesn't
        # run) raised "usage hasn't updated". Not using Claude is normal.
        w = self.world
        now = M.time.time()
        w.hooked = model.Usage(30, "5-hour limit", now + 3 * 3600, now)
        w.turn_ends = [now]                            # that reply's reading came with it
        self.poll()
        w.last_activity = now + 3700                   # the compaction, an hour later
        self.poll(3700)
        s = self.poll(M.HEALTH_AFTER + 1)
        self.assertEqual(s.health, [])

    def test_replies_without_a_usage_reading_mean_the_hook_stopped(self):
        w = self.world
        now = M.time.time()
        w.hooked = model.Usage(30, "5-hour limit", now + 3 * 3600, now)
        w.turn_ends = [now, now + 600]                 # one reply finished with no reading: could be a fluke
        self.assertEqual(self.poll(1000).health, [])
        self.assertEqual(self.poll(M.HEALTH_AFTER + 1).health, [])
        w.turn_ends = [now, now + 600, now + 900]       # a second one: the hook stopped
        self.poll()
        s = self.poll(M.HEALTH_AFTER + 1)
        self.assertEqual([k for k, _ in s.health], ["usage"])
        w.hooked = model.Usage(31, "5-hour limit", now + 3 * 3600, now + 1100)    # it's back
        self.assertEqual(self.poll().health, [])


class Cards(MonitorScenarios.__base__):
    setUp = MonitorScenarios.setUp
    tearDown = MonitorScenarios.tearDown
    poll = MonitorScenarios.poll

    def test_cards_use_claudes_own_words_and_put_urgent_first(self):
        w = self.world
        w.rows = [("Fix login", "Running"), ("Plan it", "Running"), ("Ask me", "Running"), ("Done", "Running"),
                  ("Old wait", "Awaiting input"), ("Busy", "Running"), ("This chat", "Awaiting input")]
        w.open = ["This chat"]
        self.poll()
        w.rows = [("Fix login", "Awaiting input"), ("Plan it", "Awaiting input"), ("Ask me", "Awaiting input"),
                  ("Done", "Unread response"), ("Old wait", "Awaiting input"), ("Busy", "Running"),
                  ("This chat", "Awaiting input")]
        w.toast("Done", "Split the token refresh into its own module; tests pass.")
        w.toast("Claude-mini · Fix login", 'Allow Claude to run npm test?')
        w.toast("Claude-mini · Plan it", "Allow Claude to ExitPlanMode Reply with the word hello.?")
        w.toast("Ask me", "Should the examples use the v2 client?")
        s = self.poll()
        got = [(c.title, c.kind, c.detail) for c in s.cards]
        self.assertEqual(got[:3], [("Ask me", model.QUESTION, "Should the examples use the v2 client?"),
                                   ("Plan it", model.PLAN, "Reply with the word hello."),
                                   ("Fix login", model.PERMISSION, "Allow Claude to run npm test?")])
        self.assertIn(("Old wait", model.WAITING, "Awaiting input"), got)
        self.assertEqual(got[-1], ("Done", model.FINISHED, "Split the token refresh into its own module; tests pass."))
        self.assertNotIn("This chat", [c.title for c in s.cards])     # open in Claude, no toast
        self.assertNotIn("Busy", [c.title for c in s.cards])          # working isn't news
        self.assertEqual(s.cards[0].session, "Ask me")

    def test_card_goes_away_when_claude_clears_it(self):
        w = self.world
        w.rows = [("Done", "Running")]
        self.poll()
        w.rows = [("Done", "Unread response")]
        w.toast("Done", "All tests pass.")
        self.assertEqual(len(self.poll().cards), 1)
        w.rows = [("Done", "Idle")]                    # you opened it in Claude
        self.assertEqual(self.poll(5).cards, [])

    def test_thread_without_sidebar_row_gets_an_update_card(self):
        w = self.world
        w.rows = [("A", "Idle")]
        self.poll()
        w.toast("Website redesign", "Claude posted an update in the project.")
        s = self.poll()
        self.assertEqual([(c.title, c.kind) for c in s.cards], [("Website redesign", model.UPDATE)])
        self.assertEqual(s.cards[0].session, "Website redesign")     # clicking it still looks for that thread

    def test_card_opens_its_code_session_when_the_blob_started_on_the_chat_tab(self):
        # The reported case: the blob started while Claude was on the chat tab, so it had never seen the Code tab's
        # list. A session finished; clicking its card has to find it by the notification's title.
        w = self.world
        w.sidebar_visible = False
        w.chat_tab = True
        w.aggregate = "working"
        self.poll()
        w.toast("Claude-mini · Claude desktop companion blob app", "Chat-tab reader updated; 65 tests pass")
        card = self.poll().cards[0]
        w.rows = [("Claude desktop companion blob app", "Unread response")]     # what the Code tab lists
        self.assertTrue(self.m._select(card.session))
        self.assertEqual(w.shown, ["Claude desktop companion blob app"])

    def test_card_opens_a_plain_chat(self):
        w = self.world
        w.sidebar_visible = False
        w.chat_tab = True
        self.poll()
        w.toast("Crafting an X post", "Here are three drafts.")
        card = self.poll().cards[0]
        w.chats = [("Crafting an X post", None)]
        self.assertTrue(self.m._select(card.session))
        self.assertEqual(w.shown, ["Crafting an X post"])


class NeverDrivesClaude(MonitorScenarios.__base__):
    setUp = MonitorScenarios.setUp
    tearDown = MonitorScenarios.tearDown
    poll = MonitorScenarios.poll

    def test_new_permission_prompt_never_switches_claudes_view(self):
        w = self.world
        w.rows = [("Perm", "Running")]
        self.poll()
        w.rows = [("Perm", "Awaiting input")]
        w.perm_buttons["Perm"] = ["Deny", "Allow once"]
        w.toast("Claude-mini \u00b7 Perm", "Allow Claude to run npm test?")
        for _ in range(5):
            s = self.poll()
        self.assertEqual(w.shown, [])                  # showing a prompt makes Claude raise itself: never do it
        self.assertEqual((s.cards[0].kind, s.cards[0].detail), (model.PERMISSION, "Allow Claude to run npm test?"))


class LaggingSidebar(MonitorScenarios.__base__):
    """Out of view, Claude's sidebar can lag 16 s+ behind its own notifications (seen in the field)."""
    setUp = MonitorScenarios.setUp
    tearDown = MonitorScenarios.tearDown
    poll = MonitorScenarios.poll

    def test_finished_stays_blue_while_the_sidebar_still_says_running(self):
        w = self.world
        w.rows = [("Fork", "Running"), ("Critter", "Running")]
        self.poll()
        w.toast("Fork", "run Tests A-C on Windows and report each result")
        for _ in range(20):                            # sidebar stuck on "Running" for 20 s
            s = self.poll()
            self.assertEqual((s.level, s.finished, s.working), (model.NEW, 1, 1))
        w.rows = [("Fork", "Unread response"), ("Critter", "Running")]   # sidebar catches up
        self.assertEqual(self.poll().finished, 1)
        w.rows = [("Fork", "Idle"), ("Critter", "Running")]              # you opened it
        s = self.poll()
        self.assertEqual((s.finished, s.working, s.level), (0, 1, model.WORKING))

    def test_permission_stays_amber_while_the_sidebar_still_says_running(self):
        # 22:36:28 permission toast -> amber; 22:36:33 the old rule dropped it because the row still said Running.
        w = self.world
        w.rows = [("Critter 3D", "Running"), ("This chat", "Running")]
        self.poll()
        w.toast("Claude-mini · Critter 3D", "Allow Claude to run sed -i 's/^import ma/x/' avatar.py?")
        for _ in range(15):
            s = self.poll()
            self.assertEqual((s.level, s.needs_me), (model.NEEDS_ME, 1))
            self.assertEqual(s.cards[0].kind, model.PERMISSION)
        w.rows = [("Critter 3D", "Awaiting input"), ("This chat", "Running")]  # sidebar catches up
        self.assertEqual(self.poll().needs_me, 1)
        w.rows = [("Critter 3D", "Running"), ("This chat", "Running")]         # you answered it
        s = self.poll()
        self.assertEqual((s.needs_me, s.working), (0, 2))

    def test_opened_before_the_sidebar_caught_up(self):
        w = self.world
        w.rows = [("Fork", "Running")]
        self.poll()
        w.toast("Fork", "all tests pass")
        self.assertEqual(self.poll().finished, 1)
        w.rows = [("Fork", "Idle")]                    # Running -> Idle directly: you saw it
        self.assertEqual(self.poll().level, model.IDLE)

    def test_same_named_sessions_get_one_card(self):
        w = self.world
        w.rows = [("Fork", "Running"), ("Fork", "Idle")]
        self.poll()
        w.toast("Fork", "done with the tests")
        w.toast("Fork", "done with the tests")         # Claude sometimes posts it twice
        s = self.poll()
        self.assertEqual((s.finished, len(s.cards)), (1, 1))


class PanelClicks(unittest.TestCase):
    def test_context_and_cloud_lines_open_their_session(self):
        from claude_mini.cards import CardPanel
        p = CardPanel.__new__(CardPanel)
        s = model.Snapshot()
        s.cards = [model.Card("Perm", model.PERMISSION, "Allow?", "Perm")]
        s.contexts = [model.Context("BRIEF.md implementation end-to-end", 68, 680000, 1000000, None, 0.0, None, None)]
        s.cloud = [("Deploy to Oracle", "Working", M.WORKING)]
        p.snapshot = s
        self.assertIs(p._clicked(("card", 0)), s.cards[0])
        self.assertEqual(p._clicked(("context", 0)).session, "BRIEF.md implementation end-to-end")
        self.assertEqual(p._clicked(("cloud", 0)).session, "Deploy to Oracle")
        self.assertIsNone(p._clicked(("context", 3)))
        self.assertIsNone(p._clicked(None))


class BlobClickCycle(unittest.TestCase):
    def test_cycles_through_every_waiting_session(self):
        from claude_mini.app import next_session
        waiting = ["A", "B", "C"]
        opened, visited, open_now = [], [], None
        for _ in range(6):
            # what the blob sees: the open session drops off the list and goes to the end of the round
            cands = [t for t in waiting if t != open_now] + ([open_now] if open_now else [])
            open_now, visited = next_session(cands, visited)
            opened.append(open_now)
        self.assertEqual(opened, ["A", "B", "C", "A", "B", "C"])

    def test_resolved_session_leaves_the_round(self):
        from claude_mini.app import next_session
        t, v = next_session(["A", "B", "C"], [])
        self.assertEqual(t, "A")
        t, v = next_session(["B", "C"], v)            # A answered: gone
        self.assertEqual(t, "B")
        t, v = next_session(["C", "B"], v)
        self.assertEqual(t, "C")
        self.assertEqual(next_session([], v), (None, []))


class Classifiers(unittest.TestCase):
    def test_status_labels(self):
        c = M.classify_status
        for label in ("Awaiting input", "Awaiting answer", "Needs input", "Waiting on you", "Plan ready for review",
                      "Needs your approval · Bash", "Failed to run"):
            self.assertEqual(c(label), M.NEEDS, label)
        for label in ("Unread response", "Ready for review"):
            self.assertEqual(c(label), M.NEW, label)
        for label in ("Running", "Using the browser", "Working"):
            self.assertEqual(c(label), M.WORKING, label)
        for label in ("Idle", "Done", "Archived", "Resolved", "Merged", "#3, #4 · Changes requested"):
            self.assertEqual(c(label), M.IDLE, label)
        for label in ("Connection lost", "Computer went to sleep", "Your subscription is paused"):
            self.assertEqual(c(label), M.NEEDS, label)
            self.assertTrue(M.is_problem(label), label)
        self.assertFalse(M.is_problem("Awaiting input"))

    def test_plain_row_status(self):
        # Claude 5.11+: the texts after a row's title are its status, a subtitle or a caption, all alike.
        def status(*texts):
            row = Row(texts[0] if texts else "Idle", "T", False)
            row.texts = texts
            return M.plain_status(row, others)
        others = set()
        self.assertEqual(status("Includes an artifact"), "Idle")
        self.assertEqual(status("Daily"), "Idle")
        self.assertEqual(status("Daily", "Running"), "Running")
        self.assertEqual(status(), "Idle")
        self.assertEqual(others, {"Includes an artifact", "Daily"})
        for label in ("Awaiting input", "Unread response", "#12 · Open", "Connection lost",
                      "Lost connection to devbox", "Claude Code on my-box stopped abruptly", "Session was interrupted",
                      "Couldn’t access Projects", "Something went wrong"):
            self.assertEqual(status(label), label, label)
        self.assertEqual(M.plain_status(Row("Anything", "T", False)), "Anything")    # another layout: its own label

    def test_toast_bodies(self):
        c = M.classify_toast
        self.assertEqual(c('Allow Claude to run python -c "x"?'), M.NEEDS)
        self.assertEqual(c("On laptop: Allow Claude to edit a.py?"), M.NEEDS)
        self.assertEqual(c("Should I go with 1 and 2?"), M.NEEDS)
        self.assertEqual(c("Claude is waiting for your input"), M.NEEDS)
        self.assertEqual(c("Claude ran into a problem"), M.NEEDS)
        self.assertEqual(c("Claude finished a task"), M.NEW)
        self.assertEqual(c("Claude archived: Blob test"), M.NEW)

    def test_toast_titles(self):
        m = M.toast_matches
        self.assertTrue(m("Claude-mini · Perm", "Perm"))
        self.assertTrue(m("Perm · someone@example.com", "Perm"))
        self.assertTrue(m("Confab · Local Voice setup and agent connection (…", "Local Voice setup and agent connection (fork)"))
        self.assertFalse(m("Confab · Local Voice setup and agent connection (…", "Local Voice read-aloud for FitNotes"))
        self.assertFalse(m("Confab · Plan…", "Planning the release"))    # too short to tell
        self.assertFalse(m("Confab · Perm", ""))


if __name__ == "__main__":
    unittest.main()
