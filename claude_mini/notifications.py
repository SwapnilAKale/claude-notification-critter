"""Source A: the Claude app's own Windows notifications, via UserNotificationListener (works unpackaged)."""
import ctypes

from . import com
from .win32 import GUID

CLAUDE_PFN = "Claude_pzs8sxrjxfjjc"

IID_IUserNotificationListenerStatics = GUID.parse("ff6123cf-4386-4aa3-b73d-b804e5b63b23")
# Slots (after IInspectable's 6), read from Windows.UI.Notifications.winmd / Windows.ApplicationModel.winmd:
#   IUserNotificationListenerStatics: 6 get_Current
#   IUserNotificationListener:        7 GetAccessStatus, 10 GetNotificationsAsync
#   IUserNotification:                6 get_Notification, 7 get_AppInfo, 8 get_Id, 9 get_CreationTime
#   IAppInfo:                         7 get_AppUserModelId, 9 get_PackageFamilyName
#   INotification:                    8 get_Visual
#   INotificationVisual:              9 GetBinding
#   INotificationBinding:             11 GetTextElements
#   IAdaptiveNotificationText:        6 get_Text
#   IVectorView<T>:                   6 GetAt, 7 get_Size

ALLOWED, DENIED = 1, 2


class ClaudeToast(object):
    __slots__ = ("id", "title", "body", "created")

    def __init__(self, nid, title, body, created):
        self.id, self.title, self.body, self.created = nid, title, body, created

    def __repr__(self):
        return "Toast(%d, %r, %r)" % (self.id, self.title, self.body)


def _vector(v):
    n = v.get_int(7, ctypes.c_uint)
    for i in range(n):
        yield v.get_ptr(6, ctypes.c_uint(i))


class NotificationReader(object):
    def __init__(self):
        self.listener = None
        self.cache = {}          # notification id -> ClaudeToast (or None for other apps)

    def _ensure(self):
        if self.listener is None:
            statics = com.activation_factory("Windows.UI.Notifications.Management.UserNotificationListener",
                                             IID_IUserNotificationListenerStatics)
            self.listener = statics.get_ptr(6)

    def access_status(self):
        self._ensure()
        return self.listener.get_int(7)

    def read(self):
        """All Claude toasts currently in Action Center, keyed by id."""
        self._ensure()
        op = self.listener.get_ptr(10, ctypes.c_uint(1))           # NotificationKinds.Toast
        vec = com.await_operation(op)
        seen = {}
        for un in _vector(vec):
            nid = un.get_int(8, ctypes.c_uint)
            if nid in self.cache:
                if self.cache[nid] is not None:
                    seen[nid] = self.cache[nid]
                continue
            toast = None
            try:
                info = un.get_ptr(7)
                pfn = info.get_hstring(9)
                aumid = info.get_hstring(7)
                if pfn == CLAUDE_PFN or aumid.startswith(CLAUDE_PFN + "!"):
                    toast = self._toast(un, nid)
            except com.ComError:
                toast = None
            self.cache[nid] = toast
            if toast is not None:
                seen[nid] = toast
        # forget ids that left Action Center
        live = set(seen)
        for k in list(self.cache):
            if k not in live and self.cache[k] is not None:
                del self.cache[k]
        if len(self.cache) > 2000:
            self.cache = dict((k, v) for k, v in self.cache.items() if v is not None)
        return seen

    @staticmethod
    def _toast(un, nid):
        created = ctypes.c_int64()
        un.call(9, [ctypes.POINTER(ctypes.c_int64)], ctypes.byref(created))
        texts = []
        try:
            visual = un.get_ptr(6).get_ptr(8)
            template = com.HString("ToastGeneric")          # keep alive for the duration of the call
            binding = visual.get_ptr(9, template.h)
            if binding:
                for t in _vector(binding.get_ptr(11)):
                    texts.append(t.get_hstring(6))
        except com.ComError:
            pass
        title = texts[0] if texts else ""
        body = " ".join(texts[1:]) if len(texts) > 1 else ""
        # DateTime: 100ns ticks since 1601-01-01 UTC -> unix seconds
        unix = created.value / 1e7 - 11644473600.0
        return ClaudeToast(nid, title, body, unix)
