"""Find the real Claude window and bring it forward. The blob never resizes, pins or minimizes Claude."""
from . import win32

CLAUDE_AUMID = "Claude_pzs8sxrjxfjjc!Claude"

_path_cache = {}


def is_claude_pid(pid):
    """True for the Claude desktop app's own processes (MSIX package under WindowsApps)."""
    if pid in _path_cache:
        return _path_cache[pid]
    path = (win32.process_path(pid) or "").lower()
    ok = "\\windowsapps\\claude_" in path and path.endswith("\\claude.exe")
    if len(_path_cache) > 512:
        _path_cache.clear()
    _path_cache[pid] = ok
    return ok


def claude_window_state():
    """(hwnd, state) with state "visible", "hidden" (running in the tray, window closed) or "not running"."""
    h = find_main_window()
    if h:
        return h, "visible"
    h = find_main_window(include_hidden=True)
    if h:
        return h, "hidden"
    return 0, "not running"


def find_main_window(include_hidden=False):
    """Claude's main window: the largest titled, framed Chrome_WidgetWin_1 window owned by a Claude process.

    Claude also owns borderless, click-through overlays (e.g. a full-screen border glow); those are skipped."""
    best = [0, 0]

    @win32.WNDENUMPROC
    def cb(h, _):
        if win32.class_name(h) != "Chrome_WidgetWin_1":
            return True
        if not include_hidden and not win32.IsWindowVisible(h):
            return True
        if win32.GetWindow(h, win32.GW_OWNER):
            return True                       # owned popups (menus, tooltips) aren't the main window
        style = win32.GetWindowLongPtrW(h, win32.GWL_STYLE)
        ex = win32.GetWindowLongPtrW(h, win32.GWL_EXSTYLE)
        if not (style & win32.WS_THICKFRAME and style & win32.WS_CAPTION):
            return True
        if ex & (win32.WS_EX_TRANSPARENT | win32.WS_EX_NOACTIVATE):
            return True
        if not win32.window_text(h):
            return True
        if not is_claude_pid(win32.window_pid(h)):
            return True
        r = win32.window_rect(h)
        area = 1 if win32.IsIconic(h) else r.width * r.height
        if area > best[1]:
            best[0], best[1] = win32.hwnd_value(h), area
        return True

    win32.EnumWindows(cb, 0)
    return best[0]


IN_VIEW_FRACTION = 0.5       # at least this much of Claude's window must be uncovered for it to count as "in view"


def claude_in_view(hwnd):
    """True if you can actually see Claude's window: it's the window you're using, or at least half of it is
    uncovered on screen (e.g. side by side with your browser). Merely being open behind another app doesn't count."""
    if not hwnd or not win32.IsWindowVisible(hwnd) or win32.IsIconic(hwnd):
        return False
    if win32.hwnd_value(win32.GetForegroundWindow()) == hwnd:
        return True
    r = win32.window_rect(hwnd)
    mon = win32.monitor_info(win32.MonitorFromWindow(hwnd, win32.MONITOR_DEFAULTTONEAREST)).rcMonitor
    left, top = max(r.left, mon.left), max(r.top, mon.top)
    right, bottom = min(r.right, mon.right), min(r.bottom, mon.bottom)
    if right <= left or bottom <= top:
        return False
    covers = _windows_above(hwnd)
    cols, rows = 16, 10
    seen = total = 0
    for i in range(cols):
        x = left + (right - left) * (i + 0.5) / cols
        for j in range(rows):
            y = top + (bottom - top) * (j + 0.5) / rows
            total += 1
            if not any(c.left <= x < c.right and c.top <= y < c.bottom for c in covers):
                seen += 1
    return seen >= IN_VIEW_FRACTION * total


def _windows_above(hwnd):
    """Rectangles of the visible windows stacked above Claude that actually hide it."""
    rects, done = [], [False]
    me = win32.GetCurrentProcessId()

    @win32.WNDENUMPROC
    def cb(h, _):
        if win32.hwnd_value(h) == hwnd:
            done[0] = True
            return False                      # EnumWindows walks top-down: everything after this is below Claude
        if not win32.IsWindowVisible(h) or win32.IsIconic(h):
            return True
        ex = win32.GetWindowLongPtrW(h, win32.GWL_EXSTYLE)
        if ex & win32.WS_EX_TRANSPARENT or win32.is_cloaked(h):
            return True                       # click-through overlays, hidden UWP/other-desktop windows
        pid = win32.window_pid(h)
        if pid == me or is_claude_pid(pid):
            return True                       # the blob itself, Claude's own popups and overlays
        r = win32.window_rect(h)
        if r.width > 0 and r.height > 0:
            rects.append(r)
        return True

    win32.EnumWindows(cb, 0)
    return rects


def launch_claude():
    """Start Claude, or bring back its window if it's running in the tray."""
    win32.ShellExecuteW(None, "open", "shell:AppsFolder\\" + CLAUDE_AUMID, None, None, win32.SW_SHOWNORMAL)


def bring_to_front(hwnd):
    """Show Claude normally, as if you had clicked it on the taskbar."""
    if not hwnd:
        launch_claude()
        return
    if win32.IsIconic(hwnd):
        win32.ShowWindow(hwnd, win32.SW_RESTORE)
    win32.SetForegroundWindow(hwnd)
