"""One registered window class; messages are dispatched to the Python object owning each HWND."""
import ctypes
import sys
import traceback

from . import diag, win32

_CLASS = "ClaudeMiniWindow"
_windows = {}
_registered = False


def _wndproc(hwnd, msg, wparam, lparam):
    obj = _windows.get(win32.hwnd_value(hwnd))
    if msg == win32.WM_CLOSE:
        # Only Quit in the tray menu ends Claude mini; a close sent from outside (Alt+F4, a tool closing windows)
        # would otherwise destroy this window and leave the app half there.
        diag.log("ignored a request to close the %r window" % (getattr(obj, "__class__", type(None)).__name__,))
        return 0
    if obj is not None:
        try:
            r = obj.on_message(msg, wparam, lparam)
            if r is not None:
                return r
        except Exception:
            # Never let a Python exception unwind through the Win32 callback.
            traceback.print_exc(file=sys.stderr)
    return win32.DefWindowProcW(hwnd, msg, wparam, lparam)


_proc = win32.WNDPROC(_wndproc)


def _register():
    global _registered
    if _registered:
        return
    wc = win32.WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(win32.WNDCLASSEXW)
    wc.lpfnWndProc = _proc
    wc.hInstance = win32.GetModuleHandleW(None)
    wc.hCursor = win32.LoadCursorW(None, ctypes.c_void_p(win32.IDC_ARROW))
    wc.lpszClassName = _CLASS
    if not win32.RegisterClassExW(ctypes.byref(wc)):
        raise ctypes.WinError(ctypes.get_last_error())
    _registered = True


class Window(object):
    def __init__(self, ex_style, style=win32.WS_POPUP, title="Claude mini", x=0, y=0, w=1, h=1):
        _register()
        self.hwnd = 0
        hwnd = win32.CreateWindowExW(ex_style, _CLASS, title, style, x, y, w, h, None, None,
                                     win32.GetModuleHandleW(None), None)
        if not hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        self.hwnd = win32.hwnd_value(hwnd)
        _windows[self.hwnd] = self

    def on_message(self, msg, wparam, lparam):
        return None

    def destroy(self):
        if self.hwnd:
            h = self.hwnd
            _windows.pop(h, None)
            self.hwnd = 0
            win32.DestroyWindow(h)
