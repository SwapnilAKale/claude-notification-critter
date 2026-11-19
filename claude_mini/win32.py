"""Minimal Win32 bindings via ctypes (no third-party packages)."""
import ctypes
from ctypes import wintypes as W

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
shcore = ctypes.WinDLL("shcore", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
WINEVENTPROC = ctypes.WINFUNCTYPE(None, W.HANDLE, W.DWORD, W.HWND, W.LONG, W.LONG, W.DWORD, W.DWORD)
WNDENUMPROC = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)


class POINT(ctypes.Structure):
    _fields_ = [("x", W.LONG), ("y", W.LONG)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", W.LONG), ("cy", W.LONG)]


class RECT(ctypes.Structure):
    _fields_ = [("left", W.LONG), ("top", W.LONG), ("right", W.LONG), ("bottom", W.LONG)]

    @property
    def width(self):
        return self.right - self.left

    @property
    def height(self):
        return self.bottom - self.top

    def contains(self, x, y):
        return self.left <= x < self.right and self.top <= y < self.bottom

    def copy(self):
        return RECT(self.left, self.top, self.right, self.bottom)

    def __repr__(self):
        return "RECT(%d,%d %dx%d)" % (self.left, self.top, self.width, self.height)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", W.UINT), ("style", W.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", W.HINSTANCE), ("hIcon", W.HICON),
                ("hCursor", W.HANDLE), ("hbrBackground", W.HBRUSH), ("lpszMenuName", W.LPCWSTR),
                ("lpszClassName", W.LPCWSTR), ("hIconSm", W.HICON)]


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", W.HWND), ("message", W.UINT), ("wParam", W.WPARAM), ("lParam", W.LPARAM),
                ("time", W.DWORD), ("pt", POINT), ("lPrivate", W.DWORD)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", W.DWORD), ("biWidth", W.LONG), ("biHeight", W.LONG), ("biPlanes", W.WORD),
                ("biBitCount", W.WORD), ("biCompression", W.DWORD), ("biSizeImage", W.DWORD),
                ("biXPelsPerMeter", W.LONG), ("biYPelsPerMeter", W.LONG), ("biClrUsed", W.DWORD),
                ("biClrImportant", W.DWORD)]


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [("length", W.UINT), ("flags", W.UINT), ("showCmd", W.UINT), ("ptMinPosition", POINT),
                ("ptMaxPosition", POINT), ("rcNormalPosition", RECT)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", W.DWORD), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", W.DWORD)]


class TRACKMOUSEEVENT(ctypes.Structure):
    _fields_ = [("cbSize", W.DWORD), ("dwFlags", W.DWORD), ("hwndTrack", W.HWND), ("dwHoverTime", W.DWORD)]


class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16),
                ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, s):
        s = s.strip("{}").replace("-", "")
        g = cls()
        g.Data1 = int(s[0:8], 16)
        g.Data2 = int(s[8:12], 16)
        g.Data3 = int(s[12:16], 16)
        for i in range(8):
            g.Data4[i] = int(s[16 + 2 * i:18 + 2 * i], 16)
        return g


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", W.DWORD), ("hWnd", W.HWND), ("uID", W.UINT), ("uFlags", W.UINT),
                ("uCallbackMessage", W.UINT), ("hIcon", W.HICON), ("szTip", W.WCHAR * 128),
                ("dwState", W.DWORD), ("dwStateMask", W.DWORD), ("szInfo", W.WCHAR * 256),
                ("uVersion", W.UINT), ("szInfoTitle", W.WCHAR * 64), ("dwInfoFlags", W.DWORD),
                ("guidItem", GUID), ("hBalloonIcon", W.HICON)]


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", W.UINT), ("dwTime", W.DWORD)]


def _fn(dll, name, restype, *argtypes):
    f = getattr(dll, name)
    f.restype = restype
    f.argtypes = list(argtypes)
    return f


# --- user32 ---
SetProcessDpiAwarenessContext = _fn(user32, "SetProcessDpiAwarenessContext", W.BOOL, ctypes.c_void_p)
GetThreadDpiAwarenessContext = _fn(user32, "GetThreadDpiAwarenessContext", ctypes.c_void_p)
GetAwarenessFromDpiAwarenessContext = _fn(user32, "GetAwarenessFromDpiAwarenessContext", ctypes.c_int, ctypes.c_void_p)
GetDpiForWindow = _fn(user32, "GetDpiForWindow", W.UINT, W.HWND)
GetSystemMetricsForDpi = _fn(user32, "GetSystemMetricsForDpi", ctypes.c_int, ctypes.c_int, W.UINT)
RegisterClassExW = _fn(user32, "RegisterClassExW", W.ATOM, ctypes.POINTER(WNDCLASSEXW))
CreateWindowExW = _fn(user32, "CreateWindowExW", W.HWND, W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, ctypes.c_int,
                      ctypes.c_int, ctypes.c_int, ctypes.c_int, W.HWND, W.HMENU, W.HINSTANCE, W.LPVOID)
DestroyWindow = _fn(user32, "DestroyWindow", W.BOOL, W.HWND)
DefWindowProcW = _fn(user32, "DefWindowProcW", LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
GetMessageW = _fn(user32, "GetMessageW", W.BOOL, ctypes.POINTER(MSG), W.HWND, W.UINT, W.UINT)
TranslateMessage = _fn(user32, "TranslateMessage", W.BOOL, ctypes.POINTER(MSG))
DispatchMessageW = _fn(user32, "DispatchMessageW", LRESULT, ctypes.POINTER(MSG))
PostMessageW = _fn(user32, "PostMessageW", W.BOOL, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
PostQuitMessage = _fn(user32, "PostQuitMessage", None, ctypes.c_int)
RegisterWindowMessageW = _fn(user32, "RegisterWindowMessageW", W.UINT, W.LPCWSTR)
ShowWindow = _fn(user32, "ShowWindow", W.BOOL, W.HWND, ctypes.c_int)
SetWindowPos = _fn(user32, "SetWindowPos", W.BOOL, W.HWND, W.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                   ctypes.c_int, W.UINT)
GetWindowRect = _fn(user32, "GetWindowRect", W.BOOL, W.HWND, ctypes.POINTER(RECT))
GetWindowPlacement = _fn(user32, "GetWindowPlacement", W.BOOL, W.HWND, ctypes.POINTER(WINDOWPLACEMENT))
SetWindowPlacement = _fn(user32, "SetWindowPlacement", W.BOOL, W.HWND, ctypes.POINTER(WINDOWPLACEMENT))
UpdateLayeredWindow = _fn(user32, "UpdateLayeredWindow", W.BOOL, W.HWND, W.HDC, ctypes.POINTER(POINT),
                          ctypes.POINTER(SIZE), W.HDC, ctypes.POINTER(POINT), W.COLORREF,
                          ctypes.POINTER(BLENDFUNCTION), W.DWORD)
GetDC = _fn(user32, "GetDC", W.HDC, W.HWND)
ReleaseDC = _fn(user32, "ReleaseDC", ctypes.c_int, W.HWND, W.HDC)
SetCapture = _fn(user32, "SetCapture", W.HWND, W.HWND)
ReleaseCapture = _fn(user32, "ReleaseCapture", W.BOOL)
GetCapture = _fn(user32, "GetCapture", W.HWND)
GetCursorPos = _fn(user32, "GetCursorPos", W.BOOL, ctypes.POINTER(POINT))
TrackMouseEvent = _fn(user32, "TrackMouseEvent", W.BOOL, ctypes.POINTER(TRACKMOUSEEVENT))
SetTimer = _fn(user32, "SetTimer", ctypes.c_size_t, W.HWND, ctypes.c_size_t, W.UINT, ctypes.c_void_p)
KillTimer = _fn(user32, "KillTimer", W.BOOL, W.HWND, ctypes.c_size_t)
LoadCursorW = _fn(user32, "LoadCursorW", W.HANDLE, W.HINSTANCE, ctypes.c_void_p)
SetCursor = _fn(user32, "SetCursor", W.HANDLE, W.HANDLE)
MonitorFromPoint = _fn(user32, "MonitorFromPoint", W.HMONITOR, POINT, W.DWORD)
MonitorFromWindow = _fn(user32, "MonitorFromWindow", W.HMONITOR, W.HWND, W.DWORD)
GetMonitorInfoW = _fn(user32, "GetMonitorInfoW", W.BOOL, W.HMONITOR, ctypes.POINTER(MONITORINFO))
EnumWindows = _fn(user32, "EnumWindows", W.BOOL, WNDENUMPROC, W.LPARAM)
GetWindowThreadProcessId = _fn(user32, "GetWindowThreadProcessId", W.DWORD, W.HWND, ctypes.POINTER(W.DWORD))
IsWindow = _fn(user32, "IsWindow", W.BOOL, W.HWND)
IsWindowVisible = _fn(user32, "IsWindowVisible", W.BOOL, W.HWND)
IsIconic = _fn(user32, "IsIconic", W.BOOL, W.HWND)
IsZoomed = _fn(user32, "IsZoomed", W.BOOL, W.HWND)
GetWindowTextW = _fn(user32, "GetWindowTextW", ctypes.c_int, W.HWND, W.LPWSTR, ctypes.c_int)
GetClassNameW = _fn(user32, "GetClassNameW", ctypes.c_int, W.HWND, W.LPWSTR, ctypes.c_int)
GetWindowLongPtrW = _fn(user32, "GetWindowLongPtrW", ctypes.c_ssize_t, W.HWND, ctypes.c_int)
SetWindowLongPtrW = _fn(user32, "SetWindowLongPtrW", ctypes.c_ssize_t, W.HWND, ctypes.c_int, ctypes.c_ssize_t)
GetForegroundWindow = _fn(user32, "GetForegroundWindow", W.HWND)
SetForegroundWindow = _fn(user32, "SetForegroundWindow", W.BOOL, W.HWND)
GetAncestor = _fn(user32, "GetAncestor", W.HWND, W.HWND, W.UINT)
GetWindow = _fn(user32, "GetWindow", W.HWND, W.HWND, W.UINT)
SetWinEventHook = _fn(user32, "SetWinEventHook", W.HANDLE, W.DWORD, W.DWORD, W.HMODULE, WINEVENTPROC, W.DWORD,
                      W.DWORD, W.DWORD)
UnhookWinEvent = _fn(user32, "UnhookWinEvent", W.BOOL, W.HANDLE)
CreatePopupMenu = _fn(user32, "CreatePopupMenu", W.HMENU)
AppendMenuW = _fn(user32, "AppendMenuW", W.BOOL, W.HMENU, W.UINT, ctypes.c_size_t, W.LPCWSTR)
TrackPopupMenu = _fn(user32, "TrackPopupMenu", W.BOOL, W.HMENU, W.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                     W.HWND, ctypes.c_void_p)
DestroyMenu = _fn(user32, "DestroyMenu", W.BOOL, W.HMENU)
DestroyIcon = _fn(user32, "DestroyIcon", W.BOOL, W.HICON)
AllowSetForegroundWindow = _fn(user32, "AllowSetForegroundWindow", W.BOOL, W.DWORD)
SystemParametersInfoW = _fn(user32, "SystemParametersInfoW", W.BOOL, W.UINT, W.UINT, ctypes.c_void_p, W.UINT)
GetSystemMetrics = _fn(user32, "GetSystemMetrics", ctypes.c_int, ctypes.c_int)
OpenClipboard = _fn(user32, "OpenClipboard", W.BOOL, W.HWND)
EmptyClipboard = _fn(user32, "EmptyClipboard", W.BOOL)
SetClipboardData = _fn(user32, "SetClipboardData", W.HANDLE, W.UINT, W.HANDLE)
CloseClipboard = _fn(user32, "CloseClipboard", W.BOOL)
GetGuiResources = _fn(user32, "GetGuiResources", W.DWORD, W.HANDLE, W.DWORD)
MessageBoxW = _fn(user32, "MessageBoxW", ctypes.c_int, W.HWND, W.LPCWSTR, W.LPCWSTR, W.UINT)
WindowFromPoint = _fn(user32, "WindowFromPoint", W.HWND, POINT)
GetLastInputInfo = _fn(user32, "GetLastInputInfo", W.BOOL, ctypes.POINTER(LASTINPUTINFO))
GetAsyncKeyState = _fn(user32, "GetAsyncKeyState", ctypes.c_short, ctypes.c_int)


_dwmapi = ctypes.WinDLL("dwmapi")
_dwmapi.DwmGetWindowAttribute.argtypes = [W.HWND, W.DWORD, ctypes.c_void_p, W.DWORD]
_dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long


def is_cloaked(hwnd):
    """Windows that are 'visible' but not actually shown (suspended UWP apps, other virtual desktops)."""
    v = W.DWORD(0)
    hr = _dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(v), ctypes.sizeof(v))   # DWMWA_CLOAKED
    return hr == 0 and v.value != 0


def last_input_tick():
    """Tick of your last mouse/keyboard input anywhere (to tell your actions apart from an app's)."""
    li = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
    GetLastInputInfo(ctypes.byref(li))
    return li.dwTime

# --- gdi32 ---
CreateCompatibleDC = _fn(gdi32, "CreateCompatibleDC", W.HDC, W.HDC)
DeleteDC = _fn(gdi32, "DeleteDC", W.BOOL, W.HDC)
CreateDIBSection = _fn(gdi32, "CreateDIBSection", W.HBITMAP, W.HDC, ctypes.POINTER(BITMAPINFOHEADER), W.UINT,
                       ctypes.POINTER(ctypes.c_void_p), W.HANDLE, W.DWORD)
SelectObject = _fn(gdi32, "SelectObject", W.HGDIOBJ, W.HDC, W.HGDIOBJ)
DeleteObject = _fn(gdi32, "DeleteObject", W.BOOL, W.HGDIOBJ)

# --- kernel32 ---
GetModuleHandleW = _fn(kernel32, "GetModuleHandleW", W.HMODULE, W.LPCWSTR)
CreateMutexW = _fn(kernel32, "CreateMutexW", W.HANDLE, ctypes.c_void_p, W.BOOL, W.LPCWSTR)
CloseHandle = _fn(kernel32, "CloseHandle", W.BOOL, W.HANDLE)
OpenProcess = _fn(kernel32, "OpenProcess", W.HANDLE, W.DWORD, W.BOOL, W.DWORD)
QueryFullProcessImageNameW = _fn(kernel32, "QueryFullProcessImageNameW", W.BOOL, W.HANDLE, W.DWORD, W.LPWSTR,
                                 ctypes.POINTER(W.DWORD))
GetCurrentProcessId = _fn(kernel32, "GetCurrentProcessId", W.DWORD)
GetCurrentProcess = _fn(kernel32, "GetCurrentProcess", W.HANDLE)
GlobalAlloc = _fn(kernel32, "GlobalAlloc", W.HGLOBAL, W.UINT, ctypes.c_size_t)
GlobalLock = _fn(kernel32, "GlobalLock", ctypes.c_void_p, W.HGLOBAL)
GlobalUnlock = _fn(kernel32, "GlobalUnlock", W.BOOL, W.HGLOBAL)
GlobalFree = _fn(kernel32, "GlobalFree", W.HGLOBAL, W.HGLOBAL)

# --- shell32 / shcore ---
Shell_NotifyIconW = _fn(shell32, "Shell_NotifyIconW", W.BOOL, W.DWORD, ctypes.POINTER(NOTIFYICONDATAW))
ShellExecuteW = _fn(shell32, "ShellExecuteW", W.HINSTANCE, W.HWND, W.LPCWSTR, W.LPCWSTR, W.LPCWSTR, W.LPCWSTR,
                    ctypes.c_int)
GetDpiForMonitor = _fn(shcore, "GetDpiForMonitor", ctypes.c_long, W.HMONITOR, ctypes.c_int,
                       ctypes.POINTER(W.UINT), ctypes.POINTER(W.UINT))

# --- constants ---
WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TRANSPARENT = 0x00000020
GWL_EXSTYLE = -20
GWL_STYLE = -16
WS_THICKFRAME = 0x00040000
WS_SYSMENU = 0x00080000
WS_CAPTION = 0x00C00000
SW_HIDE, SW_SHOWNORMAL, SW_SHOWMAXIMIZED, SW_SHOWNOACTIVATE, SW_SHOW, SW_SHOWNA, SW_RESTORE = 0, 1, 3, 4, 5, 8, 9
HWND_TOPMOST = W.HWND(-1)
HWND_NOTOPMOST = W.HWND(-2)
HWND_TOP = W.HWND(0)
HWND_BOTTOM = W.HWND(1)
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x1, 0x2, 0x4, 0x10, 0x40
SWP_NOOWNERZORDER = 0x200
ULW_ALPHA = 2
AC_SRC_OVER, AC_SRC_ALPHA = 0, 1
MONITOR_DEFAULTTONULL, MONITOR_DEFAULTTONEAREST = 0, 2
GA_ROOT, GA_ROOTOWNER = 2, 3
GW_OWNER = 4
EVENT_SYSTEM_FOREGROUND = 0x0003
WINEVENT_OUTOFCONTEXT, WINEVENT_SKIPOWNPROCESS = 0x0, 0x2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
IDC_ARROW, IDC_HAND = 32512, 32649
TME_LEAVE = 0x2
MF_STRING, MF_GRAYED, MF_SEPARATOR = 0x0, 0x1, 0x800
TPM_RIGHTBUTTON, TPM_RETURNCMD, TPM_NONOTIFY = 0x2, 0x100, 0x80
NIM_ADD, NIM_MODIFY, NIM_DELETE, NIM_SETVERSION = 0, 1, 2, 4
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_SHOWTIP, NIF_INFO = 0x1, 0x2, 0x4, 0x80, 0x10
NIIF_WARNING = 0x2
WM_DESTROY, WM_CLOSE, WM_QUIT, WM_TIMER, WM_MOUSEMOVE = 0x0002, 0x0010, 0x0012, 0x0113, 0x0200
WM_LBUTTONDOWN, WM_LBUTTONUP, WM_RBUTTONUP, WM_CONTEXTMENU = 0x0201, 0x0202, 0x0205, 0x007B
WM_MOUSELEAVE, WM_CAPTURECHANGED, WM_MOUSEACTIVATE, WM_SETCURSOR = 0x02A3, 0x0215, 0x0021, 0x0020
WM_DPICHANGED, WM_DISPLAYCHANGE, WM_NCHITTEST, WM_APP = 0x02E0, 0x007E, 0x0084, 0x8000
WM_SETTINGCHANGE = 0x001A
MA_NOACTIVATE = 3
NIN_SELECT = 0x400
DIB_RGB_COLORS = 0


def hiword(v):
    return (v >> 16) & 0xFFFF


def loword(v):
    return v & 0xFFFF


def signed16(v):
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def cursor_pos():
    p = POINT()
    GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def window_rect(hwnd):
    r = RECT()
    GetWindowRect(hwnd, ctypes.byref(r))
    return r


def monitor_info(hmon):
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    GetMonitorInfoW(hmon, ctypes.byref(mi))
    return mi


def monitor_at(x, y, nearest=True):
    return MonitorFromPoint(POINT(x, y), MONITOR_DEFAULTTONEAREST if nearest else MONITOR_DEFAULTTONULL)


def window_text(hwnd):
    buf = ctypes.create_unicode_buffer(512)
    GetWindowTextW(hwnd, buf, 512)
    return buf.value


def class_name(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    GetClassNameW(hwnd, buf, 256)
    return buf.value


def window_pid(hwnd):
    pid = W.DWORD()
    GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def process_path(pid):
    h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = W.DWORD(1024)
        if QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return None
    finally:
        CloseHandle(h)


def set_topmost(hwnd, topmost=True):
    SetWindowPos(hwnd, HWND_TOPMOST if topmost else HWND_NOTOPMOST, 0, 0, 0, 0,
                 SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


def hwnd_value(h):
    """HWNDs come back as int or None from ctypes; normalise to int (0 = none)."""
    return int(h or 0)


def copy_text(hwnd, text):
    """Put text on the clipboard. True if it got there."""
    data = ctypes.create_unicode_buffer(text)
    h = GlobalAlloc(0x0002, ctypes.sizeof(data))        # GMEM_MOVEABLE
    if not h:
        return False
    ctypes.memmove(GlobalLock(h), data, ctypes.sizeof(data))
    GlobalUnlock(h)
    if not OpenClipboard(hwnd):
        GlobalFree(h)
        return False
    try:
        EmptyClipboard()
        if SetClipboardData(13, h):                     # CF_UNICODETEXT: the clipboard owns it now
            return True
        GlobalFree(h)
        return False
    finally:
        CloseClipboard()
