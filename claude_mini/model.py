"""Blob state model. Priority (highest wins): NEEDS_ME > NEW > WORKING > IDLE; UNAVAILABLE when Claude isn't running."""

UNAVAILABLE, IDLE, WORKING, NEW, NEEDS_ME = 0, 1, 2, 3, 4

# card kinds
PERMISSION, PLAN, QUESTION, PROBLEM, WAITING, FINISHED, UPDATE, PAUSED, RESTARTED, BROKEN = (
    "permission", "plan", "question", "problem", "waiting", "finished", "update", "paused", "restarted", "broken")
HEADLINES = {PERMISSION: "Needs your permission", PLAN: "Plan ready for your review", QUESTION: "Has a question",
             PROBLEM: "Ran into a problem", WAITING: "Waiting for you", FINISHED: "Finished", UPDATE: "Update",
             PAUSED: "Paused at the usage limit", RESTARTED: "Restarted after it stopped",
             BROKEN: "Claude changed something"}
URGENT = {PERMISSION, PLAN, QUESTION, PROBLEM, WAITING}


class Card(object):
    """One session's latest news, in Claude's own words."""
    __slots__ = ("title", "kind", "detail", "session", "key", "when", "toast_id")

    def __init__(self, title, kind, detail, session=None, key=None, when=0.0, toast_id=None):
        self.title = title          # session (or thread) name
        self.kind = kind
        self.detail = detail        # Claude's own text: the question, the command, the summary...
        self.session = session      # sidebar row title to open, if known
        self.key = key or (title, kind, detail)
        self.when = when
        self.toast_id = toast_id    # the Claude notification this card came from, if any

    @property
    def headline(self):
        return HEADLINES[self.kind]

    @property
    def urgent(self):
        return self.kind in URGENT

    def ident(self):
        return (self.title, self.kind, self.detail, self.session)


class Usage(object):
    """Plan usage: the fullest limit (e.g. 43% of the 5-hour limit, resetting at a time), plus the other limits."""
    __slots__ = ("pct", "limit", "resets_at", "seen_at", "others")

    def __init__(self, pct, limit, resets_at, seen_at, others=()):
        self.pct = pct              # 0..100 (can exceed 100 with extra usage)
        self.limit = limit          # "5-hour limit", "Weekly · all models", "Session limit", ...
        self.resets_at = resets_at  # epoch seconds, or None if Claude didn't say
        self.seen_at = seen_at      # when this reading was taken
        self.others = list(others)  # [(label, pct, resets_at)] for the other limits

    def ident(self):
        return (self.pct, self.limit, None if self.resets_at is None else int(self.resets_at // 60),
                tuple((label, pct) for label, pct, _ in self.others))

    def live(self, now):
        """This reading as of `now`: every limit whose reset time has passed is back at 0% (next reset unknown until
        the next reading); the fullest limit is picked again."""
        wins = [(self.limit, self.pct, self.resets_at)] + list(self.others)
        if not any(r is not None and r <= now for _, _, r in wins):
            return self
        wins = [(lbl, 0, None) if (r is not None and r <= now) else (lbl, p, r) for lbl, p, r in wins]
        top = max(wins, key=lambda w: w[1])
        return Usage(top[1], top[0], top[2], self.seen_at, [w for w in wins if w is not top])

    def rows(self):
        """The limits worth listing, as (label, pct, resets_at): the 5-hour and the all-models weekly limit always,
        the per-model ones once something's used."""
        rows = [(self.limit, self.pct, self.resets_at)] + list(self.others)

        def order(row):
            label = row[0].lower()
            return 0 if ("5-hour" in label or "session" in label) else (1 if "all models" in label else 2)
        keep = [r for r in rows if order(r) < 2 or r[1] > 0 or r[0] == self.limit]
        return sorted(keep, key=order)

    def age_text(self, now):
        """ "as of 40 min ago" once the reading is old enough to say so, else "". """
        age = now - self.seen_at
        return "as of %s ago" % duration_text(age) if age >= STALE_USAGE_S else ""

    def line(self, now):
        """e.g. "Session limit: 43% used · resets in 1 hr 1 min", noting when an old reading was taken."""
        out = "%s: %d%% used" % (self.limit, self.pct)
        if self.resets_at is not None:
            out += " · resets in " + duration_text(self.resets_at - now)
        for label, pct, _ in self.others:
            if pct > 0 or "all models" in label.lower():
                out += " · %s %d%%" % (label, pct)
        age = now - self.seen_at
        if age >= STALE_USAGE_S:
            out += " (as of %s ago)" % duration_text(age)
        return out


# Usage only grows while Claude works, and every reply refreshes the reading (see usage.py), so an old reading is
# usually still right. Only use elsewhere (claude.ai, the phone app) moves it unseen: after this long it's shown
# faded, with its age.
STALE_USAGE_S = 1800


def duration_text(secs):
    m = max(0, int(secs // 60))
    if m >= 24 * 60:
        d, h = m // 1440, (m % 1440) // 60
        days = "%d day%s" % (d, "" if d == 1 else "s")
        return days if (h == 0 or d >= 3) else "%s %d hr" % (days, h)
    if m >= 60:
        return "%d hr %d min" % (m // 60, m % 60) if m % 60 else "%d hr" % (m // 60)
    return "%d min" % max(1, m)


class Context(object):
    """One session with a warm prompt cache: how full its context window is, and until when its cache lives."""
    __slots__ = ("title", "pct", "tokens", "window", "compact_at", "seen_at", "cache_until", "cache_ok", "compacted")

    def __init__(self, title, pct, tokens, window, compact_at, seen_at, cache_until=None, cache_ok=None,
                 compacted=False):
        self.title, self.pct, self.tokens, self.window = title, pct, tokens, window   # pct None: window unknown
        self.compact_at = compact_at    # % at which Claude Code compacts it on its own
        self.seen_at = seen_at          # its last request
        self.cache_until = cache_until  # when its prompt cache expires (each request renews it); None: can't tell
        self.cache_ok = cache_ok        # False: the timer disagreed with the API's own cache reads last time
        self.compacted = compacted      # compacted since its last reply: its next request starts a new cache


class Snapshot(object):
    def __init__(self, claude_running=True):
        self.claude_running = claude_running
        self.needs_me = 0
        self.finished = 0          # sessions showing "Unread response" (blue dot)
        self.new = 0               # other Claude news (notifications not tied to a visible session row)
        self.working = 0
        self.warnings = []         # why the reading is incomplete; never fail silently
        self.needs_titles = []     # session titles waiting on me, most urgent first
        self.cards = []            # Card list: urgent first, newest first
        self.open_needs = []       # sessions waiting on you that are open in Claude right now (not counted)
        self.paused = 0            # sessions stopped at a full plan limit, resuming by themselves after the reset
        self.usage = None          # Usage, while Claude has shown one and it hasn't reset since
        self.contexts = []         # Context of the sessions that replied lately, newest first
        self.cloud = []            # (title, state shown, class: needs / new / working / idle) of cloud sessions
        self.health = []           # (key, what stopped working and what still works): a source of the reading broke

    @property
    def level(self):
        if not self.claude_running:
            return UNAVAILABLE
        if self.needs_me:
            return NEEDS_ME
        if self.finished or self.new:
            return NEW
        if self.working:
            return WORKING
        return IDLE

    @property
    def degraded(self):
        return (not self.claude_running) or bool(self.warnings) or bool(self.health)

    @property
    def dimmed(self):
        # Dim when there's nothing to show and we can't vouch for that.
        return self.level == UNAVAILABLE or (self.level == IDLE and self.degraded)

    def summary(self):
        if not self.claude_running:
            head = "Claude isn't running"
        else:
            parts = []
            if self.needs_me:
                parts.append("%d need%s you" % (self.needs_me, "s" if self.needs_me == 1 else ""))
            if self.finished:
                parts.append("%d finished" % self.finished)
            if self.new:
                parts.append("%d new" % self.new)
            if self.working:
                parts.append("%d working" % self.working)
            if self.paused:
                parts.append("%d paused at the limit" % self.paused)
            head = " · ".join(parts) if parts else "All quiet"
        lines = [head] + ["⚠ " + w for w in self.warnings]
        return "\n".join(lines)

    def key(self):
        return (self.level, self.needs_me, self.finished, self.new, self.working, self.paused, tuple(self.warnings),
                tuple(self.needs_titles), self.claude_running, tuple(c.ident() for c in self.cards),
                self.usage.ident() if self.usage else None, tuple((c.title, c.pct, c.cache_until and int(c.cache_until // 60), c.cache_ok, c.compacted) for c in self.contexts),
                tuple(self.cloud), tuple(self.health))


def starting():
    s = Snapshot()
    s.warnings.append("Reading Claude's status…")
    return s
