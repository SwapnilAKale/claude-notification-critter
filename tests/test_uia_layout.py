"""Sidebar parsing for both of Claude's layouts, on fake accessibility trees shaped like the real ones (RECON.md).

Run:  py -3.14 -B -m unittest discover -s tests
"""
import os
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claude_mini import uia as U                      # noqa: E402


class El(object):
    def __init__(self, ct, name="", *kids, off=False):
        self.ct, self.name, self.kids, self.off = ct, name, list(kids), off

    def get_ptr(self, slot, *args):
        assert slot == U.E_GET_CACHED_CHILDREN
        return Arr(self.kids)

    def get_int(self, slot):
        return {U.E_CACHED_CONTROLTYPE: self.ct, U.E_CACHED_ISOFFSCREEN: int(self.off)}[slot]

    def get_bstr(self, slot):
        assert slot == U.E_CACHED_NAME
        return self.name


class Arr(object):
    def __init__(self, items):
        self.items = items

    def get_int(self, slot):
        return len(self.items)

    def get_ptr(self, slot, i):
        return self.items[i.value]


def G(*kids, name=""):
    return El(U.CT_GROUP, name, *kids)


def B(name, *kids):
    return El(U.CT_BUTTON, name, *kids)


def T(name):
    return El(U.CT_TEXT, name)


def S(name):
    return El(U.CT_STATUSBAR, name)


def chat_row(title, status=None):
    # ListItem > Group > Group{ Button{Group{Text}}, [StatusBar], Group{Button "More options for ..."} }
    inner = [B(title, G(T(title)))]
    if status is not None:
        inner.append(S(status))
    inner.append(G(B("More options for " + title)))
    return G(G(*inner))


def code_row(title, status):
    return B("%s %s" % (status, title), S(status), T(title))


def rows(tree):
    reader = U.UiaReader.__new__(U.UiaReader)
    pairs, count = [], [0]
    reader._collect(tree, pairs, 0, count)
    return [(r.title, r.status) for r, _ in pairs], count[0] > 0


class SidebarLayouts(unittest.TestCase):
    def test_code_tab_status_is_the_rows_first_child(self):
        tree = G(B("New session"), G(code_row("Fix login", "Awaiting input"), code_row("Docs", "Idle")))
        self.assertEqual(rows(tree), ([("Fix login", "Awaiting input"), ("Docs", "Idle")], True))

    def test_chat_tab_status_sits_beside_the_row(self):
        tree = G(B("Search"), B("New"), G(
            B("Projects"),
            G(B("Local-voice-agent", B("Toggle sessions for Local-voice-agent"), G(T("Local-voice-agent"))),
              G(B("More options for Local-voice-agent"))),
            G(chat_row("Parakeet layer"), chat_row("Voice plugin", "Running"), chat_row("Review", "Unread response")),
            chat_row("Greeting exchange")))                  # a plain chat: no status icon, not a session row
        # Rows with a menu but no status read as idle (Claude 5.11 dropped status icons); the chat tab keeps only rows
        # already known as Code sessions (monitor.chat_tab_rows), so a plain chat changes nothing.
        got = dict(rows(tree)[0])
        self.assertEqual((got["Voice plugin"], got["Review"], got["Parakeet layer"]), ("Running", "Unread response", "Idle"))

    def test_claude_2_37_status_is_text_inside_the_row(self):
        # Button{Group{Group{Text title, [Text subtitle], StatusBar{Text status}}}, Group{Button "Hide nested ..."}}
        def row(title, status=None, subtitle=None):
            inner = [T(title)] + ([T(subtitle)] if subtitle else []) + [El(U.CT_STATUSBAR, "", *([T(status)] if status else []))]
            return G(G(B("%s %s" % (title, status or ""), G(G(*inner)), G(B("Hide nested sessions " + title))),
                       G(B("More options for " + title))))
        tree = G(B("New"), G(El(U.CT_TEXT, "Claude-mini", B("Claude-mini", G(T("Claude-mini")))), B("Group and sort"),
                             B("New session in Claude-mini")),
                 row("Claude desktop companion blob app", "Running"),
                 row("Make the blob critter bigger and 3D", subtitle="Started by Claude from Claude desktop companion"),
                 row("Fix login", "Awaiting input"))
        self.assertEqual(rows(tree), ([("Claude desktop companion blob app", "Running"),
                                       ("Make the blob critter bigger and 3D", "Idle"),
                                       ("Fix login", "Awaiting input")], True))

    def test_a_cloud_rows_caption_says_where_it_runs_and_its_state(self):
        # Seen 2026-11-03: Button{Group{Text title, [Text subtitle], StatusBar (empty), Text "Cloud · Merged"}}
        def row(title, caption, subtitle=None):
            inner = [T(title)] + ([T(subtitle)] if subtitle else []) + [El(U.CT_STATUSBAR, ""), T(caption)]
            return G(G(B("%s %s" % (title, caption), G(*inner)), G(G(B("More options for " + title)))))
        reader = U.UiaReader.__new__(U.UiaReader)
        pairs, count = [], [0]
        reader._collect(G(row("Docker Compose deployment on Oracle Cloud", "Cloud \u00b7 Merged"),
                          row("Visitor analytics", "Cloud \u00b7 Working", "Started from a suggestion in X"),
                          row("Local one", "Started by Claude from Y")), pairs, 0, count)
        self.assertEqual([(r.title, r.status, r.env, r.env_state) for r, _ in pairs],
                         [("Docker Compose deployment on Oracle Cloud", "Idle", "Cloud", "Merged"),
                          ("Visitor analytics", "Idle", "Cloud", "Working"),
                          ("Local one", "Idle", None, None)])

    def test_captions(self):
        self.assertEqual(U.parse_caption("Cloud \u00b7 Needs input"), ("Cloud", "Needs input"))
        self.assertEqual(U.parse_caption("Cloud"), ("Cloud", None))
        self.assertIsNone(U.parse_caption("Started from a suggestion in Cloud"))

    def test_claude_5_11_status_is_plain_text_after_the_title(self):
        # Seen 2026-11-19 (Claude 5.11492): no StatusBar. Group{Group{Button{Group{Group{Text title, [Text subtitle],
        # [Text status], [Text caption]}}, Group{Button "Hide nested ..."}}, Group{Button "More options for <title>"}}}
        def row(title, *texts):
            return G(G(B("%s %s" % (title, " ".join(texts)), G(G(T(title), *[T(t) for t in texts])),
                         G(B("Hide nested sessions " + title))),
                       G(B("More options for " + title))))
        tree = G(G(El(U.CT_TEXT, "Claude-mini", B("Claude-mini", G(T("Claude-mini")))), B("Group and sort"),
                   B("New session in Claude-mini")),
                 row("Claude desktop companion blob app", "Running"),
                 row("Make the blob critter bigger and 3D", "Started by Claude from Claude desktop companion blob app"),
                 row("BRIEF.md implementation end-to-end (fork)", "Forked from BRIEF.md implementation end-to-end",
                     "Awaiting input"),
                 row("Docker Compose deployment on Oracle Cloud", "Cloud · Merged"),
                 row("Final integration and deliverables", "Remote Control · Offline"))
        self.assertEqual(rows(tree), ([("Claude desktop companion blob app", "Running"),
                                       ("Make the blob critter bigger and 3D", "Idle"),
                                       ("BRIEF.md implementation end-to-end (fork)", "Awaiting input"),
                                       ("Docker Compose deployment on Oracle Cloud", "Idle"),
                                       ("Final integration and deliverables", "Idle")], True))

    def test_empty_status_icons_are_ignored(self):
        tree = G(G(B("Some header button"), S(""), G(B("More options"))))
        self.assertEqual(rows(tree), ([], True))

    def test_the_mode_only_sidebar_lists_nothing(self):
        tree = G(El(U.CT_RADIO, "Claude"), El(U.CT_RADIO, "Code, working"), name="Mode")
        self.assertEqual(rows(tree), ([], False))


if __name__ == "__main__":
    unittest.main()
