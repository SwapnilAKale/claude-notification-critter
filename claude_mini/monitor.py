"""Status monitor: combines Source B (sidebar via UI Automation) and Source A (Claude's own toasts).

Rules (mirroring what the Claude app itself shows):
- Source B is the truth for every session in the sidebar. A session's contribution clears exactly when its sidebar
  indicator clears (answered, opened, finished...). No timers clear anything.
- Sessions open in Claude's pane(s) are left out, like Claude's own "N sessions need input" pill does - unless Claude
  sent a toast for that session (it only does that when you are NOT looking at it).
- On the chat tab the sidebar lists only project sessions (with their status); every other Code session keeps its
  last Code-tab reading, held in check by the Code toggle's own summary ("Code, awaiting your input" / "unread
  activity" / "working"): a remembered status more urgent than that summary is over.
- Chats are left out: Claude only learns a chat has replied when it next checks (rarely while you're on the Code tab),
  and marks nothing when it does, so the blob couldn't tell you more than a glance at Claude.
- If the sidebar can't be read right now (narrow window, sidebar hidden), the last good reading is kept and marked
  stale, and new toasts are layered on top. A warning appears only if this lasts.
- A toast whose session isn't in the sidebar (e.g. project threads) counts on its own until it leaves Action Center
  or you open Claude from the blob.
"""
import queue
import re
import threading
import time
import traceback

from . import com, model, win32
from .claude_window import claude_window_state
from .notifications import ALLOWED, NotificationReader
from .uia import Row, UiaReader
from .usage import PlanHistory, TranscriptUsage, merge_sample, window_kind

POLL = 1.0
GRACE = 3.0              # on the chat tab, give the Code toggle's summary this long to catch up with a toast
WARN_AFTER = 4.0         # a problem only becomes a visible warning if it lasts this long
MOVED_ON_SLACK = 2.0     # a request this soon after a toast may be the one that asked
HEALTH_AFTER = 20.0      # a source has to stay broken this long before the blob says so (Claude redraws, restarts)
HEALTH_RECOVER = 60.0    # ... and stay fine this long before it counts as working again (no flipping on/off)
SAMPLE_FRESH_S = 1800    # Claude's own usage samples older than this aren't used (a day-old one isn't today's figure)
OPEN_STALE_S = 10.0      # the list of sessions open in Claude's panes is forgotten after this long without a reading
HOOK_MISSED = 2          # this many finished replies without a usage reading: the usage hook stopped
HOOK_SLACK_S = 300       # ... the first of them at least this long ago (a reply's reading lands with it)


def claude_in_view(hwnd):
    from .claude_window import claude_in_view as in_view
    return in_view(hwnd)


NEEDS = "needs"
NEW = "new"
WORKING = "working"
IDLE = "idle"

_NEEDS_EXACT = {"awaiting input", "awaiting answer", "needs input", "needs you", "waiting on you",
                "waiting for your answer", "plan ready for review", "plan ready", "needs your input"}
_NEW_EXACT = {"unread response", "ready for review", "unread activity", "unread"}
_WORKING_EXACT = {"running", "using the browser", "working", "landing", "starting", "compacting"}
_IDLE_EXACT = {"idle", "done", "resolved", "archived", "stopped", "completed"}
# Pull-request states Claude can show instead of a status ("#12 · Open"); not something you need to act on.
_PR_LABELS = {"no pull request", "open", "draft", "approved", "changes requested", "merge conflicts", "queued",
              "merged", "closed"}
_ERROR_WORDS = re.compile(r"\b(error|errored|failed|failure|problem|crashed|couldn.t|could not)\b", re.I)


def classify_status(label):
    s = " ".join(label.split()).lower()
    if s in _NEEDS_EXACT or s.startswith("needs your approval") or s.startswith("awaiting") or _ERROR_WORDS.search(s):
        return NEEDS
    if s in _NEW_EXACT or s.startswith("unread"):
        return NEW
    if s in _WORKING_EXACT:
        return WORKING
    if s in _IDLE_EXACT or s.split(" · ")[-1] in _PR_LABELS:
        return IDLE
    # Claude's status icon only ever carries the labels above, a pull-request state, or the title of what went wrong
    # ("Connection lost", "Session was interrupted", ... about 115 of them, see RECON.md). So anything else is a problem.
    return NEEDS if s else None


def _known_status(label):
    """A label Claude uses for a session's state (not the "anything else is a problem" fallback), or an error."""
    s = " ".join(label.split()).lower()
    return bool(s) and (s in _NEEDS_EXACT or s in _NEW_EXACT or s in _WORKING_EXACT or s in _IDLE_EXACT or
                        s.startswith(("awaiting", "needs your approval", "unread")) or s in _PR_LABELS or
                        bool(_ERROR_WORDS.search(s)))


def cloud_status(row):
    """The status that counts for a sidebar row. A cloud session's row also shows its state in its caption ("Cloud ·
    Working", "Cloud · Needs input", "Cloud · Merged"); when its status icon says nothing (idle), a state Claude
    uses there counts instead."""
    state = getattr(row, "env_state", None)
    if getattr(row, "env", None) and state and classify_status(row.status) == IDLE and _known_status(state):
        return state
    return row.status


MAX_CLOUD = 4
CLOUD_SHOWN_S = 3600     # a cloud session stays on the panel's list this long after it last showed any activity


def cloud_list(rows):
    """(title, state, class) for every cloud session in the sidebar, the ones needing you first. The state is
    Claude's own label: its status icon's, else its caption's ("Merged")."""
    out = []
    for r in rows or ():
        if getattr(r, "env", None) != "Cloud" or _norm(r.status) == "archived":
            continue
        state = r.status if classify_status(r.status) != IDLE else (r.env_state or r.status)
        out.append((r.title, state, row_class(r) or IDLE))
    out.sort(key=lambda c: -_RANK.get(c[2], 0))         # stable: otherwise in the sidebar's order
    return out


_DURATION = re.compile(r"(\d+)\s*(day|d|hr|hour|h|min|minute|m|sec|second|s)\w*", re.I)
_UNIT_S = {"d": 86400, "h": 3600, "m": 60, "s": 1}


def parse_duration(text):
    """Seconds in "1 hr 1 min", "5 min", "2 days"; None if there's no duration."""
    total, found = 0, False
    for n, unit in _DURATION.findall(text):
        u = unit.lower()
        total += int(n) * _UNIT_S["m" if u.startswith("mi") else u[0]]
        found = True
    return total if found else None


_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_CLOCK = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m\b|\b(\d{1,2}):(\d{2})\b", re.I)


def parse_reset(text, now):
    """When a limit resets, from Claude's wording: "Resets in 1 hr 1 min", "Resets at 6:30 AM", "Resets Wed 4:30 PM",
    "Resets Nov 5, 4:30 PM" (clock times are local). None if it can't tell. (Reported 2026-11-04: "4:30" was read as
    4 minutes 30 seconds, so a weekly limit seemed to reset within minutes and dropped to 0%.)"""
    low = " ".join(text.split()).lower()
    if not low.startswith("resets"):
        return None
    if re.search(r"\bin\b", low):
        secs = parse_duration(low)
        return now + secs if secs is not None else None
    m = _CLOCK.search(low)
    hour = minute = 0
    if m:
        if m.group(3):
            hour, minute = int(m.group(1)) % 12 + (12 if m.group(3) == "p" else 0), int(m.group(2) or 0)
        else:
            hour, minute = int(m.group(4)), int(m.group(5))
        if hour > 23 or minute > 59:
            return None
    t = time.localtime(now)
    base = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, hour, minute, 0, 0, 0, -1))
    words = re.findall(r"[a-z]+", low)
    month = next((i for i, w in enumerate(_MONTHS) if any(x.startswith(w) for x in words)), None)
    day = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\b(?!\s*:)(?!\s*[ap]\.?\s*m)", low)
    if month is not None and day:
        year = t.tm_year + (1 if month + 1 < t.tm_mon else 0)
        return time.mktime((year, month + 1, int(day.group(1)), hour, minute, 0, 0, 0, -1))
    weekday = next((i for i, w in enumerate(_WEEKDAYS) if w in [x[:3] for x in words]), None)
    if weekday is not None:
        ahead = (weekday - t.tm_wday) % 7
        at = base + ahead * 86400
        return at if at > now else at + 7 * 86400
    if "tomorrow" in words:
        return base + 86400
    if m:
        return base if base > now else base + 86400
    return None


def parse_usage(label, now):
    """Claude's usage ring label -> model.Usage, or None. Seen: "Usage: 43% of session limit, Resets in 1 hr 1 min";
    from its code: "Usage: <Limit>: 80%, ..." for other limits, "Plan usage unavailable" when it can't tell."""
    text = label[len("Usage:"):].strip() if label.startswith("Usage:") else label
    parts = [p.strip() for p in text.split(", ")]
    if not parts or parts[0].lower().startswith("context"):
        return None                      # the ring is showing the context window, not plan usage
    m = re.match(r"^(\d+(?:\.\d+)?)% of (.+)$", parts[0])
    if m:
        pct, limit = float(m.group(1)), m.group(2).strip()
    else:
        m = re.match(r"^(.+?):\s*(\d+(?:\.\d+)?)%$", parts[0])
        if not m:
            return None
        limit, pct = m.group(1).strip(), float(m.group(2))
    limit = limit[0].upper() + limit[1:]
    resets_at = None
    for i, p in enumerate(parts[1:], 1):
        if p.lower().startswith("resets"):
            resets_at = parse_reset(", ".join(parts[i:]), now)      # "Resets Nov 5, 4:30 PM" spans two parts
            break
    return model.Usage(int(round(pct)), limit, resets_at, now)


def merge_ring(usage, ring):
    """A fresh figure from Claude's usage ring ("43% of session limit") put into the full reading: it replaces the
    matching limit (session = the 5-hour limit, weekly = all models) and makes the reading current."""
    if usage is None:
        return ring
    want = "5-hour" if "session" in ring.limit.lower() else ("all models" if "weekly" in ring.limit.lower() else None)
    wins = [(usage.limit, usage.pct, usage.resets_at)] + list(usage.others)
    hit = False
    for i, (label, pct, resets_at) in enumerate(wins):
        if want and want in label.lower() or label.lower() == ring.limit.lower():
            wins[i] = (label, ring.pct, ring.resets_at or resets_at)
            hit = True
    if not hit:
        wins.append((ring.limit, ring.pct, ring.resets_at))
    top = max(wins, key=lambda w: w[1])
    return model.Usage(top[1], top[0], top[2], ring.seen_at, [w for w in wins if w is not top])


WINDOW_S = {"5-hour": 5 * 3600, "weekly": 7 * 86400}


def ring_is_ahead(usage, ring, ring_since, last_reset, now):
    """Is the ring's figure newer than the reading's for the same limit, though the ring didn't change? Within one
    window usage only grows, so a higher figure with the same reset time (to a minute or two) is the newer one
    (reported 2026-11-04: the ring said 98% while the blob kept an earlier 84%). If the reading doesn't know when
    that limit resets (it has reset since, as Claude's samples said), the ring's figure counts if it's at least as
    high, resets within one window from now, and was first shown after that reset (so it isn't left over from before:
    `ring_since` is when the ring first showed this figure, `last_reset` the latest reset per limit)."""
    kind = window_kind(ring.limit)
    if usage is None or kind is None or ring.resets_at is None:
        return False
    for label, pct, resets_at in [(usage.limit, usage.pct, usage.resets_at)] + list(usage.others):
        if window_kind(label) == kind:
            if resets_at is not None:
                return abs(resets_at - ring.resets_at) <= 120 and ring.pct > pct
            return ring.pct >= pct and 0 < ring.resets_at - now <= WINDOW_S[kind] + 120 and \
                ring_since >= last_reset.get(kind, 0)
    return False


def passed_resets(usage, upto):
    """{limit kind: reset time} for the limits of `usage` that reset by `upto`."""
    out = {}
    for label, _, resets_at in ([(usage.limit, usage.pct, usage.resets_at)] + list(usage.others)) if usage else ():
        kind = window_kind(label)
        if kind and resets_at is not None and resets_at <= upto:
            out[kind] = resets_at
    return out


def stopped_at_limit(row, items):
    """While a plan limit is full: does everything Claude says about this session fit a session stopped at the limit?
    That's its generic error label ("Something went wrong") and/or its generic "Claude is waiting for your input"
    notification. A real permission prompt, plan or question is never taken for one."""
    asks = [card_content(i.toast.body, True)[0] for i in items if i.kind == NEEDS]
    if any(k in (model.PERMISSION, model.PLAN, model.QUESTION) for k in asks):
        return False
    return is_problem(row.status) or (bool(asks) and all(k in (model.WAITING, model.PROBLEM) for k in asks))


def paused_detail(stop, resumes, now):
    """A paused card's text: when the limit resets, or that it has. `stop`: the session's refused request, if its
    transcript shows one; `resumes`: when the full plan limit resets, if one is full."""
    at = stop["resets_at"] if stop and stop["resets_at"] else resumes
    if at is not None and at > now:
        return "Stopped at the usage limit · resets in %s" % model.duration_text(at - now)
    if stop:
        return "Stopped at the usage limit, which has reset since. Send it a message to carry on"
    return "Stopped at the usage limit · resets soon"


def is_problem(label):
    """An error title rather than a request for your input."""
    s = " ".join(label.split()).lower()
    return classify_status(label) == NEEDS and not (
        s in _NEEDS_EXACT or s.startswith("needs your approval") or s.startswith("awaiting"))


def classify_toast(body):
    b = " ".join(body.split())
    low = b.lower()
    if re.match(r"^(on .+?: )?allow claude to\b", low) or b.endswith("?"):
        return NEEDS                     # permission prompts, plan approvals, questions
    if any(k in low for k in ("waiting for your input", "needs your", "needs input", "has a question",
                              "waiting on you", "needs you")):
        return NEEDS
    if _ERROR_WORDS.search(low):
        return NEEDS                     # failed / errored threads
    return NEW


def card_content(body, matched):
    """(kind, detail) for a toast body, keeping Claude's own wording."""
    b = " ".join(body.split())
    low = b.lower()
    m = re.match(r"^(on .+?: )?allow claude to exitplanmode\b\s*(.*?)\??$", b, re.I)
    if m:
        return model.PLAN, m.group(2).strip() or b
    if re.match(r"^(on .+?: )?allow claude to\b", low):
        return model.PERMISSION, b
    if _ERROR_WORDS.search(low):
        return model.PROBLEM, b
    if b.endswith("?") or "has a question" in low:
        return model.QUESTION, b
    if classify_toast(b) == NEEDS:
        return model.WAITING, b
    return (model.FINISHED if matched else model.UPDATE), b


def _norm(s):
    return " ".join(s.split()).lower()


def toast_matches(toast_title, row_title):
    r = _norm(row_title)
    if not r:
        return False
    # "<session>", "<folder> · <session>" or "<session> · <account>". Claude cuts a long one short with "…"
    # ("SwapnilAKale/Confab · Local Voice setup and agent connection (…").
    for p in [toast_title] + toast_title.split("·"):
        p = _norm(p)
        if p == r or (p.endswith("…") and len(p) > 10 and r.startswith(p[:-1].rstrip())):
            return True
    return False


_RANK = {NEEDS: 3, NEW: 2, WORKING: 1}


class _Remembered(Row):
    """A Code-tab row the chat tab doesn't list: its status is as last seen, capped by the Code toggle's summary."""
    __slots__ = ("cap",)

    def __init__(self, row, cap):
        Row.__init__(self, row.status, row.title, row.offscreen, False, row.env, row.env_state)
        self.cap = cap


def chat_tab_rows(listed, remembered, aggregate):
    # The chat tab also lists chats, which can look just like Code sessions (Claude 2.37+); only rows already known as
    # Code sessions count. The rest of Code is covered by the Code toggle's summary.
    known = {r.title for r in remembered or ()}
    listed = [r for r in listed if r.title in known]
    titles = {_norm(r.title) for r in listed}
    # No summary: Claude 5.11 shows none at all, so it says nothing (it used to mean nothing was going on).
    cap = classify_status(aggregate) if aggregate else None
    return list(listed) + [_Remembered(r, cap) for r in remembered or () if _norm(r.title) not in titles]


def with_transcript_turns(rows, working, local=(), demote=True):
    """Rows with what the transcripts say, Claude Code's own record of each local session (sturdier than Claude's
    screen, which every update may change): a session with a turn under way is working, listed or not. With `demote`
    (Claude's screen can't be read right now, or shows no status: the chat tab, Claude closed to the tray, a changed
    layout) a local session (`local`: titles the transcripts speak for) shown as working whose turn has ended isn't any
    more. Cloud sessions have no transcript and keep their row. Questions, permissions and news come from Claude's
    notifications. Reported 2026-11-19: on the chat tab the critter never showed a Code session working."""
    out, titles = [], set()
    for r in rows or ():
        titles.add(_norm(r.title))
        cls = classify_status(r.status)
        if r.title in working and cls in (IDLE, WORKING):
            r.status = "Running"
        elif demote and r.title in local and r.title not in working and cls == WORKING:
            r.status = "Idle"
        out.append(r)
    out.extend(Row("Running", t, False) for t in sorted(working) if _norm(t) not in titles)
    return out


def row_class(row):
    cls = classify_status(row.status)
    cap = getattr(row, "cap", None)
    if cap is not None and _RANK.get(cls, 0) > _RANK.get(cap, 0):
        return IDLE                      # the Code toggle says nothing that urgent is left
    return cls


def best_row(rows, item):
    """The one sidebar row a toast belongs to. Sessions can share a title (e.g. two forks); prefer the row whose
    status fits the toast (unread for a finished toast, awaiting for a request), then one still working."""
    matches = [r for r in rows if toast_matches(item.toast.title, r.title)]
    if item.row_title is not None:
        same = [r for r in matches if r.title == item.row_title]
        matches = same or matches
    if len(matches) <= 1:
        return matches[0] if matches else None
    order = {item.kind: 0, WORKING: 1}
    return min(matches, key=lambda r: order.get(classify_status(r.status), 2))


class _Item(object):
    __slots__ = ("toast", "kind", "seen_at", "row_title", "status_then", "caught_up", "viewed_since")

    def __init__(self, toast):
        self.toast = toast
        self.kind = classify_toast(toast.body)
        self.seen_at = time.time()
        self.row_title = None            # sidebar row this toast belongs to, once known
        self.status_then = None          # that row's status when the toast first matched it
        self.caught_up = False           # the row has shown the toast's news (Unread response / Awaiting input)
        self.viewed_since = None         # since when its session has been open in front of you


class Monitor(threading.Thread):
    def __init__(self, notify):
        threading.Thread.__init__(self, name="claude-mini-monitor", daemon=True)
        self.notify = notify                 # called (from this thread) when a new snapshot is ready
        self.log = lambda text: None         # the app swaps in its --verbose logger
        self.latest = model.starting()
        self.lock = threading.Lock()
        self.requests = queue.Queue()
        self.stop_event = threading.Event()
        self.expanded = False
        self.quiet_until = 0.0               # no "unreadable" warning until then (panel just folded)
        self.uia = None
        self.toasts = None
        self.baseline = None                 # toast ids present when we started (history, ignored)
        self.items = {}                      # id -> _Item for toasts that arrived while running
        self.consumed = set()
        self.last_rows = None                # last good sidebar reading
        self.usage = None                    # last model.Usage
        self.transcripts = None              # TranscriptUsage: the get_usage hook's records
        self.history = None                  # PlanHistory: the figures Claude itself samples every ~15 minutes
        self.hook_at = 0.0                   # when the newest hook reading in use was taken
        self.sample_at = 0.0                 # ... and the newest of Claude's samples
        self.ring_pct = None                 # the percentage Claude's usage ring showed last time
        self.ring_since = 0.0                # ... and since when it has shown it
        self.last_reset = {}                 # limit kind -> when it last reset, as far as the readings tell
        self.code_rows = None                # last Code-tab reading (the chat tab lists only project sessions)
        self.last_open = set()
        self.unreadable_since = None
        self.warn_since = {}
        self.health_since = {}               # health key -> since when that source has been broken
        self.health_said = set()             # broken sources already logged
        self.health_text = {}                # health key -> what was said about it last
        self.health_gone = {}                # health key -> since when it hasn't shown (it may only be out of sight)
        self.health_ok = set()               # health keys a reading has just shown working (rows read again...)
        self.open_at = 0.0                   # when the list of open sessions was last read
        self.access_checked = 0.0
        self.access_ok = True
        self.last_key = None
        self.cloud_seen = {}                 # cloud session title -> (state, when it was last active, class)

    # ---------- called from the UI thread ----------
    def set_expanded(self, value):
        self.expanded = value
        if not value:
            self.quiet_until = time.time() + WARN_AFTER   # Claude is resizing back; give the sidebar time

    def select_session(self, title, timeout=4.0):
        return self._run(lambda: self._select(title), timeout)

    def acknowledge_unmatched(self):
        self.requests.put((self._acknowledge, None, None))

    def stop(self):
        self.stop_event.set()
        self.requests.put((None, None, None))

    def _run(self, fn, timeout):
        done = threading.Event()
        box = []
        self.requests.put((fn, done, box))
        if not done.wait(timeout):
            return False
        return box[0] if box else False

    # ---------- worker ----------
    def run(self):
        com.init_mta()
        next_poll = 0.0
        while not self.stop_event.is_set():
            wait = max(0.0, next_poll - time.time())
            try:
                fn, done, box = self.requests.get(timeout=wait)
                if fn is not None:
                    try:
                        result = fn()
                    except Exception:
                        traceback.print_exc()
                        result = False
                    if box is not None:
                        box.append(result)
                    if done is not None:
                        done.set()
                    next_poll = 0.0          # refresh right after acting
                continue
            except queue.Empty:
                pass
            try:
                snap = self._poll()
            except Exception as e:
                traceback.print_exc()
                snap = model.Snapshot()
                snap.warnings.append("Status reader error: %s" % e)
                self.uia = None
                self.toasts = None
            self._publish(snap)
            next_poll = time.time() + POLL

    def _publish(self, snap):
        # Brief hiccups (Claude rebuilding its page, a resize) never flash the warning mark: a warning is shown
        # only once the same problem has lasted WARN_AFTER seconds.
        now = time.time()
        current = set(snap.warnings)
        for w in current:
            self.warn_since.setdefault(w, now)
        for w in list(self.warn_since):
            if w not in current:
                del self.warn_since[w]
        snap.warnings = [w for w in snap.warnings if now - self.warn_since[w] >= WARN_AFTER]
        # A broken source is said loudly (a red card, and the app's own notification), but only once it has lasted.
        # A problem that stops showing has often only gone out of sight (Claude behind another app, no Code session
        # open): it counts as working again only on evidence (e.g. the sidebar's rows read again) or after it has
        # stayed away HEALTH_RECOVER seconds, so one ongoing problem is never announced twice (reported 2026-11-25:
        # "works again" / "broke" every few seconds while Claude came and went).
        keys = {k for k, _ in snap.health}
        for k, text in snap.health:
            self.health_since.setdefault(k, now)
            self.health_text[k] = text
            self.health_gone.pop(k, None)
        for k in list(self.health_since):
            if k in keys:
                continue
            gone = self.health_gone.setdefault(k, now)
            if k in self.health_ok or k not in self.health_said or (k != "sidebar" and now - gone >= HEALTH_RECOVER):
                del self.health_since[k]
                self.health_gone.pop(k, None)
                if k in self.health_said:
                    self.health_said.discard(k)
                    self.log("health: %s works again" % k)
            else:
                snap.health.append((k, self.health_text[k]))      # still broken as far as we know
                keys.add(k)
        snap.health = [(k, text) for k, text in snap.health if now - self.health_since[k] >= HEALTH_AFTER]
        for k, text in snap.health:
            if k not in self.health_said:
                self.health_said.add(k)
                self.log("health: %s broke: %s" % (k, text))
        self.health_said &= keys
        snap.cards = [model.Card("Claude mini", model.BROKEN, text, key=("health", k)) for k, text in snap.health] + \
            snap.cards
        key = snap.key()
        with self.lock:
            self.latest = snap
        if key != self.last_key:
            self.last_key = key
            self.notify()

    def _select(self, title):
        end = time.time() + 2.0
        while True:                          # Claude may still be opening (launched from the tray)
            hwnd, state = claude_window_state()
            if state == "visible" or time.time() > end:
                break
            time.sleep(0.1)
        if state != "visible":
            return False
        if self.uia is None:
            self.uia = UiaReader()
        # `title` is a sidebar row's title, or a notification's title ("<folder> · <session>") for news the blob
        # couldn't tie to a row yet (e.g. it started while Claude was on the chat tab).
        return self.uia.select_session(hwnd, lambda name: toast_matches(title, name))

    def _recent_cloud(self, cloud, now):
        """The cloud sessions to list: those active (working, needing you, news) or active within the last
        CLOUD_SHOWN_S. Their rows carry no time, so activity is what the blob saw itself: being active, or finishing
        (active, then not). One already finished when first seen is old news (when it finished isn't known) and isn't
        listed. A change between finished states ("Cloud" -> "Cloud · Merged" as Claude redraws a row) isn't activity:
        reported 2026-11-11, such flickers kept week-old merged sessions on the list."""
        active = (NEEDS, NEW, WORKING)
        out = []
        for title, state, cls in cloud:
            seen = self.cloud_seen.get(title)
            if cls in active or (seen is not None and seen[2] in active):
                last = now
            else:
                last = seen[1] if seen is not None else None
            self.cloud_seen[title] = (state, last, cls)
            if last is not None and now - last < CLOUD_SHOWN_S:
                out.append((title, state, cls))
        return out[:MAX_CLOUD]

    def _acknowledge(self):
        # You opened Claude: toasts that belong to no known sidebar session have nothing else to clear them.
        for k, it in list(self.items.items()):
            if it.row_title is None:
                self.consumed.add(k)
                del self.items[k]
        return True

    # ---------- one reading ----------
    def _poll(self):
        snap = model.Snapshot()
        now = time.time()
        hwnd, state = claude_window_state()
        if state == "not running":
            snap.claude_running = False
            self.items.clear()
            self.baseline = None             # re-baseline on restart so old toasts don't light the blob
            self.last_rows = None
            self.code_rows = None
            self.usage = None
            self.last_open = set()
            self.unreadable_since = None
            return snap

        toasts = self._read_toasts(snap)
        reading = None
        if state == "visible":
            try:
                if self.uia is None:
                    self.uia = UiaReader()
                reading = self.uia.read(hwnd)
            except com.ComError as e:
                self.uia = None
                snap.warnings.append("Can't read Claude's window (%s)" % e)   # shown only if it persists

        # ----- plan usage: refreshed after every reply by the get_usage Stop hook (usage.py) -----
        try:
            if self.transcripts is None:
                self.transcripts = TranscriptUsage()
            hooked = self.transcripts.poll(now)
            snap.contexts = self.transcripts.contexts(now)
            activity = self.transcripts.activity()
            limit_stops = self.transcripts.limit_stops()
            working = self.transcripts.working(now)
            local = self.transcripts.local_titles()
            transcripts_read = self.transcripts.ok
        except Exception:
            traceback.print_exc()
            hooked, activity, limit_stops, working, local = None, [], {}, set(), set()
            transcripts_read = False
        if hooked is not None and (self.usage is None or hooked.seen_at > self.usage.seen_at):
            self.usage = hooked
            self.hook_at = hooked.seen_at
        # Claude's own samples (every ~15 minutes while it runs, whether or not a session replies): newer than the
        # hook's reading, they bring the 5-hour and weekly figures up to date. Reported 2026-11-04: with no reply
        # finishing for a while, the 5-hour figure stayed at 70% while Claude was at 91%.
        try:
            if self.history is None:
                self.history = PlanHistory()
            sample = self.history.poll(now)
        except Exception:
            traceback.print_exc()
            sample = None
        if sample is not None and sample[0] > max(self.sample_at, self.hook_at) and now - sample[0] <= SAMPLE_FRESH_S:
            self.sample_at = sample[0]
            self.last_reset.update(passed_resets(self.usage, sample[0]))
            self.usage = merge_sample(self.usage, sample[0], sample[1])
            self.log("Claude's usage sample: %s" % ", ".join("%s %d%%" % kv for kv in sorted(sample[1].items())))
        # Claude's own ring only refreshes when you open it. So its figure counts as new when its percentage changes
        # (its "resets in" countdown ticks on its own), and otherwise only stands in until the hook has run.
        ring = parse_usage(reading.usage, now) if reading is not None and reading.usage else None
        if ring is not None:
            if ring.pct != self.ring_pct:
                self.ring_since = now
            if (self.ring_pct is not None and ring.pct != self.ring_pct) or \
                    ring_is_ahead(self.usage, ring, self.ring_since, self.last_reset, now):
                self.usage = merge_ring(self.usage, ring)
            elif self.usage is None:
                self.usage = ring
            self.ring_pct = ring.pct
        self.last_reset.update(passed_resets(self.usage, now))
        # A limit whose reset time has passed is empty again (any of them, not just the fullest); the next reply's
        # reading brings its new reset time.
        snap.usage = self.usage.live(now) if self.usage is not None else None

        # ----- sidebar: fresh, or last good reading -----
        chat_tab = reading is not None and reading.mode_found and reading.code_selected is False
        fresh = reading is not None and reading.complete and (
            bool(reading.rows) or (chat_tab and reading.sidebar_found))
        # Claude's Code tab always lists your sessions: showing its sidebar but no row we can read means its layout
        # changed (an update). Not a hidden sidebar: say so loudly, and carry on from the transcripts.
        layout_broken = reading is not None and reading.complete and reading.sidebar_found and not chat_tab and \
            not reading.rows and bool(local)
        self.health_ok = {"sidebar"} if fresh and not chat_tab else set()      # the sidebar's rows were just read
        if reading is not None and reading.complete and reading.mode_found:
            self.health_ok.add("mode")                                         # the Claude | Code switch is there
        if fresh:
            cloud = "; ".join("%s=%s/%s" % (r.title[:28], r.status, r.env_state) for r in reading.rows if r.env)
            if cloud != getattr(self, "_last_cloud", None):
                self._last_cloud = cloud
                self.log("cloud rows: " + (cloud or "-"))      # status icon / caption state, as Claude shows them
            for r in reading.rows:
                r.status = cloud_status(r)
            if chat_tab:
                self.last_rows = with_transcript_turns(chat_tab_rows(reading.rows, self.code_rows, reading.aggregate),
                                                       working, local, demote=not reading.aggregate)
            else:
                self.code_rows = reading.rows
                self.last_rows = with_transcript_turns(reading.rows, working, local, demote=False)
            self.unreadable_since = None
        else:
            if self.unreadable_since is None:
                self.unreadable_since = now
            # The sidebar can't be read (Claude closed to the tray, minimized, the sidebar hidden, a changed layout):
            # the last reading, kept current by the transcripts.
            self.last_rows = with_transcript_turns(self.last_rows, working, local, demote=True)
        # Which sessions are open in Claude's panes comes from their headers, not the sidebar: taken from every complete
        # reading, rows readable or not (reported 2026-11-25: with Claude 6's sidebar unreadable, the open list froze
        # empty and a permission card for the session on screen kept saying it needed you). On the chat tab only what
        # it lists can be open in front of you: a Code session's pane isn't on screen there (reported 2026-11-04).
        if reading is not None and reading.complete:
            listed = {_norm(r.title) for r in reading.rows}            # matched like titles elsewhere (case, spacing)
            self.last_open = {t for t in reading.open_titles if _norm(t) in listed} if chat_tab else reading.open_titles
            self.open_at = now
        elif now - self.open_at > OPEN_STALE_S:
            self.last_open = set()           # not read for a while: don't keep leaving sessions out on old news
        rows = self.last_rows
        snap.cloud = self._recent_cloud(cloud_list(rows), now)
        # Open sessions drop out only while you can actually see them (Claude is the window you're using, or most of
        # it is uncovered), as Claude's own "N sessions need input" pill assumes. Behind another app, minimized or
        # closed to the tray, they count like any other (e.g. an open session auto-continuing while you're in Chrome).
        in_view = state == "visible" and claude_in_view(hwnd)
        open_titles = self.last_open if in_view else set()
        open_norm = {_norm(t) for t in open_titles}     # a pane header and a sidebar row may differ in case or spacing
        diag = "rows: %s | open: %s | in view: %s | fresh: %s" % (
            ", ".join("%s=%s%s" % (r.title[:28], r.status, "~" if hasattr(r, "cap") else "") for r in (rows or ())
                      if row_class(r) in (NEEDS, NEW, WORKING)) or "-",
            sorted(t[:28] for t in self.last_open), in_view, fresh)
        if diag != getattr(self, "_last_diag", None):
            self._last_diag = diag
            self.log("read " + diag)

        health = []
        if layout_broken:
            health.append(("sidebar", "Can't read Claude's sidebar: an update changed it. Working, finished and "
                                      "needs-you still show (from Claude Code's records and Claude's notifications); "
                                      "unread and cloud sessions are paused."))
        elif reading is not None and reading.complete and reading.sidebar_found and not reading.mode_found:
            health.append(("mode", "Can't find Claude's Claude | Code switch: an update changed it. Telling the chat "
                                   "tab from the Code tab is paused; everything else still works."))
        if reading is not None and reading.usage and ring is None and \
                not reading.usage[len("Usage:"):].strip().lower().startswith("context"):
            health.append(("ring", "Can't read Claude's usage ring. Usage still comes from your hook and Claude's "
                                   "own samples. It says: \"%s\"" % reading.usage[:60]))
        if self.transcripts is not None and not self.transcripts.ok:
            health.append(("transcripts", "Can't read Claude Code's transcripts. Working and finished from them, "
                                          "context and the usage hook are paused; Claude's screen and notifications "
                                          "still work."))
        elif self.transcripts is not None and not self.transcripts.records_ok:
            health.append(("records", "Can't read Claude's session records. Rewound sessions may show twice and "
                                      "archived ones aren't left out; everything else still works."))
        # The usage hook runs when a reply finishes and writes its reading with it. Replies that finished with no
        # reading are proof it stopped; a quiet spell isn't (reported 2026-11-19: after a break, one compaction, which
        # isn't a reply, was taken for "usage stopped updating").
        missed = self.transcripts.turn_ends_after(hooked.seen_at + 60) \
            if self.transcripts is not None and hooked is not None else []
        if len(missed) >= HOOK_MISSED and now - missed[0] >= HOOK_SLACK_S:
            health.append(("usage", "Your usage hook stopped: %d replies finished since %s without a usage "
                                    "reading. Claude's ring still updates the figures when you open it." %
                           (len(missed), time.strftime("%H:%M", time.localtime(hooked.seen_at)))))
        if sample is not None and now - sample[0] > 6 * 3600 and not getattr(self, "_said_old_sample", False):
            self._said_old_sample = True        # not a failure (Claude never promised that file): a note in the log
            self.log("note: Claude's own usage samples stop at %s" % time.strftime("%d-%m %H:%M",
                                                                                  time.localtime(sample[0])))
        # What this reading shows working again (a problem stops being said on evidence, not just by going unseen).
        if reading is not None and reading.usage and (ring is not None or reading.usage[len("Usage:"):].strip().lower()
                                                      .startswith("context")):
            self.health_ok.add("ring")
        if not missed:
            self.health_ok.add("usage")      # turn ends only accumulate: none missed means a newer reading came
        if self.transcripts is not None and self.transcripts.ok:
            self.health_ok.add("transcripts")
        if self.transcripts is not None and self.transcripts.records_ok:
            self.health_ok.add("records")
        snap.health = health

        if not fresh and not layout_broken and not self.expanded and now >= self.quiet_until:
            if state == "hidden":
                why = "Claude's window is closed (still running in the tray)"
            else:
                why = "Claude's sidebar is hidden or the window is too narrow"
            snap.warnings.append(why + ("; showing the last reading plus notifications" if rows is not None
                                         else "; showing notifications only"))

        # ----- toasts -> sidebar rows -----
        if toasts is not None:
            if self.baseline is None:
                self.baseline = set(toasts)
            for nid, t in toasts.items():
                if nid not in self.baseline and nid not in self.consumed and nid not in self.items:
                    self.items[nid] = _Item(t)
            for nid in list(self.items):
                if nid not in toasts:
                    del self.items[nid]      # dismissed in Action Center, or replaced by Claude
        # A session that has sent Claude another request since asking for you got its answer (permission given or
        # refused, question answered), whatever the sidebar or a lost toast match says.
        for nid, it in list(self.items.items()):
            if it.kind == NEEDS and any(at > it.toast.created + MOVED_ON_SLACK and toast_matches(it.toast.title, t)
                                        for t, at in activity):
                self.log("answered: %s | %s" % (it.toast.title, it.toast.body[:60]))
                self.consumed.add(nid)
                del self.items[nid]
        toasted = {}                         # id(row) -> strongest live toast kind for it (titles can repeat)
        by_row = {}                          # id(row) -> live toast items, newest first
        for nid, it in sorted(self.items.items(), key=lambda kv: -kv[1].toast.created):
            row = best_row(rows or (), it)
            if row is None:
                continue
            it.row_title = row.title
            cls = row_class(row)
            if it.status_then is None:
                it.status_then = row.status
            if cls == it.kind:
                it.caught_up = True          # the sidebar now shows this news itself
            # Claude's sidebar can lag far behind its notifications while Claude is out of view (it redraws slowly
            # in the background: seen 16 s+). So a row that simply hasn't changed since the toast arrived is stale,
            # not proof the news is over. The toast is dropped only once the row has caught up with it and then
            # cleared (answered / opened), or has visibly moved on to something other than working.
            # A session the chat tab doesn't list: once you're looking at Claude (so it isn't lagging) and the Code
            # toggle's summary no longer shows anything that urgent, the news is over.
            if fresh and getattr(row, "cap", None) is not None and in_view and now - it.seen_at >= GRACE and \
                    _RANK.get(it.kind, 0) > _RANK.get(row.cap, 0):
                self.consumed.add(nid)
                del self.items[nid]
                continue
            if fresh and not hasattr(row, "cap") and cls not in (NEEDS, NEW) and (
                    it.caught_up or (row.status != it.status_then and cls != WORKING)):
                self.consumed.add(nid)       # the app's own indicator cleared
                del self.items[nid]
                continue
            # News for a session you have open in front of you: Claude never marks it unread (you're looking at it),
            # so there's no indicator to clear. Seeing it for a few seconds is reading it.
            if it.kind == NEW and _norm(row.title) in open_norm:
                if it.viewed_since is None:
                    it.viewed_since = now
                if now - it.viewed_since >= GRACE:
                    self.consumed.add(nid)
                    del self.items[nid]
                    continue
            else:
                it.viewed_since = None
            if toasted.get(id(row)) != NEEDS:
                toasted[id(row)] = it.kind
            by_row.setdefault(id(row), []).append(it)

        # ----- count sidebar sessions -----
        needs, unknown, cards = [], set(), []
        # A session stopped at the usage limit: Claude labels it with its generic "Something went wrong". Paused, not a
        # problem, both while a plan limit is full and after the reset if it hasn't carried on: its transcript ends
        # with the refused request (it may resume by itself, or wait for your next message).
        full = [w for w in (snap.usage.rows() if snap.usage is not None else ()) if w[1] >= 100]
        resumes = min((w[2] for w in full if w[2] is not None), default=None)
        for r in rows or ():
            cls = row_class(r)
            stop = limit_stops.get(r.title)
            # Reported 2026-11-04: at the weekly limit every turn stopped, but a row still said "Running" (Claude out
            # of view redraws its sidebar late, and background tasks keep retrying) and the critter kept working.
            # Its transcript ends with the refused request: paused until the limit resets.
            stopped = stop is not None and (full or (stop["resets_at"] or 0) > now)
            if cls == WORKING and stopped:
                snap.paused += 1
                cards.append(model.Card(r.title, model.PAUSED, paused_detail(stop, resumes, now), r.title))
                continue
            if cls == NEEDS and (full or stop) and stopped_at_limit(r, by_row.get(id(r), [])):
                snap.paused += 1
                cards.append(model.Card(r.title, model.PAUSED, paused_detail(stop, resumes, now), r.title))
                continue
            if _norm(r.title) in open_norm:
                if cls == NEEDS or id(r) in toasted:
                    snap.open_needs.append(r.title)
                continue                     # open in Claude right now: you're looking at it
            # A live toast outranks a row that hasn't caught up yet (or a stale reading while the panel is open);
            # it's only dropped once a fresh reading shows the session handled (see above).
            tk = toasted.get(id(r))
            if tk == NEEDS:
                cls = NEEDS
            elif tk == NEW and cls not in (NEEDS, NEW):
                cls = NEW
            its = by_row.get(id(r), [])
            if cls == NEEDS:
                snap.needs_me += 1
                needs.append((id(r) in toasted, r.title))
                it = next((i for i in its if i.kind == NEEDS), None)
                if it is not None:
                    kind, detail = card_content(it.toast.body, True)
                    cards.append(model.Card(r.title, kind, detail, r.title, when=it.toast.created,
                                            toast_id=it.toast.id))
                elif is_problem(r.status):
                    cards.append(model.Card(r.title, model.PROBLEM, r.status, r.title))
                else:
                    cards.append(model.Card(r.title, model.WAITING, r.status, r.title))
            elif cls == NEW:
                snap.finished += 1
                it = its[0] if its else None
                detail = it.toast.body if it is not None else r.status
                cards.append(model.Card(r.title, model.FINISHED, " ".join(detail.split()), r.title,
                                        when=it.toast.created if it is not None else 0.0,
                                        toast_id=it.toast.id if it is not None else None))
            elif cls == WORKING:
                snap.working += 1
            elif cls is None:
                unknown.add(r.status)
        # sessions with a fresh toast first: those are the ones that just asked for you
        snap.needs_titles = [t for _, t in sorted(needs, key=lambda x: not x[0])]

        # On the chat tab, the Code toggle's summary ("Code, awaiting your input") covers sessions it doesn't list.
        if reading is not None and reading.mode_found and reading.code_selected is False:
            agg = classify_status(reading.aggregate) if reading.aggregate else None
            if agg == NEEDS and snap.needs_me == 0:
                snap.needs_me = 1
                cards.append(model.Card("Claude Code", model.WAITING, "A session is awaiting your input"))
            elif agg == NEW and snap.finished == 0:
                snap.finished = 1
                cards.append(model.Card("Claude Code", model.FINISHED, "A session has unread activity"))
            elif agg == WORKING and snap.working == 0:
                snap.working = 1

        # ----- toasts for sessions the sidebar doesn't list -----
        unmatched = {}                       # one entry per thread: its most urgent, then newest, toast
        for it in sorted(self.items.values(), key=lambda i: -i.toast.created):
            if it.row_title is None:
                key = _norm(it.toast.title)
                cur = unmatched.get(key)
                if cur is None or (it.kind == NEEDS and cur.kind != NEEDS):
                    unmatched[key] = it
        for it in unmatched.values():
            # Its session is open in front of you (no sidebar row to say so, e.g. while the rows can't be read):
            # you're looking at it, as for a row.
            seen = next((t for t in open_titles if toast_matches(it.toast.title, t)), None)
            if seen is not None:
                if it.kind == NEEDS:
                    if _norm(seen) not in {_norm(t) for t in snap.open_needs}:     # once, however it's spelled
                        snap.open_needs.append(seen)
                    continue
                # News for it: seeing it for a few seconds is reading it, as for a session with a row.
                if it.viewed_since is None:
                    it.viewed_since = now
                if now - it.viewed_since >= GRACE:
                    for nid, other in list(self.items.items()):
                        if other.row_title is None and other.kind != NEEDS and _norm(other.toast.title) == \
                                _norm(it.toast.title):
                            self.consumed.add(nid)
                            del self.items[nid]
                continue
            it.viewed_since = None
            kind, detail = card_content(it.toast.body, False)
            stop = next((s for t, s in limit_stops.items() if toast_matches(it.toast.title, t)), None)
            if (full or stop) and kind in (model.WAITING, model.PROBLEM):
                snap.paused += 1                 # the generic "waiting for your input" of a session at the limit
                cards.append(model.Card(it.toast.title, model.PAUSED, paused_detail(stop, resumes, now),
                                        it.toast.title, when=it.toast.created, toast_id=it.toast.id))
                continue
            if it.kind == NEEDS:
                snap.needs_me += 1
            else:
                snap.new += 1
            cards.append(model.Card(it.toast.title, kind, detail, it.toast.title, when=it.toast.created,
                                    toast_id=it.toast.id))

        cards.sort(key=lambda c: (not c.urgent, -c.when))
        snap.cards = cards
        # Nothing needs you, works or is new, and every session's prompt cache is gone (the Context list is empty): your
        # next message anywhere is read in full again. The critter dozes until something happens (asked 2026-11-25).
        # Sessions open on screen aren't counted above, but one waiting on you, or working (its row, or its transcript
        # with a turn under way), still keeps it awake. And an empty Context list is evidence only if the transcripts
        # could be read.
        open_working = any(row_class(r) == WORKING and _norm(r.title) in open_norm for r in rows or ())
        snap.asleep = transcripts_read and not snap.contexts and not (
            snap.needs_me or snap.finished or snap.new or snap.working or snap.open_needs or working or open_working)
        for u in sorted(unknown):
            snap.warnings.append("Unrecognised session status: %s" % u)
        return snap

    def _read_toasts(self, snap):
        try:
            if self.toasts is None:
                self.toasts = NotificationReader()
            now = time.time()
            if now - self.access_checked > 30:
                self.access_ok = self.toasts.access_status() == ALLOWED
                self.access_checked = now
            if not self.access_ok:
                snap.warnings.append("Notification access is off: Settings › Privacy & security › Notifications")
                return None
            return self.toasts.read()
        except com.ComError as e:
            self.toasts = None
            snap.warnings.append("Can't read Windows notifications (%s)" % e)
            return None
