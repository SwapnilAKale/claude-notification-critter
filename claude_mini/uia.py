"""Source B: read Claude's sidebar (Code tab or chat tab, and the Claude/Code mode toggle) through UI Automation.

Recon facts this relies on (see RECON.md):
- Code tab: each session row is a Button whose FIRST child is a StatusBar/Image named with the status ("Awaiting
  input", "Unread response", "Running", "Idle", ...), followed by a Text with the session title;
- chat tab: project sessions are listed under their project; a row is a Button with the title, and its status is a
  StatusBar NEXT to it in the same group (plain chats have no status icon);
- the sidebar is a group named "Sidebar" with ARIA role "complementary" (the chat tab has two: one holds only the
  mode toggle, the other the list);
- the Claude/Code toggle is a radio group named "Mode"; the unselected tab's name carries an aggregate such as
  "Code, awaiting your input" or "Code, unread activity";
- a cloud session's row also carries a caption after its status: "Cloud", or "Cloud · <state>" (seen: "Cloud ·
  Merged", a pull-request state);
- Claude 5.11 (2026-11): no status element any more. A row is a Button holding Texts (title, [subtitle], [status],
  [caption]) next to its own "More options for <title>" button; an idle row has no status text at all;
- Claude 6.28 (2026-11-25): the same, but the row is a Hyperlink instead of a Button.
"""
import ctypes
import re
import time

from . import com
from .win32 import GUID

CLSID_CUIAutomation8 = GUID.parse("e22ad333-b25f-460c-83d0-0581107395c9")
CLSID_CUIAutomation = GUID.parse("ff48dba4-60ef-4201-aa87-54103eef594e")
IID_IUIAutomation = GUID.parse("30cbe57d-d9d0-452a-ab13-7ac5ac4825ee")
IID_InvokePattern = GUID.parse("fb377fbe-8ea6-46d5-9c73-6499642d3059")
IID_SelectionItemPattern = GUID.parse("a8efa66a-0fda-421a-9194-38021f3578ea")
IID_LegacyPattern = GUID.parse("828055ad-355b-4435-86d5-3b51c14a9b1b")

# property / control type / pattern ids
P_NAME, P_CONTROLTYPE, P_ISOFFSCREEN, P_ARIAROLE = 30005, 30003, 30022, 30101
CT_BUTTON, CT_IMAGE, CT_RADIO, CT_STATUSBAR, CT_TEXT, CT_GROUP = 50000, 50006, 50013, 50017, 50020, 50026
CT_HYPERLINK = 50005
ROW_TYPES = (CT_BUTTON, CT_HYPERLINK)          # a session row: a Button, or a Hyperlink since Claude 6 (2026-11-25)
PAT_INVOKE, PAT_SELECTIONITEM, PAT_LEGACY = 10000, 10010, 10018
SCOPE_CHILDREN, SCOPE_DESCENDANTS, SCOPE_SUBTREE = 2, 4, 7

# IUIAutomation slots (UIAutomationCore type library)
A_ELEMENT_FROM_HANDLE, A_RAW_CONDITION, A_CREATE_CACHE_REQUEST = 6, 17, 20
A_TRUE_CONDITION, A_PROPERTY_CONDITION, A_AND_CONDITION = 21, 23, 25
# IUIAutomationElement slots
E_FIND_FIRST, E_FIND_ALL, E_BUILD_UPDATED_CACHE, E_GET_CURRENT_PATTERN_AS = 5, 6, 9, 14
E_GET_CACHED_CHILDREN, E_CUR_CONTROLTYPE, E_CUR_NAME, E_CUR_ISOFFSCREEN = 19, 21, 23, 38
E_CACHED_CONTROLTYPE, E_CACHED_NAME, E_CACHED_ISOFFSCREEN = 53, 55, 70
# IUIAutomationCacheRequest slots
C_ADD_PROPERTY, C_PUT_TREESCOPE, C_PUT_TREEFILTER = 3, 7, 9


class VARIANT(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort), ("r3", ctypes.c_ushort),
                ("val", ctypes.c_void_p), ("val2", ctypes.c_void_p)]


def _ref(guid):
    return ctypes.c_void_p(ctypes.addressof(guid))


def _bstr_variant(s):
    v = VARIANT()
    v.vt = 8                                    # VT_BSTR
    v.val = com.oleaut32.SysAllocString(s)
    return v


def _i4_variant(n):
    v = VARIANT()
    v.vt = 3                                    # VT_I4
    v.val = n
    return v


# What a row says about where it came from ("Started by Claude from X", "Started from a suggestion in X", "Forked
# from X"): not a status.
_SUBTITLE = re.compile(r"^(started by|started from|forked from)\b", re.I)
_CAPTION = re.compile(r"^(Cloud|Remote Control|SSH)(?:\s*\u00b7\s*(.+))?$")


def parse_caption(text):
    """(where the session runs, the state shown with it) from a row caption: "Cloud · Merged" -> ("Cloud", "Merged"),
    "Cloud" -> ("Cloud", None); None for any other text (a subtitle such as "Started by Claude from ...")."""
    m = _CAPTION.match(" ".join(text.split()))
    return (m.group(1), m.group(2)) if m else None


class Row(object):
    __slots__ = ("status", "title", "offscreen", "beside", "env", "env_state")

    def __init__(self, status, title, offscreen, beside=False, env=None, env_state=None):
        self.status, self.title, self.offscreen = status, title, offscreen
        self.beside = beside            # chat-tab layout (status next to the row): a chat, or a project session
        self.env = env                  # "Cloud" for a cloud session, else None (runs on this computer)
        self.env_state = env_state      # the state its caption shows ("Merged", "Working", ...), if any

    def __repr__(self):
        return "Row(%r, %r)" % (self.status, self.title)


class Reading(object):
    def __init__(self):
        self.window_found = False
        self.mode_found = False
        self.code_selected = None       # True = Code tab, False = Claude (chat) tab, None = unknown
        self.aggregate = ""              # e.g. "awaiting your input" / "unread activity" from the unselected Code tab
        self.sidebar_found = False
        self.rows = []
        self.open_titles = set()         # sessions open in Claude's pane(s) (header "<title>, rename session")
        self.usage = ""                  # Claude's usage ring, e.g. "Usage: 43% of session limit, Resets in 1 hr 1 min"
        self.complete = True             # False if part of the page changed under us mid-read


def _array(arr):
    if not arr:
        return
    n = arr.get_int(3)
    for i in range(n):
        yield arr.get_ptr(4, ctypes.c_int(i))


class UiaReader(object):
    def __init__(self):
        try:
            self.uia = com.create_instance(CLSID_CUIAutomation8, IID_IUIAutomation)
        except com.ComError:
            self.uia = com.create_instance(CLSID_CUIAutomation, IID_IUIAutomation)
        self.true_cond = self.uia.get_ptr(A_TRUE_CONDITION)
        raw = self.uia.get_ptr(A_RAW_CONDITION)
        self.cache = self.uia.get_ptr(A_CREATE_CACHE_REQUEST)
        for prop in (P_NAME, P_CONTROLTYPE, P_ISOFFSCREEN):
            self.cache.call(C_ADD_PROPERTY, [ctypes.c_int], prop)
        self.cache.call(C_PUT_TREESCOPE, [ctypes.c_int], SCOPE_SUBTREE)
        self.cache.call(C_PUT_TREEFILTER, [ctypes.c_void_p], raw.p)
        self.cond_sidebar = self._and(self._prop(P_NAME, "Sidebar"), self._prop(P_ARIAROLE, "complementary"))
        self.cond_mode = self._and(self._prop(P_NAME, "Mode"), self._prop(P_ARIAROLE, "radiogroup"))
        self.cond_button = self._prop(P_CONTROLTYPE, CT_BUTTON)
        self.hwnd = 0
        self.root = None
        self.sidebars = []
        self.mode = None
        self.next_sidebar_search = 0.0
        self.next_mode_search = 0.0

    # ---------- conditions ----------
    def _prop(self, prop, value):
        v = _bstr_variant(value) if isinstance(value, str) else _i4_variant(value)
        try:
            return self.uia.get_ptr(A_PROPERTY_CONDITION, ctypes.c_int(prop), v)
        finally:
            if v.vt == 8:
                com.oleaut32.SysFreeString(v.val)

    def _and(self, a, b):
        return self.uia.get_ptr(A_AND_CONDITION, ctypes.c_void_p(a.p), ctypes.c_void_p(b.p))

    # ---------- element helpers ----------
    @staticmethod
    def _alive(el):
        if not el:
            return False
        try:
            el.get_int(E_CUR_CONTROLTYPE)
            return True
        except com.ComError:
            return False

    def _find_first(self, el, scope, cond):
        return el.get_ptr(E_FIND_FIRST, ctypes.c_int(scope), ctypes.c_void_p(cond.p))

    def _attach(self, hwnd):
        if hwnd != self.hwnd or not self._alive(self.root):
            self.hwnd = hwnd
            self.root = self.uia.get_ptr(A_ELEMENT_FROM_HANDLE, ctypes.c_void_p(hwnd))
            self.sidebars = []
            self.mode = None
            self.next_sidebar_search = self.next_mode_search = 0.0

    # ---------- reading ----------
    def read(self, hwnd):
        r = Reading()
        if not hwnd:
            return r
        self._attach(hwnd)
        r.window_found = bool(self.root)
        now = time.time()

        # Claude rebuilds parts of its page when you switch sessions; elements can vanish mid-read
        # (UIA_E_ELEMENTNOTAVAILABLE). Each part is read separately and a hiccup only marks the reading incomplete.

        # Mode toggle (Claude | Code)
        try:
            if not self._alive(self.mode) and now >= self.next_mode_search:
                self.mode = self._find_first(self.root, SCOPE_DESCENDANTS, self.cond_mode)
                if not self.mode:
                    self.next_mode_search = now + 3.0     # it disappears while the sidebar is collapsed
            if self._alive(self.mode):
                for radio in _array(self.mode.get_ptr(E_FIND_ALL, ctypes.c_int(SCOPE_CHILDREN),
                                                      ctypes.c_void_p(self.true_cond.p))):
                    name = radio.get_bstr(E_CUR_NAME)
                    tab, _, extra = name.partition(",")
                    selected = False
                    try:
                        sel = radio.get_ptr(E_GET_CURRENT_PATTERN_AS, ctypes.c_int(PAT_SELECTIONITEM),
                                            _ref(IID_SelectionItemPattern))
                        selected = bool(sel) and bool(sel.get_int(6))       # get_CurrentIsSelected
                    except com.ComError:
                        pass
                    if tab.strip().lower() == "code":
                        r.code_selected = selected
                        if not selected:
                            r.aggregate = extra.strip().lower()
                r.mode_found = True
        except com.ComError:
            self.mode = None
            r.complete = False

        # Sidebar rows
        try:
            if not self._sidebars_alive() and now >= self.next_sidebar_search:
                self._find_sidebars()
            if self.sidebars:
                pairs, listed = self._scan()
                r.sidebar_found = listed
                r.rows = [row for row, _ in pairs]
                if not listed:
                    # Collapsed/re-created sidebars can leave us holding an empty shell; look again soon.
                    self.sidebars = []
                    self.next_sidebar_search = now + 1.0
        except com.ComError:
            self.sidebars = []
            r.rows = []
            r.sidebar_found = False
            r.complete = False

        # Sessions open in Claude's pane(s): Claude's own "N sessions need input" pill leaves these out. The same pass
        # picks up the usage ring next to a Code session's message box (one per pane; they all say the same).
        suffix = ", rename session"
        try:
            for b in _array(self.root.get_ptr(E_FIND_ALL, ctypes.c_int(SCOPE_DESCENDANTS),
                                              ctypes.c_void_p(self.cond_button.p))):
                try:
                    name = b.get_bstr(E_CUR_NAME)
                except com.ComError:
                    continue
                if name.endswith(suffix):
                    if not b.get_int(E_CUR_ISOFFSCREEN):        # a pane kept but not shown isn't open in front of you
                        r.open_titles.add(name[:-len(suffix)].strip())
                elif name.startswith("Usage:") and not r.usage:
                    r.usage = name
        except com.ComError:
            r.complete = False
        return r

    def _sidebars_alive(self):
        return bool(self.sidebars) and all(self._alive(sb) for sb in self.sidebars)

    def _find_sidebars(self):
        self.sidebars = list(_array(self.root.get_ptr(E_FIND_ALL, ctypes.c_int(SCOPE_DESCENDANTS),
                                                      ctypes.c_void_p(self.cond_sidebar.p))))
        if not self.sidebars:
            self.next_sidebar_search = time.time() + 1.0

    def _scan(self):
        """(row, button) pairs from every sidebar group, and whether any of them lists anything at all."""
        pairs, count = [], [0]
        for sb in self.sidebars:
            cached = sb.get_ptr(E_BUILD_UPDATED_CACHE, ctypes.c_void_p(self.cache.p))
            self._collect(cached, pairs, 0, count)
        return pairs, count[0] > 0

    def _collect(self, el, pairs, depth, count):
        if depth > 40:
            return
        kids = list(_array(el.get_ptr(E_GET_CACHED_CHILDREN)))
        # chat tab: a status icon beside a row button in the same group belongs to that row
        beside = ""
        for k in kids:
            if k.get_int(E_CACHED_CONTROLTYPE) == CT_STATUSBAR:
                beside = k.get_bstr(E_CACHED_NAME).strip() or beside
        for child in kids:
            if child.get_int(E_CACHED_CONTROLTYPE) in ROW_TYPES:
                count[0] += 1
                row = self._row(child)
                if row is None and not beside:
                    row = self._row_plain(child, kids)
                if row is None and beside:
                    row = self._row_beside(child, beside)
                    if row is not None:
                        beside = ""
                if row is not None:
                    pairs.append((row, child))
                    continue
            self._collect(child, pairs, depth + 1, count)

    def _row_beside(self, button, status):
        name = button.get_bstr(E_CACHED_NAME).strip()
        if not name or name.startswith(("More options", "Toggle sessions")):
            return None
        title = self._first_text(list(_array(button.get_ptr(E_GET_CACHED_CHILDREN))), 0) or name
        return Row(status, title, bool(button.get_int(E_CACHED_ISOFFSCREEN)), beside=True)

    def _row(self, button):
        kids = list(_array(button.get_ptr(E_GET_CACHED_CHILDREN)))
        if not kids:
            return None
        first = kids[0]
        if first.get_int(E_CACHED_CONTROLTYPE) not in (CT_STATUSBAR, CT_IMAGE):
            return self._row_inline(button, kids)
        status = first.get_bstr(E_CACHED_NAME).strip()
        if not status:
            return None
        title = self._first_text(kids[1:], 0)
        if not title:
            name = button.get_bstr(E_CACHED_NAME)
            title = name[len(status):].strip() if name.startswith(status) else name
        offscreen = bool(button.get_int(E_CACHED_ISOFFSCREEN))
        return Row(status, title, offscreen)

    def _row_inline(self, button, kids):
        """Claude 2.37+: Button{Group{Text <title>, [Text <subtitle>], StatusBar{Text <status>}, [Text <caption>]}}.
        The StatusBar is empty for an idle session (no label at all); the caption says where a cloud session runs."""
        title, status, env = "", None, None
        stack = [(k, 0) for k in reversed(kids)]
        while stack:
            el, depth = stack.pop()
            ct = el.get_int(E_CACHED_CONTROLTYPE)
            if ct == CT_STATUSBAR:
                status = el.get_bstr(E_CACHED_NAME).strip() or \
                    self._first_text(list(_array(el.get_ptr(E_GET_CACHED_CHILDREN))), 0) or "Idle"
            elif ct == CT_TEXT:
                text = el.get_bstr(E_CACHED_NAME).strip()
                if title and env is None:
                    env = parse_caption(text)
                title = title or text
            elif ct not in ROW_TYPES and depth < 3:      # a nested Button ("Hide nested sessions ...") isn't the row
                stack.extend((k, depth + 1) for k in reversed(list(_array(el.get_ptr(E_GET_CACHED_CHILDREN)))))
        if status is None or not title:
            return None
        return Row(status, title, bool(button.get_int(E_CACHED_ISOFFSCREEN)), False, *(env or (None, None)))

    def _row_plain(self, button, siblings):
        """Claude 5.11+: Button{Group{Group{Text <title>, [Text <subtitle>], [Text <status>], [Text <caption>]}}, ...}
        with a "More options for <title>" button beside it (only session rows have one). No status text = idle."""
        texts = self._texts(list(_array(button.get_ptr(E_GET_CACHED_CHILDREN))), 0)
        if not texts:
            return None
        title = texts[0]
        menu = "More options for " + title
        if not any(self._has_button(s, menu, 0) for s in siblings if s is not button):
            return None
        status, env = None, None
        for text in texts[1:]:
            caption = parse_caption(text)
            if caption:
                env = env or caption
            elif not _SUBTITLE.match(text):
                status = status or text
        return Row(status or "Idle", title, bool(button.get_int(E_CACHED_ISOFFSCREEN)), False,
                   *(env or (None, None)))

    def _texts(self, elements, depth):
        """The Texts inside a row, in order (not inside a nested Button such as "Hide nested sessions ...")."""
        out = []
        for el in elements:
            ct = el.get_int(E_CACHED_CONTROLTYPE)
            if ct == CT_TEXT:
                t = el.get_bstr(E_CACHED_NAME).strip()
                if t:
                    out.append(t)
            elif ct not in ROW_TYPES and depth < 4:
                out.extend(self._texts(list(_array(el.get_ptr(E_GET_CACHED_CHILDREN))), depth + 1))
        return out

    def _has_button(self, el, name, depth):
        if el.get_int(E_CACHED_CONTROLTYPE) == CT_BUTTON:
            return el.get_bstr(E_CACHED_NAME).strip() == name
        return depth < 3 and any(self._has_button(k, name, depth + 1)
                                 for k in _array(el.get_ptr(E_GET_CACHED_CHILDREN)))

    def _first_text(self, elements, depth):
        for el in elements:
            ct = el.get_int(E_CACHED_CONTROLTYPE)
            if ct == CT_TEXT:
                t = el.get_bstr(E_CACHED_NAME).strip()
                if t:
                    return t
            if depth < 4:
                t = self._first_text(list(_array(el.get_ptr(E_GET_CACHED_CHILDREN))), depth + 1)
                if t:
                    return t
        return ""

    # ---------- acting (only ever on your click on the blob or a card) ----------
    def select_session(self, hwnd, matches, timeout=2.0):
        """Click the sidebar entry whose title satisfies `matches(title)`: a session row on the current tab, else (on
        the chat tab) a chat or project session listed without a status, else a row on the Code tab. If the Code tab
        doesn't have it either, Claude is put back on the chat tab. Returns True on success."""
        self._attach(hwnd)
        r = self.read(hwnd)
        button = self._find_row_button(matches)
        if button is None and r.mode_found and r.code_selected is False:
            button = self._find_plain_button(matches)
        if button is not None:
            return self._invoke(button)
        if not (r.mode_found and r.code_selected is False):
            return False
        if not self._pick_tab("code"):
            return False
        self.sidebars = []
        end = time.time() + timeout
        while True:
            if not self._sidebars_alive():
                self._find_sidebars()
            button = self._find_row_button(matches)
            if button:
                return self._invoke(button)
            if time.time() > end:
                self._pick_tab("claude")             # not a Code session after all: leave you where you were
                self.sidebars = []
                return False
            time.sleep(0.1)

    def _pick_tab(self, tab):
        if not self._alive(self.mode):
            return False
        for radio in _array(self.mode.get_ptr(E_FIND_ALL, ctypes.c_int(SCOPE_CHILDREN),
                                              ctypes.c_void_p(self.true_cond.p))):
            if radio.get_bstr(E_CUR_NAME).split(",")[0].strip().lower() == tab:
                sel = radio.get_ptr(E_GET_CURRENT_PATTERN_AS, ctypes.c_int(PAT_SELECTIONITEM),
                                    _ref(IID_SelectionItemPattern))
                if sel:
                    sel.call(3, [])                                   # Select
                    return True
        return False

    def _find_row_button(self, matches):
        if not self.sidebars:
            return None
        try:
            pairs, _ = self._scan()
        except com.ComError:
            self.sidebars = []
            return None
        return next((b for row, b in pairs if matches(row.title)), None)

    def _find_plain_button(self, matches):
        try:
            for sb in self.sidebars:
                cached = sb.get_ptr(E_BUILD_UPDATED_CACHE, ctypes.c_void_p(self.cache.p))
                b = self._plain_button(cached, matches, 0)
                if b is not None:
                    return b
        except com.ComError:
            self.sidebars = []
        return None

    def _plain_button(self, el, matches, depth):
        # Chats (and project sessions with nothing to show) are a Button named with the title. A project's own
        # header Button holds a "Toggle sessions for ..." Button, so it's skipped.
        if depth > 40:
            return None
        for child in _array(el.get_ptr(E_GET_CACHED_CHILDREN)):
            if child.get_int(E_CACHED_CONTROLTYPE) in ROW_TYPES:
                kids = list(_array(child.get_ptr(E_GET_CACHED_CHILDREN)))
                if not any(k.get_int(E_CACHED_CONTROLTYPE) in ROW_TYPES for k in kids):
                    name = child.get_bstr(E_CACHED_NAME).strip()
                    if name and not name.startswith(("More options", "Toggle sessions")) and matches(name):
                        return child
            found = self._plain_button(child, matches, depth + 1)
            if found is not None:
                return found
        return None

    @staticmethod
    def _invoke(el):
        # The legacy default action clicks without activating Claude's window. (The Invoke pattern makes Claude come
        # to the front and take keyboard focus, verified on this machine.) Invoke is only a fallback.
        try:
            leg = el.get_ptr(E_GET_CURRENT_PATTERN_AS, ctypes.c_int(PAT_LEGACY), _ref(IID_LegacyPattern))
            if leg:
                leg.call(4, [])                                         # DoDefaultAction
                return True
        except com.ComError:
            pass
        try:
            inv = el.get_ptr(E_GET_CURRENT_PATTERN_AS, ctypes.c_int(PAT_INVOKE), _ref(IID_InvokePattern))
            if inv:
                inv.call(3, [])                                         # Invoke
                return True
        except com.ComError:
            pass
        return False
