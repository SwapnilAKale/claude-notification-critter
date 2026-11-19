"""The get_usage hook's records in Claude Code transcripts -> plan usage (claude_mini/usage.py).

Run:  py -3.14 -B -m unittest discover -s tests
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claude_mini import usage as U                     # noqa: E402


def reply(at, tokens, ttl, sidechain=False, model="claude-opus-5-5"):
    """A main-thread assistant message as Claude Code records it: its API usage, with the cache type written."""
    cc = {"ephemeral_1h_input_tokens": 500 if ttl == "1h" else 0, "ephemeral_5m_input_tokens": 500 if ttl == "5m" else 0}
    usage = {"input_tokens": 2, "cache_creation_input_tokens": 500 if ttl else 0,
             "cache_read_input_tokens": tokens - 2 - (500 if ttl else 0), "output_tokens": 300, "cache_creation": cc}
    return json.dumps({"type": "assistant", "isSidechain": sidechain, "timestamp": iso(at),
                       "message": {"role": "assistant", "model": model, "usage": usage, "content": []}})


def five_hour(u):
    return dict([(u.limit, u.pct)] + [(lbl, p) for lbl, p, _ in u.others])["5-hour limit"]


def iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(epoch)) + ".%03dZ" % int(round((epoch % 1) * 1000) % 1000)


def record(at, five=7, weekly=25, five_reset=None, context=None):
    """A transcript line shaped like the one recorded on 2026-10-25 (Stop hook, type mcp_tool)."""
    plan = {"plan": {"status": "ok", "plan": "Max", "windows": [
        {"label": "5-hour limit", "percentUsed": five, "resetsAt": iso(five_reset or at + 3 * 3600),
         "resetsIn": "2h 58m"},
        {"label": "Weekly · all models", "percentUsed": weekly, "resetsAt": iso(at + 3 * 86400)},
        {"label": "Weekly · Fable", "percentUsed": 0, "resetsAt": iso(at + 3 * 86400)}],
        "extraUsage": {"enabled": False}}}
    if context is not None:
        plan["context"] = {"session": "self", "status": "ok", "tokensUsed": context * 10000, "contextWindow": 1000000,
                           "percentUsed": context, "autoCompactsAtPercent": 97}
    return json.dumps({"type": "attachment", "timestamp": iso(at), "sessionId": "s",
                       "attachment": {"type": "hook_success", "hookName": "Stop", "hookEvent": "Stop",
                                      "stdout": json.dumps(plan, indent=2), "exitCode": 0,
                                      "command": "ccd_session_mgmt/get_usage"}})


class Records(unittest.TestCase):
    def test_reads_the_hook_record(self):
        now = time.time()
        u = U.usage_from_record(record(now - 5))
        self.assertEqual((u.pct, u.limit), (25, "Weekly · all models"))     # the fullest limit leads
        self.assertEqual([(lbl, p) for lbl, p, _ in u.others], [("5-hour limit", 7), ("Weekly · Fable", 0)])
        self.assertAlmostEqual(u.seen_at, now - 5, delta=1)

    def test_a_window_past_its_reset_is_empty_again(self):
        now = time.time()
        u = U.usage_from_record(record(now - 7200, five=90, weekly=25, five_reset=now - 60))
        self.assertEqual((u.pct, u.limit), (25, "Weekly · all models"))

    def test_other_lines_are_ignored(self):
        for line in ('{"type": "assistant", "message": {"content": "windows"}}', "not json",
                     json.dumps({"type": "attachment", "attachment": {"type": "hook_success", "stdout": "{}",
                                                                      "command": "other"}})):
            self.assertIsNone(U.usage_from_record(line))


class Watching(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.mkdir(os.path.join(self.root, "proj-a"))
        os.mkdir(os.path.join(self.root, "proj-b"))
        self.records = tempfile.mkdtemp()
        self._saved = U.RECORDS
        U.RECORDS = self.records             # never the real session records

    def tearDown(self):
        U.RECORDS = self._saved
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.records, ignore_errors=True)

    def write(self, rel, *lines):
        with open(os.path.join(self.root, rel), "a", encoding="utf-8") as fh:
            for line in lines:
                fh.write(line + "\n")

    def test_newest_record_across_sessions_and_after_every_reply(self):
        now = time.time()
        self.write("proj-a/one.jsonl", '{"type": "user"}', record(now - 600, five=10))
        self.write("proj-b/two.jsonl", record(now - 60, five=40), '{"type": "assistant"}')
        t = U.TranscriptUsage(self.root)
        u = t.poll(now)
        self.assertEqual(five_hour(u), 40)
        self.assertEqual(t.poll(now + 0.5), u)                 # between scans: nothing re-read
        time.sleep(0.05)
        self.write("proj-a/one.jsonl", record(now, five=55))   # another reply somewhere
        u = t.poll(now + U.SCAN_EVERY + 1)
        self.assertEqual(five_hour(u), 55)

    def test_sub_agent_transcripts_of_active_sessions(self):
        now = time.time()
        self.write("proj-a/sess.jsonl", record(now - 3000, five=10))
        os.makedirs(os.path.join(self.root, "proj-a", "sess", "subagents"))
        self.write("proj-a/sess/subagents/agent-1.jsonl", record(now - 10, five=70))
        self.assertEqual(five_hour(U.TranscriptUsage(self.root).poll(now)), 70)

    def test_sessions_are_listed_while_their_cache_is_warm(self):
        now = time.time()
        title = lambda t: json.dumps({"type": "custom-title", "customTitle": t, "sessionId": "s"})  # noqa: E731
        self.write("proj-a/one.jsonl", title("BRIEF.md implementation end-to-end"), record(now - 900, context=30),
                   reply(now - 600, 350000, "1h"))
        self.write("proj-b/two.jsonl", title("Local Voice setup"), reply(now - 120, 90000, "5m"))
        self.write("proj-b/cold.jsonl", title("Cold one"), reply(now - 400, 50000, "5m"))
        os.makedirs(os.path.join(self.root, "proj-a", "one", "subagents"))
        self.write("proj-a/one/subagents/agent-1.jsonl", reply(now - 5, 12000, "1h", sidechain=True))
        t = U.TranscriptUsage(self.root)
        t.poll(now)
        got = [(c.title, c.pct, c.tokens, round(c.cache_until - now)) for c in t.contexts(now)]
        self.assertEqual(got, [("Local Voice setup", 9, 90000, 180),                  # 5-minute cache; window learned from the model
                               ("BRIEF.md implementation end-to-end", 35, 350000, 3000)])   # 1-hour cache
        self.assertEqual([c.title for c in t.contexts(now + 200)], ["BRIEF.md implementation end-to-end"])
        self.assertEqual(t.contexts(now + 3001), [])                     # every cache gone: nobody listed

    def test_a_new_request_renews_the_cache(self):
        now = time.time()
        self.write("proj-a/one.jsonl", reply(now - 3500, 100000, "1h"))
        t = U.TranscriptUsage(self.root)
        t.poll(now)
        self.assertEqual(round(t.contexts(now)[0].cache_until - now), 100)
        time.sleep(0.05)
        self.write("proj-a/one.jsonl", json.dumps({"type": "api-request", "timestamp": iso(now - 10)}),
                   reply(now - 2, 101000, None))                          # read from the cache only
        t.poll(now + U.SCAN_EVERY + 1)
        c = t.contexts(now)[0]
        self.assertEqual((round(c.cache_until - now), c.tokens), (3598, 101000))

    def test_window_size_learned_from_another_session_on_the_same_model(self):
        # Reported 2026-10-28: a session started before the hook showed "136k" instead of a percentage.
        now = time.time()
        self.write("proj-a/old.jsonl", reply(now - 60, 136000, "1h"))                       # never ran the hook
        self.write("proj-b/new.jsonl", reply(now - 120, 350000, "1h"), record(now - 100, context=35))
        self.write("proj-b/other.jsonl", reply(now - 90, 50000, "1h", model="claude-haiku-5-5"))
        t = U.TranscriptUsage(self.root)
        t.poll(now)
        got = dict((round(c.tokens / 1000), c.pct) for c in t.contexts(now))
        self.assertEqual(got, {136: 14, 350: 35, 50: None})     # same model: learned; another model: still tokens

    def test_a_rewound_session_is_listed_once(self):
        # Reported 2026-10-29: one sidebar session showed three times. Each rewind / message edit had carried it on in a
        # new transcript copying the conversation (same first message, same title).
        now = time.time()
        title = '{"type":"custom-title","customTitle":"Sanskrit speech models research"}'
        fork = '{"type":"custom-title","customTitle":"Sanskrit speech models research (fork)"}'
        first = '{"type":"user","uuid":"b127a79d","parentUuid":null,"message":{"role":"user","content":"hi"}}'
        other = '{"type":"user","uuid":"0f00","parentUuid":null,"message":{"role":"user","content":"hi"}}'
        self.write("proj-a/original.jsonl", title, first, reply(now - 1000, 327000, "1h"))
        self.write("proj-a/rewind1.jsonl", title, first, reply(now - 880, 331000, "1h"))
        snapshots = ['{"type":"file-history-snapshot","messageId":"m%d","snapshot":"%s"}' % (i, "x" * 3600)
                     for i in range(40)]           # a rewound transcript starts with copies of its file snapshots
        self.write("proj-a/rewind2.jsonl", title, *(snapshots + [first, reply(now - 5, 347000, "1h")]))
        self.write("proj-a/fork.jsonl", fork, first, reply(now - 50, 300000, "1h"))
        self.write("proj-b/same-title-new-chat.jsonl", title, other, reply(now - 60, 100000, "1h"))
        t = U.TranscriptUsage(self.root)
        t.poll(now)
        got = sorted((c.title, round(c.tokens / 1000)) for c in t.contexts(now))
        self.assertEqual(got, [("Sanskrit speech models research", 100), ("Sanskrit speech models research", 347),
                               ("Sanskrit speech models research (fork)", 300)])

    def test_an_archived_session_leaves_the_list_until_unarchived(self):
        now = time.time()
        self.write("proj-a/29ba9c3c.jsonl", '{"type":"custom-title","customTitle":"Side chat"}', reply(now - 600, 83000, "1h"))
        folder = os.path.join(self.records, "claude_x", "claude-code-sessions", "a", "b")
        os.makedirs(folder)
        record = os.path.join(folder, "local_29ba.json")
        with open(record, "w", encoding="utf-8") as fh:
            json.dump({"cliSessionId": "29ba9c3c", "isArchived": True}, fh)
        t = U.TranscriptUsage(self.root, self.records)
        t.poll(now)
        self.assertEqual(t.contexts(now), [])
        time.sleep(0.05)
        with open(record, "w", encoding="utf-8") as fh:
            json.dump({"cliSessionId": "29ba9c3c", "isArchived": False}, fh)
        os.utime(record, (now + 5, now + 5))
        t.poll(now + U.SCAN_EVERY + 1)
        self.assertEqual([c.title for c in t.contexts(now)], ["Side chat"])

    def test_a_turn_is_under_way_from_its_request_to_its_stop_hook(self):
        now = time.time()
        title = '{"type":"custom-title","customTitle":"BRIEF.md implementation end-to-end"}'
        stop = json.dumps({"type": "system", "subtype": "stop_hook_summary", "isSidechain": False,
                           "timestamp": iso(now - 300)})
        request = json.dumps({"type": "api-request", "timestamp": iso(now - 20)}, separators=(",", ":"))  # as Claude writes it
        self.write("proj-a/one.jsonl", title, reply(now - 400, 100000, "1h"), stop)
        t = U.TranscriptUsage(self.root)
        t.poll(now)
        self.assertEqual(t.working(now), set())                     # its last turn ended
        time.sleep(0.05)
        self.write("proj-a/one.jsonl", request)                     # a new turn: working
        t.poll(now + U.SCAN_EVERY + 1)
        self.assertEqual(t.working(now), {"BRIEF.md implementation end-to-end"})
        self.assertEqual(t.working(now + U.TURN_QUIET_S + 60), set())   # nothing written for 15 min: not taken for working
        time.sleep(0.05)
        self.write("proj-a/one.jsonl", json.dumps({"type": "user", "isSidechain": False, "message": {
            "role": "user", "content": [{"type": "text", "text": "[Request interrupted by user]"}]}}))
        t.poll(now + 2 * U.SCAN_EVERY + 2)
        self.assertEqual(t.working(now), set())                     # you stopped it

    def test_when_replies_ended(self):
        now = time.time()
        stop = lambda at: json.dumps({"type": "system", "subtype": "stop_hook_summary", "isSidechain": False,  # noqa: E731
                                      "timestamp": iso(at)})
        self.write("proj-a/one.jsonl", reply(now - 900, 1000, "1h"), stop(now - 890), reply(now - 300, 1200, "1h"),
                   stop(now - 290))
        t = U.TranscriptUsage(self.root)
        t.poll(now)
        self.assertEqual([round(now - at) for at in t.turn_ends_after(now - 600)], [290])

    def test_a_request_refused_at_the_limit(self):
        # Seen 2026-11-03: Claude records the refused request as an assistant message with zero usage. It isn't a
        # request (no tokens sent, no cache kept), but it says the session stopped at the limit, and until when.
        now = time.time()
        refused = json.dumps({"type": "assistant", "isSidechain": False, "timestamp": iso(now - 60),
                              "isApiErrorMessage": True, "error": "rate_limit",
                              "apiErrorParams": {"rate_limit_info": {"resetsAt": now + 900}},
                              "message": {"model": "<synthetic>", "content": [
                                  {"type": "text", "text": "You've hit your session limit · resets 1:10am"}],
                                  "usage": {"input_tokens": 0, "cache_creation_input_tokens": 0,
                                            "cache_read_input_tokens": 0, "cache_creation": {
                                                "ephemeral_1h_input_tokens": 0, "ephemeral_5m_input_tokens": 0}}}})
        self.write("proj-a/one.jsonl", '{"type":"custom-title","customTitle":"Busy one"}',
                   reply(now - 600, 120000, "1h"), refused)
        t = U.TranscriptUsage(self.root)
        t.poll(now)
        (c,) = t.contexts(now)
        self.assertEqual((c.tokens, round(c.cache_until - now)), (120000, 3000))    # the real request's
        stop = t.limit_stops()["Busy one"]
        self.assertEqual((stop["text"], round(stop["resets_at"] - now)), ("You've hit your session limit · resets 1:10am", 900))
        time.sleep(0.05)
        self.write("proj-a/one.jsonl", reply(now - 5, 121000, "1h"))                 # carried on
        t.poll(now + U.SCAN_EVERY + 1)
        self.assertEqual(t.limit_stops(), {})

    def test_a_session_rewound_after_compacting_is_listed_once(self):
        # Reported 2026-10-31: "BRIEF.md implementation end-to-end" showed twice. Rewound after a compaction, its new
        # transcript started at the compaction summary, not the first message; Claude's session record ties them.
        now = time.time()
        title = '{"type":"custom-title","customTitle":"BRIEF.md implementation end-to-end"}'
        first = '{"type":"user","uuid":"23145c14","parentUuid":null,"message":{"role":"user","content":"hi"}}'
        summary = '{"type":"user","uuid":"4f650ab8","parentUuid":"09b0","message":{"role":"user","content":"..."}}'
        self.write("proj-a/e5601d41.jsonl", title, first, reply(now - 2000, 176000, "1h"))
        self.write("proj-a/eadad695.jsonl", title, summary, reply(now - 300, 267000, "1h"))
        self.write("proj-b/other.jsonl", title, summary, reply(now - 100, 50000, "1h"))      # no record: on its own
        folder = os.path.join(self.records, "claude_x", "claude-code-sessions", "a", "b")
        os.makedirs(folder)
        with open(os.path.join(folder, "local_1e91.json"), "w", encoding="utf-8") as fh:
            json.dump({"cliSessionId": "eadad695", "priorCliSessionIds": ["1012d2d0", "e5601d41"]}, fh)
        t = U.TranscriptUsage(self.root, self.records)
        t.poll(now)
        got = sorted(round(c.tokens / 1000) for c in t.contexts(now))
        self.assertEqual(got, [50, 267])

    def test_a_compaction_replaces_the_old_size(self):
        # Reported 2026-10-30: after compacting a session the panel still showed its size from before (88%).
        now = time.time()
        boundary = json.dumps({"type": "system", "subtype": "compact_boundary", "isSidechain": False,
                               "timestamp": iso(now - 120), "compactMetadata": {"preTokens": 877159}})
        stdout = "<local-command-stdout>## Context Usage\n\n**Model:** claude-opus-5-5  \n**Tokens:** 90.1k / 1m (9%)\n"
        context_cmd = json.dumps({"type": "system", "subtype": "local_command", "timestamp": iso(now - 60),
                                  "content": stdout})
        reloaded = json.dumps({"type": "attachment", "attachment": {"type": "file", "content": "x" * 300000}})
        self.write("proj-a/a.jsonl", reply(now - 300, 880000, "1h"), record(now - 290, context=88), boundary,
                   reloaded)                       # big records after the compaction push it out of the usual tail
        t = U.TranscriptUsage(self.root)
        t.poll(now)
        (c,) = t.contexts(now)
        self.assertEqual((c.compacted, c.pct, c.tokens), (True, None, None))     # size unknown until the next reply
        self.write("proj-a/a.jsonl", context_cmd)
        t.poll(now + 5)
        (c,) = t.contexts(now + 5)
        self.assertEqual((c.compacted, c.pct, c.tokens), (True, 9, 90100))      # /context told the new size
        self.write("proj-a/a.jsonl", reply(now - 30, 95000, "1h"))
        t.poll(now + 10)
        (c,) = t.contexts(now + 10)
        self.assertEqual((c.compacted, c.pct, c.tokens), (False, 10, 95000))    # the next reply: back to normal

    def test_unreadable_cache_type_shows_as_unknown_not_guessed(self):
        now = time.time()
        self.write("proj-a/one.jsonl", reply(now - 1200, 80000, None))          # never wrote a cache we can see
        t = U.TranscriptUsage(self.root)
        t.poll(now)
        c = t.contexts(now)[0]
        self.assertEqual((c.cache_until, c.tokens), (None, 80000))               # "cache ?" in the panel
        self.assertEqual(t.contexts(now + 2401), [])                             # gone after the longest TTL

    def test_a_reply_that_mentions_hook_records_still_counts(self):
        # The bug found on 2026-10-28: a reply whose text mentions "hook_success" was taken for a hook record.
        now = time.time()
        line = json.loads(reply(now - 30, 120000, "1h"))
        line["message"]["content"] = [{"type": "text", "text": 'look for "hook_success" and "custom-title"'}]
        self.write("proj-a/one.jsonl", json.dumps(line))
        t = U.TranscriptUsage(self.root)
        t.poll(now)
        self.assertEqual(t.contexts(now)[0].tokens, 120000)

    def test_no_transcripts_no_usage(self):
        self.assertIsNone(U.TranscriptUsage(os.path.join(self.root, "missing")).poll())



def req(at, tokens=100000, read=None, ttl=3600, model="claude-opus-5-5"):
    return {"at": at, "tokens": tokens, "read": tokens - 500 if read is None else read, "ttl": ttl, "model": model}


class CacheCheck(unittest.TestCase):
    """The timer is checked against what the API reported: mostly read from the cache = it was warm."""

    def test_agreeing(self):
        self.assertIs(U.cache_check([req(0), req(30)]), True)                     # within the hour, read: warm
        self.assertIs(U.cache_check([req(0), req(5000, read=0)]), True)           # after the hour, not read: cold

    def test_disagreeing_is_flagged(self):
        self.assertIs(U.cache_check([req(0), req(600, read=0)]), False)           # timer said warm, the API missed
        self.assertIs(U.cache_check([req(0), req(5000)]), False)                  # timer said cold, the API hit

    def test_pairs_it_cant_judge_are_skipped(self):
        self.assertIsNone(U.cache_check([req(0), "compact", req(600, read=0)]))           # compaction: new cache
        self.assertIsNone(U.cache_check([req(0), req(600, read=0, model="claude-sonnet-5-5")]))   # model switch
        self.assertIsNone(U.cache_check([req(0), req(3620, read=0)]))                     # too close to the TTL
        self.assertIsNone(U.cache_check([req(0, tokens=900000), req(60, tokens=90000, read=0)]))  # shrank a lot
        self.assertIsNone(U.cache_check([req(0)]))

    def test_the_latest_verdict_counts(self):
        self.assertIs(U.cache_check([req(0), req(600, read=0), req(630)]), True)


if __name__ == "__main__":
    unittest.main()
