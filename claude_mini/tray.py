"""System tray icon (the critter, with the blob's status marks) and a small menu (status line, open full size, hide /
show the critter, reset position, copy the diagnostic log, restart, quit)."""
import ctypes

from . import avatar, model, win32
from . import gdiplus as G
from .wnd import Window

WM_TRAY = win32.WM_APP + 1
CMD_OPEN, CMD_RESET, CMD_QUIT, CMD_COPY_LOG, CMD_HIDE, CMD_RESTART = 1, 2, 3, 4, 5, 6
SM_CXSMICON = 49


def draw_icon(cv, n, dot=None, warn=False, faded=False):
    """The standing critter filling an n x n icon, with an optional status dot (hex colour) bottom-right and a
    small warning mark bottom-left; faded: Claude isn't running."""
    avatar.draw_fitted(cv, 0, 0, n, n * 0.84, avatar.standing_frame(), alpha=0.45 if faded else 1.0)
    r = max(3.0, n * 0.2)
    if dot:
        cv.fill_ellipse(n - 2 * r - 0.5, n - 2 * r - 0.5, 2 * r, 2 * r, G.hexc(dot))
        cv.draw_ellipse(n - 2 * r - 0.5, n - 2 * r - 0.5, 2 * r, 2 * r, G.hexc("FFFFFF"), max(1.0, n / 20.0))
    if warn:
        h = n * 0.42
        pts = [(h / 2 + 0.5, n - h - 0.5), (h + 0.5, n - 0.5), (0.5, n - 0.5)]
        cv.fill_polygon(pts, G.hexc("FACC15"), G.hexc("1C1B19"), max(1.0, n / 24.0))


def icon_marks(snap):
    """(dot colour or None, warning mark?, faded?) for a snapshot."""
    dot = {model.NEEDS_ME: "F59E0B", model.NEW: "3B82F6"}.get(snap.level)
    return dot, snap.claude_running and snap.degraded, not snap.claude_running


class Tray(Window):
    def __init__(self, on_open, on_reset, on_quit, on_copy_log=None, on_hide=None, on_restart=None):
        Window.__init__(self, win32.WS_EX_TOOLWINDOW, title="Claude mini tray")
        self.on_open, self.on_reset, self.on_quit, self.on_copy_log = on_open, on_reset, on_quit, on_copy_log
        self.on_hide = on_hide                  # called with True (hide the critter) or False (bring it back)
        self.on_restart = on_restart            # start the critter again from its files (loads updates)
        self.critter_hidden = False
        self.status = "Starting…"
        self.icon = None
        self.icon_key = None
        self.taskbar_created = win32.RegisterWindowMessageW("TaskbarCreated")
        self.nid = win32.NOTIFYICONDATAW()
        self.nid.cbSize = ctypes.sizeof(win32.NOTIFYICONDATAW)
        self.nid.hWnd = self.hwnd
        self.nid.uID = 1
        self.nid.uCallbackMessage = WM_TRAY
        self._make_icon((None, False, False))
        self._add()

    def _add(self):
        self.nid.uFlags = win32.NIF_MESSAGE | win32.NIF_ICON | win32.NIF_TIP
        self.nid.hIcon = self.icon
        self.nid.szTip = self._tip()
        win32.Shell_NotifyIconW(win32.NIM_ADD, ctypes.byref(self.nid))

    def _tip(self):
        t = "Claude mini: " + self.status.replace("\n", "  ")
        return t if len(t) < 127 else t[:125] + "…"

    def update(self, snap):
        self.status = snap.summary()
        self._make_icon(icon_marks(snap))
        self.nid.uFlags = win32.NIF_ICON | win32.NIF_TIP
        self.nid.hIcon = self.icon
        self.nid.szTip = self._tip()
        win32.Shell_NotifyIconW(win32.NIM_MODIFY, ctypes.byref(self.nid))

    def alert(self, title, text):
        """A Windows notification from the tray icon (shown in Action Center too): for something the critter itself
        has to tell you, such as a source of its reading that stopped working."""
        self.nid.uFlags = win32.NIF_INFO
        self.nid.szInfoTitle = title[:63]
        self.nid.szInfo = text if len(text) < 255 else text[:253] + "…"
        self.nid.dwInfoFlags = win32.NIIF_WARNING
        win32.Shell_NotifyIconW(win32.NIM_MODIFY, ctypes.byref(self.nid))
        self.nid.szInfo = ""                      # later icon / tip updates don't repeat it

    def _icon_size(self):
        # the tray's small-icon size at this screen's scaling (20 px at 125%), so the pixel art isn't rescaled
        return win32.GetSystemMetricsForDpi(SM_CXSMICON, win32.GetDpiForWindow(self.hwnd)) or 16

    def _make_icon(self, marks):
        n = self._icon_size()
        key = (marks, n)
        if key == self.icon_key:
            return
        self.icon_key = key
        cv = G.Canvas(n, n)
        cv.clear(0)
        draw_icon(cv, n, *marks)
        old = self.icon
        self.icon = cv.to_hicon()
        cv.close()
        if old:
            win32.DestroyIcon(old)

    def _menu(self):
        m = win32.CreatePopupMenu()
        for line in self.status.split("\n"):
            win32.AppendMenuW(m, win32.MF_STRING | win32.MF_GRAYED, 0, line)
        win32.AppendMenuW(m, win32.MF_SEPARATOR, 0, None)
        win32.AppendMenuW(m, win32.MF_STRING, CMD_OPEN, "Open Claude full size")
        if self.on_hide is not None:
            win32.AppendMenuW(m, win32.MF_STRING, CMD_HIDE, "Show critter" if self.critter_hidden else "Hide critter")
        win32.AppendMenuW(m, win32.MF_STRING | (win32.MF_GRAYED if self.critter_hidden else 0), CMD_RESET,
                          "Reset blob position")
        if self.on_copy_log is not None:
            win32.AppendMenuW(m, win32.MF_STRING, CMD_COPY_LOG, "Copy diagnostic log")
        win32.AppendMenuW(m, win32.MF_SEPARATOR, 0, None)
        if self.on_restart is not None:
            win32.AppendMenuW(m, win32.MF_STRING, CMD_RESTART, "Restart for updates")
        win32.AppendMenuW(m, win32.MF_STRING, CMD_QUIT, "Quit")
        x, y = win32.cursor_pos()
        win32.SetForegroundWindow(self.hwnd)   # required so the menu closes when clicking elsewhere
        cmd = win32.TrackPopupMenu(m, win32.TPM_RIGHTBUTTON | win32.TPM_RETURNCMD | win32.TPM_NONOTIFY,
                                   x, y, 0, self.hwnd, None)
        win32.DestroyMenu(m)
        if cmd == CMD_OPEN:
            self.on_open()
        elif cmd == CMD_RESET:
            self.on_reset()
        elif cmd == CMD_COPY_LOG:
            self.on_copy_log()
        elif cmd == CMD_HIDE:
            self.on_hide(not self.critter_hidden)
        elif cmd == CMD_RESTART:
            self.on_restart()
        elif cmd == CMD_QUIT:
            self.on_quit()

    def on_message(self, msg, wparam, lparam):
        if msg == WM_TRAY:
            ev = win32.loword(lparam)
            if ev in (win32.WM_RBUTTONUP, win32.WM_CONTEXTMENU, win32.WM_LBUTTONUP):
                self._menu()
            return 0
        if msg == self.taskbar_created:
            self._add()                         # Explorer restarted
            return 0
        if msg == win32.WM_DPICHANGED:
            self.icon_key = None                # redraw at the new size on the next update
            return None
        return None

    def destroy(self):
        win32.Shell_NotifyIconW(win32.NIM_DELETE, ctypes.byref(self.nid))
        if self.icon:
            win32.DestroyIcon(self.icon)
            self.icon = None
        Window.destroy(self)
