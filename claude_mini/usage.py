"""Plan usage, refreshed after every Claude Code reply at no cost to your usage.

Hooks in ~/.claude/settings.json (type "mcp_tool": the desktop app's own get_usage tool, no model involved) run
after every reply in every desktop session (Stop), when a reply fails, e.g. on hitting the limit (StopFailure), and
whenever a sub-agent finishes (SubagentStop), so a long workflow reply keeps it fresh too. Claude Code records its result in that session's transcript
(~/.claude/projects/<project>/<session>.jsonl) as a "hook_success" attachment, and does not feed it back to the
model. This module only reads those transcripts: it never writes anything.
"""
import json
import os
import re
import time
from datetime import datetime

from . import model

PROJECTS = os.path.join(os.path.expanduser("~"), ".claude", "projects")
# The desktop app's own record of each sidebar session (<profile>/claude-code-sessions/.../local_<id>.json), which
# lists every transcript the session has used. Only read.
RECORDS = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Claude-Data")
TAIL_BYTES = 256 * 1024     # the newest usage record is near the end of a transcript
MAX_TAIL_BYTES = 16 * 1024 * 1024  # how far back to look for a session's last request
SCAN_EVERY = 2.0            # s between looks at the transcripts folder
ACTIVE_S = 6 * 3600         # a session written to this recently may have sub-agents worth looking at
STOPPED_S = 7 * 86400       # ... and this recently, for a session still stopped at the usage limit
TURN_QUIET_S = 900          # a turn whose transcript hasn't been written for this long isn't taken for working
BG_FIRST_BYTES = 4 * 1024 * 1024   # a transcript first seen: how much of its end to look through for background tasks
BG_MAX_READ = 8 * 1024 * 1024      # ... and at most this much of what was added since, per look
BG_DONE_SLACK = 10.0        # a sub-agent's transcript written this long after its completion notice: it was resumed
_BG_OUTPUT = re.compile(r"Output is being written to: (.+?\.output)")
_BG_NOTICE = re.compile(r"<task-id>([^<]+)</task-id>")
MAX_TTL = 3600              # the longest a prompt cache lives (the 1-hour cache)
CHECK_SLACK = 60            # gaps this close to the TTL are too close to call for the cache check
MAX_CONTEXTS = 5
TITLE_BYTES = 64 * 1024     # how far past the first message a session's title may come
HEAD_BYTES = 8 * 1024 * 1024  # how far into a transcript to look for its title and first message


def _json(raw):
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _text_of(content):
    """The plain text in a message's content (a string, or blocks holding text or tool results)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(_text_of(b.get("content") if b.get("type") == "tool_result" else b.get("text"))
                         for b in content if isinstance(b, dict))
    return ""


def _epoch(iso):
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def find_plan(v, depth=0):
    """The {"windows": [...]} part of a get_usage result, whatever it's wrapped in (JSON text, lists, objects)."""
    if depth > 6 or v is None:
        return None
    if isinstance(v, str):
        i = v.find("{")
        if i < 0:
            return None
        try:
            return find_plan(json.loads(v[i:]), depth + 1)
        except ValueError:
            return None
    if isinstance(v, list):
        for x in v:
            r = find_plan(x, depth + 1)
            if r is not None:
                return r
        return None
    if isinstance(v, dict):
        if isinstance(v.get("windows"), list):
            return v
        for x in v.values():
            r = find_plan(x, depth + 1)
            if r is not None:
                return r
    return None


def usage_from_plan(plan, seen_at):
    """model.Usage from get_usage's plan: the meter follows the fullest window; the others ride along."""
    if not plan or plan.get("status", "ok") != "ok":
        return None
    wins = []
    for w in plan.get("windows") or ():
        try:
            pct = int(round(float(w.get("percentUsed"))))
        except (TypeError, ValueError):
            continue
        wins.append((str(w.get("label") or "Limit"), pct, _epoch(w.get("resetsAt"))))
    if not wins:
        return None
    # A window whose reset has passed is empty again.
    wins = [(lbl, 0 if r is not None and r <= time.time() else p, r) for lbl, p, r in wins]
    top = max(wins, key=lambda w: w[1])
    others = [w for w in wins if w is not top]
    return model.Usage(top[1], top[0], top[2], seen_at, others)


def context_from_record(line):
    """model.Context from a get_usage hook record: how full that session's context window was."""
    try:
        o = json.loads(line)
        out = json.loads(o["attachment"]["stdout"]) if isinstance(o["attachment"]["stdout"], str) else {}
    except (ValueError, KeyError, TypeError):
        return None
    c = out.get("context") if isinstance(out, dict) else None
    if not isinstance(c, dict) or c.get("status", "ok") != "ok" or c.get("percentUsed") is None:
        return None
    try:
        return model.Context(None, int(round(float(c["percentUsed"]))), int(c.get("tokensUsed") or 0),
                             int(c.get("contextWindow") or 0), c.get("autoCompactsAtPercent"),
                             _epoch(o.get("timestamp")) or time.time())
    except (TypeError, ValueError):
        return None


def context_command_tokens(line):
    """Tokens in context from a /context run recorded in the transcript ("**Tokens:** 90.1k / 1m (9%)"), or None."""
    o = _json(line)
    if not isinstance(o, dict) or o.get("type") != "system" or o.get("subtype") != "local_command":
        return None
    m = re.search(r"\*\*Tokens:\*\*\s*([\d.]+)\s*([km]?)\s*/", o.get("content") or "")
    if not m:
        return None
    return int(round(float(m.group(1)) * {"k": 1000, "m": 1000000}.get(m.group(2), 1)))


def request_from_line(line):
    """One main-thread request, from its assistant message's API usage: {at, tokens (the context it sent), read (tokens
    read from the prompt cache), ttl (the cache type it wrote: 3600 / 300 s, or None if it wrote none), model}."""
    try:
        o = json.loads(line)
    except ValueError:
        return None
    if not isinstance(o, dict) or o.get("type") != "assistant" or o.get("isSidechain") or o.get("isApiErrorMessage"):
        return None                         # an API error (e.g. the usage limit) never reached the model
    msg = o.get("message") or {}
    u = msg.get("usage")
    at = _epoch(o.get("timestamp"))
    if not isinstance(u, dict) or at is None:
        return None
    read = int(u.get("cache_read_input_tokens") or 0)
    tokens = read + int(u.get("input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0)
    cc = u.get("cache_creation") or {}
    ttl = 3600 if (cc.get("ephemeral_1h_input_tokens") or 0) > 0 else (
        300 if (cc.get("ephemeral_5m_input_tokens") or 0) > 0 else None)
    return {"at": at, "tokens": tokens, "read": read, "ttl": ttl, "model": msg.get("model")}


def limit_stop_from_line(line):
    """{at, text, resets_at} if this is the main thread's record of a request refused at the usage limit ("You've hit
    your session limit · resets 1:10am"), else None. resets_at: when that limit resets (None if not given)."""
    o = _json(line)
    if not isinstance(o, dict) or o.get("type") != "assistant" or o.get("isSidechain") or \
            not o.get("isApiErrorMessage") or o.get("error") != "rate_limit":
        return None
    info = (o.get("apiErrorParams") or {}).get("rate_limit_info") or o.get("quotaLimits") or {}
    try:
        resets_at = float(info["resetsAt"])
    except (KeyError, TypeError, ValueError):
        resets_at = None
    content = (o.get("message") or {}).get("content")
    text = " ".join(c.get("text", "") for c in content if isinstance(c, dict)) if isinstance(content, list) else ""
    return {"at": _epoch(o.get("timestamp")), "text": " ".join(text.split()), "resets_at": resets_at}


def cache_check(events):
    """Does our cache timer agree with what the API reported? `events`: requests (dicts) and "compact" marks, oldest
    first. For each pair of consecutive requests, the timer says the cache was warm at the second one if it came
    within the TTL of the first; the API says warm if most of its context was read from the cache. Pairs it can't
    judge are skipped: a compaction or model switch in between (those start a new cache), a context that shrank a
    lot, or a gap within CHECK_SLACK of the TTL. Returns the latest verdict, True / False, or None if none."""
    verdict, prev, ttl = None, None, None
    for ev in events:
        if ev == "compact":
            prev = None
            continue
        if prev is not None and ttl and ev["tokens"] > 0 and ev["model"] == prev["model"] and \
                ev["tokens"] >= 0.6 * prev["tokens"]:
            gap = ev["at"] - prev["at"]
            if abs(gap - ttl) > CHECK_SLACK:
                verdict = (gap < ttl) == (ev["read"] >= 0.5 * ev["tokens"])
        if ev["ttl"]:
            ttl = ev["ttl"]
        prev = ev
    return verdict


def usage_from_record(line):
    """model.Usage from one transcript line, if it's a get_usage hook result."""
    if '"hook_success"' not in line or "windows" not in line:
        return None
    try:
        o = json.loads(line)
    except ValueError:
        return None
    a = o.get("attachment") if isinstance(o, dict) else None
    if not isinstance(a, dict) or a.get("type") != "hook_success" or "get_usage" not in str(a.get("command", "")):
        return None
    return usage_from_plan(find_plan(a.get("stdout")), _epoch(o.get("timestamp")) or time.time())


HISTORY = "plan-usage-history.json"


class PlanHistory(object):
    """Claude's own record of your plan usage: the app fetches the 5-hour ("fh") and weekly ("sd") figures about every
    15 minutes while it runs, whether or not any session replies, and keeps them in
    <profile>/plan-usage-history.json ({"samples": [{"t": ms, "u": {"fh": 93, "sd": 100}}, ...]}). Only read."""

    def __init__(self, root=None):
        self.root = RECORDS if root is None else root
        self.seen = None
        self.latest = None          # (when, {"5-hour": pct, "weekly": pct}) of the newest sample
        self.next_scan = 0.0

    def poll(self, now=None):
        now = time.time() if now is None else now
        if now < self.next_scan:
            return self.latest
        self.next_scan = now + SCAN_EVERY
        newest = None
        try:
            for e in os.scandir(self.root):
                path = os.path.join(e.path, HISTORY)
                try:
                    mtime = os.stat(path).st_mtime
                except OSError:
                    continue
                if newest is None or mtime > newest[0]:
                    newest = (mtime, path)
        except OSError:
            return self.latest
        if newest is None or newest == self.seen:
            return self.latest
        try:
            with open(newest[1], encoding="utf-8") as fh:
                samples = json.load(fh).get("samples") or []
        except (OSError, ValueError, AttributeError):
            return self.latest              # caught mid-write: tried again next time
        self.seen = newest
        for s in sorted((s for s in samples if isinstance(s, dict)), key=lambda s: s.get("t") or 0, reverse=True):
            u = s.get("u") if isinstance(s.get("u"), dict) else {}
            try:
                self.latest = (float(s["t"]) / 1000.0, {"5-hour": int(u["fh"]), "weekly": int(u["sd"])})
                break
            except (KeyError, TypeError, ValueError):
                continue
        return self.latest


def window_kind(label):
    low = label.lower()
    return "5-hour" if ("5-hour" in low or "session" in low) else ("weekly" if "all models" in low else None)


WEEK_S = 7 * 86400


def merge_sample(usage, at, pcts):
    """`usage` with one of Claude's samples put in: {"5-hour": pct, "weekly": pct} as of `at`. A limit whose reset
    time had passed by then has reset: the weekly one resets at the same time a week on; the 5-hour one starts with
    your next use, so its reset time is unknown until the next full reading (or Claude's ring)."""
    wins = ([(usage.limit, usage.pct, usage.resets_at)] + list(usage.others)) if usage is not None else []
    out, hit = [], set()
    for label, pct, resets_at in wins:
        key = window_kind(label)
        if key in pcts and key not in hit:
            hit.add(key)
            if resets_at is not None and resets_at <= at:
                resets_at = resets_at + WEEK_S * (1 + int((at - resets_at) // WEEK_S)) if key == "weekly" else None
            out.append((label, pcts[key], resets_at))
        else:
            out.append((label, pct, resets_at))
    for key, label in (("5-hour", "5-hour limit"), ("weekly", "Weekly \u00b7 all models")):
        if key in pcts and key not in hit:
            out.append((label, pcts[key], None))
    top = max(out, key=lambda w: w[1])
    return model.Usage(top[1], top[0], top[2], at, [w for w in out if w is not top])


class TranscriptUsage(object):
    """Keeps the newest usage record found in any transcript, and each session's newest context reading. Only files
    that changed are read, and only their tail."""

    def __init__(self, root=PROJECTS, records=None):
        self.root = root
        self.records = RECORDS if records is None else records
        self.record_seen = {}       # session record path -> mtime last read
        self.owner = {}             # transcript id -> the sidebar session it belongs to
        self.archived = set()       # sidebar sessions (record names) archived in Claude
        self.seen = {}              # path -> (mtime, size) last read
        self.latest = None          # newest model.Usage
        self.sessions = {}          # main transcript path -> what we know of that session (see _read_tail)
        self.windows = {}           # model -> its context window size, as get_usage reported it for a session on it
        # Background work a session started (sub-agents, background commands), from its transcript: launched,
        # then a completion notice with the same task id. A session's turn can end while these still run.
        self.bg_read = {}           # main transcript path -> how far it was looked through for them
        self.bg = {}                # main transcript path -> {task id: {"kind", "at", "file"}}
        self.bg_done = {}           # task id -> when its newest completion notice came (any transcript: ids are unique)
        self.ok = True              # the transcripts folder could be read last time
        self.records_ok = True      # ... and Claude's session records
        self.next_scan = 0.0

    def poll(self, now=None):
        now = time.time() if now is None else now
        if now < self.next_scan:
            return self.latest
        self.next_scan = now + SCAN_EVERY
        self._read_records()
        try:
            projects = list(os.scandir(self.root))
            self.ok = True
        except OSError:
            self.ok = False
            return self.latest
        changed = []
        for proj in projects:
            if not proj.is_dir():
                continue
            try:
                entries = list(os.scandir(proj.path))
            except OSError:
                continue
            files, active = [], set()
            for e in entries:
                if e.name.endswith(".jsonl"):
                    files.append(e.path)
                    try:
                        if now - e.stat().st_mtime < ACTIVE_S:
                            active.add(e.name[:-len(".jsonl")])
                    except OSError:
                        pass
            # Sub-agents (workflows, Agent calls) keep their own transcripts under <session>/subagents: look in
            # those of sessions active lately (a SubagentStop hook's record may land there).
            for sid in active:
                sub = os.path.join(proj.path, sid, "subagents")
                try:
                    files.extend(e.path for e in os.scandir(sub) if e.name.endswith(".jsonl"))
                except OSError:
                    pass
            for path in files:
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                key = (st.st_mtime, st.st_size)
                if self.seen.get(path) != key:
                    self.seen[path] = key
                    changed.append((st.st_mtime, st.st_size, path))
        # Newest first. A file last written before the usage reading we already have can't hold a newer one, and only
        # sessions that replied lately get a context line; older ones (up to a week) are read once for a session that
        # stopped at the usage limit and was left there.
        for mtime, size, path in sorted(changed, reverse=True):
            recent = now - mtime < STOPPED_S
            if not recent and self.latest is not None and mtime < self.latest.seen_at:
                break
            u, ctx, act = self._read_tail(path, size)
            if u is not None and (self.latest is None or u.seen_at > self.latest.seen_at):
                self.latest = u
            if os.path.basename(os.path.dirname(path)) == "subagents":
                continue                    # a sub-agent's cache and context aren't the session's
            if now - mtime < ACTIVE_S:
                self._read_background(path, size)
            info = self.sessions.setdefault(path, {"title": None, "last_at": None, "ttl": None, "tokens": None,
                                                   "window": None, "hook_pct": None, "check": None, "mtime": None,
                                                   "model": None, "root": None, "compacted": False,
                                                   "limit_stop": None, "turn_open": False, "turn_ends": []})
            if info["root"] is None:
                head_title, info["root"] = self._read_head(path)
                if info["title"] is None and not act["title"]:
                    info["title"] = head_title
            if act["model"]:
                info["model"] = act["model"]
            info["mtime"] = mtime
            if act["check"] is not None:
                info["check"] = act["check"]
            if act["title"]:
                info["title"] = act["title"]
            elif info["title"] is None:
                info["title"] = "Untitled session"
            for k in ("last_at", "tokens", "ttl"):
                if act[k] is not None:
                    info[k] = act[k]
            info["compacted"] = act["compacted"]
            info["limit_stop"] = act["limit_stop"]
            info["turn_open"] = act["turn_open"]
            info["turn_ends"] = act["turn_ends"]
            if act["compacted"]:
                info["tokens"], info["hook_pct"] = act["tokens"], None    # the size before compacting is gone
            if ctx is not None:
                info["window"] = ctx.window or None
                info["hook_pct"] = None if act["compacted"] else ctx.pct   # a reading from before compacting is stale
                if ctx.window and info["model"]:
                    self.windows[info["model"]] = ctx.window   # what this model's window is, for sessions without one
        return self.latest

    def contexts(self, now=None):
        """Sessions whose prompt cache is still warm (newest first, at most MAX_CONTEXTS): their context and until when
        the cache lives. Each request keeps a session's cache for its TTL (1 hour, or 5 minutes). A session whose
        cache type or last request can't be read is listed with cache_until None ("cache ?") for up to the longest
        TTL, rather than dropped or guessed."""
        now = time.time() if now is None else now
        # Rewinding or editing a message makes Claude carry on in a new transcript; the old one is an abandoned branch
        # of the same sidebar session. Keep only the newest per sidebar session, as Claude's session records tell.
        # Without a record: per conversation and title, since the new transcript copies the first message and title
        # (a real fork copies the first message too, but Claude retitles it "(fork)"). After a compaction the copy
        # starts at the summary instead, so only the record ties it to the old one.
        newest = {}
        for path, info in self.sessions.items():
            owner = self.owner.get(os.path.basename(path)[:-len(".jsonl")])
            key = ("session", owner) if owner else (info["root"], info["title"]) if info["root"] else path
            at = info["last_at"] or info["mtime"] or 0
            if key not in newest or at > newest[key][0]:
                newest[key] = (at, info)
        out = []
        for key, (_, info) in newest.items():
            if key[0] == "session" and key[1] in self.archived:
                continue                    # archived: not coming back soon (it is listed again once unarchived and used)
            last = info["last_at"]
            if last is None and info["mtime"] is None:
                continue
            if last is not None and info["ttl"]:
                until = shown_until = last + info["ttl"]
            else:
                until, shown_until = None, (last if last is not None else info["mtime"]) + MAX_TTL
            if shown_until <= now:
                continue                    # cache gone: the session drops off
            pct = None
            # The window size comes from the session's own get_usage reading, else from another session on the same
            # model (sessions started before the hook never report it). Unknown: the context shows as tokens.
            window = info["window"] or self.windows.get(info["model"])
            if window and info["tokens"] is not None:
                pct = int(round(100.0 * info["tokens"] / window))
            elif info["hook_pct"] is not None:
                pct = info["hook_pct"]
            out.append(model.Context(info["title"], pct, info["tokens"], window, None,
                                     last if last is not None else info["mtime"], until, info["check"],
                                     info["compacted"]))
        return sorted(out, key=lambda c: -c.seen_at)[:MAX_CONTEXTS]

    def activity(self):
        """(title, time of its last request) for each session read so far."""
        return [(info["title"], info["last_at"]) for info in self.sessions.values() if info["last_at"]]

    def working(self, now=None):
        """Titles of sessions with a turn under way: a request was made since the turn's last end (its Stop hook,
        an interruption or a refused request), and the transcript was written within TURN_QUIET_S (a session that
        crashed mid-turn writes nothing more). Archived sessions aren't counted."""
        now = time.time() if now is None else now
        out = set()
        for path, info in self.sessions.items():
            owner = self.owner.get(os.path.basename(path)[:-len(".jsonl")])
            if info["turn_open"] and info["mtime"] and now - info["mtime"] < TURN_QUIET_S and \
                    owner not in self.archived:
                out.add(info["title"])
        return out

    def background(self, now=None):
        """Title -> how many background tasks (sub-agents, background commands) it started are still running: launched
        and no completion notice since (a sub-agent written to after its notice was resumed). One that hasn't written
        anything for TURN_QUIET_S doesn't count (it crashed, or waits on nothing we can see). Archived sessions aren't
        counted. Reported 2026-11-27: the critter celebrated while a session's checkers were still running."""
        now = time.time() if now is None else now
        out, gone = {}, []
        for path, tasks in self.bg.items():
            info = self.sessions.get(path)
            owner = self.owner.get(os.path.basename(path)[:-len(".jsonl")])
            # An archived or untitled session's tasks don't count, but are still forgotten in time (below).
            counts = bool(info and info["title"]) and owner not in self.archived
            n = 0
            for tid, t in tasks.items():
                try:
                    written = os.stat(t["file"]).st_mtime if t["file"] else None
                except OSError:
                    written = None
                done = self.bg_done.get(tid)
                if done is not None and done >= t["at"] and not (
                        t["kind"] == "agent" and written is not None and written > done + BG_DONE_SLACK):
                    if now - max(done, written or 0) > ACTIVE_S:
                        gone.append((tasks, tid))        # long over: forget it
                    continue
                quiet = now - max(t["at"], written or 0)
                if quiet < TURN_QUIET_S:
                    n += 1
                elif quiet > ACTIVE_S:
                    gone.append((tasks, tid))            # never got a notice (its session was killed): forget it
            if n and counts:
                out[info["title"]] = out.get(info["title"], 0) + n
        for tasks, tid in gone:
            tasks.pop(tid, None)
            self.bg_done.pop(tid, None)
        for path in [p for p, tasks in self.bg.items() if not tasks]:
            del self.bg[path]
        return out

    def _read_background(self, path, size):
        """Look through what was added to a main transcript since last time for background tasks it launched
        (a tool result carrying "backgroundTaskId", or a sub-agent's "async_launched") and completion notices (an
        enqueued "<task-notification>" naming the task id). Only the records' own fields count: a message that merely
        quotes one isn't taken for it."""
        start = self.bg_read.get(path)
        if start is None or start > size:
            start = max(0, size - BG_FIRST_BYTES)       # first look (or the file was rewritten): its recent part
        if size - start > BG_MAX_READ:
            start = size - BG_MAX_READ
        try:
            with open(path, "rb") as fh:
                fh.seek(start)
                data = fh.read(size - start)
        except OSError:
            return
        end = data.rfind(b"\n")
        if end < 0:
            return                                       # no complete line yet
        first = path not in self.bg_read or self.bg_read[path] != start
        self.bg_read[path] = start + end + 1
        lines = data[:end].split(b"\n")
        if first and start > 0:
            lines = lines[1:]                            # began mid-line
        tasks = self.bg.setdefault(path, {})
        sid_dir = path[:-len(".jsonl")]
        for line in lines:
            if b'"backgroundTaskId"' in line or b'"async_launched"' in line:
                o = _json(line.decode("utf-8", "replace"))
                r = o.get("toolUseResult") if isinstance(o, dict) and o.get("type") == "user" else None
                if not isinstance(r, dict):
                    continue
                at = _epoch(o.get("timestamp")) or time.time()
                if isinstance(r.get("backgroundTaskId"), str):
                    m = _BG_OUTPUT.search(_text_of((o.get("message") or {}).get("content")))
                    tasks[r["backgroundTaskId"]] = {"kind": "command", "at": at, "file": m.group(1) if m else None}
                elif r.get("status") == "async_launched" and isinstance(r.get("agentId"), str):
                    tasks[r["agentId"]] = {"kind": "agent", "at": at, "file": os.path.join(
                        sid_dir, "subagents", "agent-%s.jsonl" % r["agentId"])}
            elif b"<task-notification>" in line and b'"queue-operation"' in line:
                o = _json(line.decode("utf-8", "replace"))
                if not (isinstance(o, dict) and o.get("type") == "queue-operation" and o.get("operation") == "enqueue"):
                    continue
                content = o.get("content")
                if not (isinstance(content, str) and content.lstrip().startswith("<task-notification>")):
                    continue
                m = _BG_NOTICE.search(content)
                at = _epoch(o.get("timestamp"))
                if m and at is not None:
                    tid = m.group(1).strip()
                    self.bg_done[tid] = max(at, self.bg_done.get(tid) or 0)
        if not tasks:
            del self.bg[path]
        if len(self.bg_done) > 5000:                     # notices of tasks launched before what was looked through
            for tid, _ in sorted(self.bg_done.items(), key=lambda kv: kv[1])[:1000]:
                del self.bg_done[tid]

    def local_titles(self):
        """Titles of the local sessions whose transcripts were read (not archived): those the transcripts speak for."""
        out = set()
        for path, info in self.sessions.items():
            if info["title"] and self.owner.get(os.path.basename(path)[:-len(".jsonl")]) not in self.archived:
                out.add(info["title"])
        return out

    def turn_ends_after(self, t):
        """When main-thread turns ended (their Stop hooks ran) after `t`, oldest first, across sessions."""
        return sorted(at for info in self.sessions.values() for at in info["turn_ends"] if at > t)

    def last_activity(self):
        """When any session last made a request (None if none read)."""
        return max((info["last_at"] for info in self.sessions.values() if info["last_at"]), default=None)

    def limit_stops(self):
        """title -> {at, text, resets_at} for sessions whose last request was refused at the usage limit."""
        return {info["title"]: info["limit_stop"] for info in self.sessions.values() if info["limit_stop"]}

    def _read_records(self):
        try:
            tops = [os.path.join(e.path, "claude-code-sessions") for e in os.scandir(self.records) if e.is_dir()]
        except OSError:
            self.records_ok = False
            return
        tops = [t for t in tops if os.path.isdir(t)]
        self.records_ok = bool(tops)
        for top in tops:
            for folder, _, names in os.walk(top):
                for name in names:
                    if not (name.startswith("local_") and name.endswith(".json")):
                        continue
                    path = os.path.join(folder, name)
                    try:
                        mtime = os.stat(path).st_mtime
                        if self.record_seen.get(path) == mtime:
                            continue
                        with open(path, encoding="utf-8") as fh:
                            o = json.load(fh)
                    except (OSError, ValueError):
                        continue                    # gone, or caught mid-write: tried again next time
                    self.record_seen[path] = mtime
                    if not isinstance(o, dict):
                        continue
                    for cli in [o.get("cliSessionId")] + list(o.get("priorCliSessionIds") or ()):
                        if isinstance(cli, str):
                            self.owner[cli] = name
                    if o.get("isArchived") is True:
                        self.archived.add(name)
                    else:
                        self.archived.discard(name)

    @staticmethod
    def _read_tail(path, size, tail=TAIL_BYTES):
        """(newest usage, newest hook context, activity) from the tail of a transcript. Activity: the session's title,
        its last request to the API (last_at), the cache TTL it writes (ttl, s), its context size (tokens) and whether
        the cache timer agreed with the API's own cache reads (check). If the tail holds neither a request nor a
        compaction (a compaction is followed by large records: reloaded files, tool lists), it looks further back."""
        act = {"title": None, "last_at": None, "ttl": None, "tokens": None, "check": None, "model": None,
               "compacted": False, "limit_stop": None, "turn_open": False, "turn_ends": []}
        try:
            with open(path, "rb") as fh:
                if size > tail:
                    fh.seek(size - tail)
                    fh.readline()           # skip the partial first line
                data = fh.read().decode("utf-8", "replace")
        except OSError:
            return None, None, act
        usage = ctx = None
        events, last_request = [], None
        compacted_at = after_compact = None     # a compaction with no request since, and its size if /context told
        for line in data.splitlines():
            # Each check parses the record and verifies its type: a reply that merely mentions these words in its
            # text isn't taken for one.
            if line.startswith('{"type":"custom-title"'):
                try:
                    act["title"] = json.loads(line).get("customTitle") or act["title"]
                except ValueError:
                    pass
            if line.startswith('{"type":"api-request"'):
                try:
                    last_request = _epoch(json.loads(line).get("timestamp")) or last_request
                    act["turn_open"] = True
                except ValueError:
                    pass
            if '"stop_hook_summary"' in line or '[Request interrupted' in line:
                o = _json(line)
                if isinstance(o, dict) and not o.get("isSidechain") and (
                        (o.get("type") == "system" and o.get("subtype") == "stop_hook_summary") or
                        (o.get("type") == "user" and "[Request interrupted" in json.dumps(
                            (o.get("message") or {}).get("content")))):
                    act["turn_open"] = False        # the turn ended (or you stopped it)
                    if o.get("subtype") == "stop_hook_summary":
                        at = _epoch(o.get("timestamp"))
                        if at is not None:
                            act["turn_ends"].append(at)     # its Stop hooks ran, the usage hook among them
            if '"hook_success"' in line:
                u = usage_from_record(line)
                if u is not None:
                    usage, ctx = u, context_from_record(line)
            if '"compact_boundary"' in line:
                o = _json(line)
                if isinstance(o, dict) and o.get("type") == "system" and o.get("subtype") == "compact_boundary":
                    events.append("compact")
                    compacted_at, after_compact = _epoch(o.get("timestamp")), None
            if compacted_at is not None and '"local_command"' in line and "Context Usage" in line:
                after_compact = context_command_tokens(line) or after_compact
            if '"isApiErrorMessage"' in line:
                stop = limit_stop_from_line(line)
                if stop is not None:
                    act["limit_stop"], act["turn_open"] = stop, False
            if '"cache_creation"' in line:
                req = request_from_line(line)
                if req is not None:
                    events.append(req)
                    compacted_at = None
                    act["limit_stop"] = None    # it got through after all
                    act["turn_open"] = True
        reqs = [e for e in events if e != "compact"]
        if reqs:
            act["tokens"] = reqs[-1]["tokens"]
            act["model"] = reqs[-1]["model"]
            act["last_at"] = max(reqs[-1]["at"], last_request or 0)
            act["ttl"] = next((r["ttl"] for r in reversed(reqs) if r["ttl"]), None)
            act["check"] = cache_check(events)
        elif last_request is not None:
            act["last_at"] = last_request
        if not reqs and compacted_at is None and tail < min(size, MAX_TAIL_BYTES):
            return TranscriptUsage._read_tail(path, size, tail * 4)
        if compacted_at is not None:
            # Compacted since its last reply: the old size and cache no longer apply. The new size is known only once
            # the next reply comes (or if /context was run since); the old cache doesn't fit the new conversation.
            act["compacted"] = True
            act["tokens"] = after_compact
            act["last_at"] = max(act["last_at"] or 0, compacted_at)
        return usage, ctx, act

    @staticmethod
    def _read_head(path):
        """(title, id of the conversation's first message) from the start of a transcript; either may be None. Read
        once per transcript, line by line: a rewound one starts with copies of all its file snapshots."""
        title = root = None
        read = root_at = 0
        try:
            with open(path, "rb") as fh:
                for raw in fh:
                    read += len(raw)
                    if title is None and b'"custom-title"' in raw:
                        o = _json(raw)
                        if isinstance(o, dict) and o.get("type") == "custom-title":
                            title = o.get("customTitle")
                    if root is None and b'"uuid"' in raw:
                        o = _json(raw)
                        if isinstance(o, dict) and o.get("type") in ("user", "assistant") and \
                                isinstance(o.get("uuid"), str):
                            root, root_at = o["uuid"], read
                    # The title, when set, is near the top or soon after the first message.
                    if read > HEAD_BYTES or (root is not None and (title is not None or read - root_at > TITLE_BYTES)):
                        break
        except OSError:
            pass
        return title, root
