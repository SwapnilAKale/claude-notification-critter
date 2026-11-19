"""Bare-bones COM / WinRT calling via ctypes vtables (no comtypes, no pywinrt).

Vtable slot numbers used by callers were read from Windows' own metadata (UIAutomationCore type library and the
WinRT .winmd files), not guessed.
"""
import ctypes
import time
from ctypes import wintypes as W

from .win32 import GUID

ole32 = ctypes.WinDLL("ole32")
oleaut32 = ctypes.WinDLL("oleaut32")
combase = ctypes.WinDLL("combase")

ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, W.DWORD]
ole32.CoInitializeEx.restype = ctypes.c_long
ole32.CoUninitialize.argtypes = []
ole32.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p, W.DWORD, ctypes.POINTER(GUID),
                                   ctypes.POINTER(ctypes.c_void_p)]
ole32.CoCreateInstance.restype = ctypes.c_long
oleaut32.SysAllocString.argtypes = [W.LPCWSTR]
oleaut32.SysAllocString.restype = ctypes.c_void_p
oleaut32.SysFreeString.argtypes = [ctypes.c_void_p]
oleaut32.SysStringLen.argtypes = [ctypes.c_void_p]
oleaut32.SysStringLen.restype = W.UINT
combase.WindowsCreateString.argtypes = [W.LPCWSTR, W.UINT, ctypes.POINTER(ctypes.c_void_p)]
combase.WindowsCreateString.restype = ctypes.c_long
combase.WindowsDeleteString.argtypes = [ctypes.c_void_p]
combase.WindowsGetStringRawBuffer.argtypes = [ctypes.c_void_p, ctypes.POINTER(W.UINT)]
combase.WindowsGetStringRawBuffer.restype = ctypes.c_void_p
combase.RoGetActivationFactory.argtypes = [ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
combase.RoGetActivationFactory.restype = ctypes.c_long

COINIT_MULTITHREADED = 0x0
CLSCTX_INPROC_SERVER = 0x1
IID_IAsyncInfo = GUID.parse("00000036-0000-0000-C000-000000000046")


class ComError(OSError):
    def __init__(self, hr, what):
        OSError.__init__(self, "%s failed: 0x%08X" % (what, hr & 0xFFFFFFFF))
        self.hr = hr


def init_mta():
    hr = ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
    if hr < 0 and (hr & 0xFFFFFFFF) != 0x80010106:     # RPC_E_CHANGED_MODE is fine
        raise ComError(hr, "CoInitializeEx")


class Ptr(object):
    """Owning wrapper around a COM interface pointer."""
    __slots__ = ("p",)

    def __init__(self, p):
        self.p = p.value if isinstance(p, ctypes.c_void_p) else p

    def __bool__(self):
        return bool(self.p)

    def call(self, slot, argtypes, *args, **kw):
        if not self.p:
            raise ComError(-1, "call on null pointer")
        vt = ctypes.cast(ctypes.c_void_p(self.p), ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        fn = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vt[slot])
        hr = fn(self.p, *args)
        if hr < 0:
            raise ComError(hr, kw.get("what", "COM slot %d" % slot))
        return hr

    # --- common getters ---
    def get_ptr(self, slot, *in_args):
        """slot(in_args..., out IFoo**) -> Ptr"""
        out = ctypes.c_void_p()
        types = [type(a) for a in in_args] + [ctypes.POINTER(ctypes.c_void_p)]
        self.call(slot, types, *(list(in_args) + [ctypes.byref(out)]))
        return Ptr(out)

    def get_int(self, slot, ctype=ctypes.c_int):
        out = ctype()
        self.call(slot, [ctypes.POINTER(ctype)], ctypes.byref(out))
        return out.value

    def get_bstr(self, slot):
        out = ctypes.c_void_p()
        self.call(slot, [ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(out))
        return take_bstr(out)

    def get_hstring(self, slot):
        out = ctypes.c_void_p()
        self.call(slot, [ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(out))
        return take_hstring(out)

    def qi(self, iid):
        out = ctypes.c_void_p()
        self.call(0, [ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(iid), ctypes.byref(out))
        return Ptr(out)

    def release(self):
        if self.p:
            vt = ctypes.cast(ctypes.c_void_p(self.p), ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vt[2])(self.p)
            self.p = None

    def __del__(self):
        try:
            self.release()
        except Exception:
            pass


def take_bstr(v):
    if not v.value:
        return ""
    try:
        return ctypes.wstring_at(v.value, oleaut32.SysStringLen(v.value))
    finally:
        oleaut32.SysFreeString(v.value)


class HString(object):
    def __init__(self, s):
        self.h = ctypes.c_void_p()
        hr = combase.WindowsCreateString(s, len(s), ctypes.byref(self.h))
        if hr < 0:
            raise ComError(hr, "WindowsCreateString")

    def __del__(self):
        if self.h:
            combase.WindowsDeleteString(self.h)
            self.h = None


def take_hstring(v):
    if not v.value:
        return ""
    try:
        n = W.UINT()
        buf = combase.WindowsGetStringRawBuffer(v, ctypes.byref(n))
        return ctypes.wstring_at(buf, n.value) if buf else ""
    finally:
        combase.WindowsDeleteString(v)


def create_instance(clsid, iid):
    out = ctypes.c_void_p()
    hr = ole32.CoCreateInstance(ctypes.byref(clsid), None, CLSCTX_INPROC_SERVER, ctypes.byref(iid), ctypes.byref(out))
    if hr < 0:
        raise ComError(hr, "CoCreateInstance")
    return Ptr(out)


def activation_factory(class_name, iid):
    name = HString(class_name)
    out = ctypes.c_void_p()
    hr = combase.RoGetActivationFactory(name.h, ctypes.byref(iid), ctypes.byref(out))
    if hr < 0:
        raise ComError(hr, "RoGetActivationFactory(%s)" % class_name)
    return Ptr(out)


def await_operation(op, timeout=5.0):
    """Wait for a WinRT IAsyncOperation<T> and return its result pointer (IAsyncOperation::GetResults, slot 8)."""
    info = op.qi(IID_IAsyncInfo)
    end = time.time() + timeout
    while True:
        status = info.get_int(7)                  # IAsyncInfo::get_Status
        if status == 1:                           # Completed
            break
        if status in (2, 3):                      # Canceled / Error
            err = info.get_int(8)                 # get_ErrorCode
            raise ComError(err, "async operation")
        if time.time() > end:
            info.call(9, [])                      # Cancel
            raise ComError(-1, "async operation timed out")
        time.sleep(0.01)
    return op.get_ptr(8)
