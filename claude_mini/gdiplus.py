"""Tiny GDI+ wrapper: anti-aliased drawing into a premultiplied-ARGB DIB for layered windows."""
import ctypes
from ctypes import wintypes as W

from . import win32

gp = ctypes.WinDLL("gdiplus")
_VP = ctypes.c_void_p
_F = ctypes.c_float


class PointF(ctypes.Structure):
    _fields_ = [("X", _F), ("Y", _F)]


class RectF(ctypes.Structure):
    _fields_ = [("X", _F), ("Y", _F), ("Width", _F), ("Height", _F)]


class _StartupInput(ctypes.Structure):
    _fields_ = [("GdiplusVersion", ctypes.c_uint32), ("DebugEventCallback", _VP),
                ("SuppressBackgroundThread", W.BOOL), ("SuppressExternalCodecs", W.BOOL)]


def _fn(name, *argtypes):
    f = getattr(gp, name)
    f.restype = ctypes.c_int
    f.argtypes = list(argtypes)
    return f


_P = ctypes.POINTER
GdiplusStartup = _fn("GdiplusStartup", _P(ctypes.c_size_t), _P(_StartupInput), _VP)
GdipCreateBitmapFromScan0 = _fn("GdipCreateBitmapFromScan0", ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, _VP, _P(_VP))
GdipGetImageGraphicsContext = _fn("GdipGetImageGraphicsContext", _VP, _P(_VP))
GdipDeleteGraphics = _fn("GdipDeleteGraphics", _VP)
GdipDisposeImage = _fn("GdipDisposeImage", _VP)
GdipSetSmoothingMode = _fn("GdipSetSmoothingMode", _VP, ctypes.c_int)
GdipSetPixelOffsetMode = _fn("GdipSetPixelOffsetMode", _VP, ctypes.c_int)
GdipSetTextRenderingHint = _fn("GdipSetTextRenderingHint", _VP, ctypes.c_int)
GdipGraphicsClear = _fn("GdipGraphicsClear", _VP, ctypes.c_uint32)
GdipCreateSolidFill = _fn("GdipCreateSolidFill", ctypes.c_uint32, _P(_VP))
GdipCreateLineBrush = _fn("GdipCreateLineBrush", _P(PointF), _P(PointF), ctypes.c_uint32, ctypes.c_uint32, ctypes.c_int, _P(_VP))
GdipCreatePathGradientFromPath = _fn("GdipCreatePathGradientFromPath", _VP, _P(_VP))
GdipSetPathGradientCenterColor = _fn("GdipSetPathGradientCenterColor", _VP, ctypes.c_uint32)
GdipSetPathGradientSurroundColorsWithCount = _fn("GdipSetPathGradientSurroundColorsWithCount", _VP, _P(ctypes.c_uint32), _P(ctypes.c_int))
GdipDeleteBrush = _fn("GdipDeleteBrush", _VP)
GdipCreatePen1 = _fn("GdipCreatePen1", ctypes.c_uint32, _F, ctypes.c_int, _P(_VP))
GdipSetPenStartCap = _fn("GdipSetPenStartCap", _VP, ctypes.c_int)
GdipSetPenEndCap = _fn("GdipSetPenEndCap", _VP, ctypes.c_int)
GdipSetPenLineJoin = _fn("GdipSetPenLineJoin", _VP, ctypes.c_int)
GdipDeletePen = _fn("GdipDeletePen", _VP)
GdipFillEllipse = _fn("GdipFillEllipse", _VP, _VP, _F, _F, _F, _F)
GdipFillRectangles = _fn("GdipFillRectangles", _VP, _VP, _P(RectF), ctypes.c_int)
GdipDrawEllipse = _fn("GdipDrawEllipse", _VP, _VP, _F, _F, _F, _F)
GdipDrawArc = _fn("GdipDrawArc", _VP, _VP, _F, _F, _F, _F, _F, _F)
GdipDrawLine = _fn("GdipDrawLine", _VP, _VP, _F, _F, _F, _F)
GdipFillPolygon = _fn("GdipFillPolygon", _VP, _VP, _P(PointF), ctypes.c_int, ctypes.c_int)
GdipDrawPolygon = _fn("GdipDrawPolygon", _VP, _VP, _P(PointF), ctypes.c_int)
GdipCreatePath = _fn("GdipCreatePath", ctypes.c_int, _P(_VP))
GdipAddPathArc = _fn("GdipAddPathArc", _VP, _F, _F, _F, _F, _F, _F)
GdipAddPathEllipse = _fn("GdipAddPathEllipse", _VP, _F, _F, _F, _F)
GdipClosePathFigure = _fn("GdipClosePathFigure", _VP)
GdipFillPath = _fn("GdipFillPath", _VP, _VP, _VP)
GdipDrawPath = _fn("GdipDrawPath", _VP, _VP, _VP)
GdipDeletePath = _fn("GdipDeletePath", _VP)
GdipCreateFontFamilyFromName = _fn("GdipCreateFontFamilyFromName", W.LPCWSTR, _VP, _P(_VP))
GdipDeleteFontFamily = _fn("GdipDeleteFontFamily", _VP)
GdipCreateFont = _fn("GdipCreateFont", _VP, _F, ctypes.c_int, ctypes.c_int, _P(_VP))
GdipDeleteFont = _fn("GdipDeleteFont", _VP)
GdipCreateStringFormat = _fn("GdipCreateStringFormat", ctypes.c_int, W.WORD, _P(_VP))
GdipSetStringFormatAlign = _fn("GdipSetStringFormatAlign", _VP, ctypes.c_int)
GdipSetStringFormatLineAlign = _fn("GdipSetStringFormatLineAlign", _VP, ctypes.c_int)
GdipSetStringFormatTrimming = _fn("GdipSetStringFormatTrimming", _VP, ctypes.c_int)
GdipDeleteStringFormat = _fn("GdipDeleteStringFormat", _VP)
GdipDrawString = _fn("GdipDrawString", _VP, W.LPCWSTR, ctypes.c_int, _VP, _P(RectF), _VP, _VP)
GdipMeasureString = _fn("GdipMeasureString", _VP, W.LPCWSTR, ctypes.c_int, _VP, _P(RectF), _VP, _P(RectF), _VP, _VP)
GdipCreateHICONFromBitmap = _fn("GdipCreateHICONFromBitmap", _VP, _P(W.HICON))

PixelFormat32bppPARGB = 0xE200B
UnitPixel = 2
LineCapRound = 2
SmoothingModeNone = 3
SmoothingModeAntiAlias = 4
PixelOffsetModeHighQuality = 2
TextRenderingHintAntiAlias = 4
FontStyleBold = 1
StringFormatFlagsNoWrap = 0x1000
StringFormatFlagsLineLimit = 0x2000
StringTrimmingEllipsisCharacter = 3

_token = ctypes.c_size_t()


def startup():
    si = _StartupInput(1, None, False, False)
    status = GdiplusStartup(ctypes.byref(_token), ctypes.byref(si), None)
    if status != 0:
        raise OSError("GdiplusStartup failed: %d" % status)


def argb(a, r, g, b):
    return ((a & 255) << 24) | ((r & 255) << 16) | ((g & 255) << 8) | (b & 255)


def hexc(s, a=255):
    s = s.lstrip("#")
    return argb(a, int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


class Canvas(object):
    """A w x h drawing surface backed by a DIB selected into a memory DC (for UpdateLayeredWindow)."""

    def __init__(self, w, h):
        self.w, self.h = w, h
        bmi = win32.BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(win32.BITMAPINFOHEADER)
        bmi.biWidth = w
        bmi.biHeight = -h          # top-down
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bits = ctypes.c_void_p()
        screen = win32.GetDC(None)
        self.dc = win32.CreateCompatibleDC(screen)
        win32.ReleaseDC(None, screen)
        self.dib = win32.CreateDIBSection(self.dc, ctypes.byref(bmi), win32.DIB_RGB_COLORS, ctypes.byref(bits), None, 0)
        self.old = win32.SelectObject(self.dc, self.dib)
        self.bitmap = _VP()
        GdipCreateBitmapFromScan0(w, h, w * 4, PixelFormat32bppPARGB, bits, ctypes.byref(self.bitmap))
        self.g = _VP()
        GdipGetImageGraphicsContext(self.bitmap, ctypes.byref(self.g))
        GdipSetSmoothingMode(self.g, SmoothingModeAntiAlias)
        GdipSetPixelOffsetMode(self.g, PixelOffsetModeHighQuality)
        GdipSetTextRenderingHint(self.g, TextRenderingHintAntiAlias)

    # --- primitives ---
    def clear(self, color=0):
        GdipGraphicsClear(self.g, color)

    def _brush(self, color):
        b = _VP()
        GdipCreateSolidFill(color, ctypes.byref(b))
        return b

    def _pen(self, color, width, round_caps=True):
        p = _VP()
        GdipCreatePen1(color, width, UnitPixel, ctypes.byref(p))
        if round_caps:
            GdipSetPenStartCap(p, LineCapRound)
            GdipSetPenEndCap(p, LineCapRound)
            GdipSetPenLineJoin(p, 2)
        return p

    def fill_ellipse(self, x, y, w, h, color):
        b = self._brush(color)
        GdipFillEllipse(self.g, b, x, y, w, h)
        GdipDeleteBrush(b)

    def fill_ellipse_vgradient(self, x, y, w, h, top, bottom):
        b = _VP()
        p1, p2 = PointF(x, y - 1), PointF(x, y + h + 1)
        GdipCreateLineBrush(ctypes.byref(p1), ctypes.byref(p2), top, bottom, 0, ctypes.byref(b))
        GdipFillEllipse(self.g, b, x, y, w, h)
        GdipDeleteBrush(b)

    def fill_ellipse_radial(self, x, y, w, h, centre, edge):
        path = _VP()
        GdipCreatePath(0, ctypes.byref(path))
        GdipAddPathEllipse(path, x, y, w, h)
        b = _VP()
        GdipCreatePathGradientFromPath(path, ctypes.byref(b))
        GdipSetPathGradientCenterColor(b, centre)
        col = ctypes.c_uint32(edge)
        n = ctypes.c_int(1)
        GdipSetPathGradientSurroundColorsWithCount(b, ctypes.byref(col), ctypes.byref(n))
        GdipFillPath(self.g, b, path)
        GdipDeleteBrush(b)
        GdipDeletePath(path)

    def fill_rects(self, rects, color):
        """Fill (x, y, w, h) rects in one call, without anti-aliasing: whole-pixel rects stay crisp (pixel art)."""
        self.fill_rect_groups([(color, [v for r in rects for v in r])])

    def fill_rect_groups(self, groups):
        """Like fill_rects for several colours at once: (color, [x, y, w, h, x, y, w, h, ...]) each, the coordinates
        flat, as a list or a ready ctypes float array (much cheaper to hand to GDI+ than one RectF per rect)."""
        GdipSetSmoothingMode(self.g, SmoothingModeNone)
        for color, flat in groups:
            if len(flat) < 4:
                continue
            arr = flat if isinstance(flat, ctypes.Array) else (ctypes.c_float * len(flat))(*flat)
            b = self._brush(color)
            GdipFillRectangles(self.g, b, ctypes.cast(arr, _P(RectF)), len(flat) // 4)
            GdipDeleteBrush(b)
        GdipSetSmoothingMode(self.g, SmoothingModeAntiAlias)

    def draw_ellipse(self, x, y, w, h, color, width):
        p = self._pen(color, width)
        GdipDrawEllipse(self.g, p, x, y, w, h)
        GdipDeletePen(p)

    def draw_arc(self, x, y, w, h, start, sweep, color, width):
        p = self._pen(color, width)
        GdipDrawArc(self.g, p, x, y, w, h, start, sweep)
        GdipDeletePen(p)

    def draw_line(self, x1, y1, x2, y2, color, width):
        p = self._pen(color, width)
        GdipDrawLine(self.g, p, x1, y1, x2, y2)
        GdipDeletePen(p)

    def fill_polygon(self, pts, color, outline=None, outline_width=1.0):
        arr = (PointF * len(pts))(*[PointF(x, y) for x, y in pts])
        b = self._brush(color)
        GdipFillPolygon(self.g, b, arr, len(pts), 0)
        GdipDeleteBrush(b)
        if outline is not None:
            p = self._pen(outline, outline_width)
            GdipDrawPolygon(self.g, p, arr, len(pts))
            GdipDeletePen(p)

    def fill_round_rect(self, x, y, w, h, r, color, outline=None, outline_width=1.0):
        path = _VP()
        GdipCreatePath(0, ctypes.byref(path))
        d = 2 * r
        GdipAddPathArc(path, x, y, d, d, 180, 90)
        GdipAddPathArc(path, x + w - d, y, d, d, 270, 90)
        GdipAddPathArc(path, x + w - d, y + h - d, d, d, 0, 90)
        GdipAddPathArc(path, x, y + h - d, d, d, 90, 90)
        GdipClosePathFigure(path)
        b = self._brush(color)
        GdipFillPath(self.g, b, path)
        GdipDeleteBrush(b)
        if outline is not None:
            p = self._pen(outline, outline_width)
            GdipDrawPath(self.g, p, path)
            GdipDeletePen(p)
        GdipDeletePath(path)

    def _font(self, size, bold):
        fam = _VP()
        GdipCreateFontFamilyFromName("Segoe UI", None, ctypes.byref(fam))
        font = _VP()
        GdipCreateFont(fam, size, FontStyleBold if bold else 0, UnitPixel, ctypes.byref(font))
        return fam, font

    def draw_text(self, text, x, y, w, h, size, color, bold=False, align=1, valign=1, wrap=False):
        """Draw text in a box. wrap=False: one line with an ellipsis; wrap=True: as many whole lines as fit, the
        last one ending in an ellipsis if the text is longer."""
        fam, font = self._font(size, bold)
        fmt = _VP()
        flags = StringFormatFlagsLineLimit if wrap else StringFormatFlagsNoWrap
        GdipCreateStringFormat(flags, 0, ctypes.byref(fmt))
        GdipSetStringFormatTrimming(fmt, StringTrimmingEllipsisCharacter)
        GdipSetStringFormatAlign(fmt, align)
        GdipSetStringFormatLineAlign(fmt, valign)
        b = self._brush(color)
        rect = RectF(x, y, w, h)
        GdipDrawString(self.g, text, len(text), font, ctypes.byref(rect), fmt, b)
        GdipDeleteBrush(b)
        GdipDeleteStringFormat(fmt)
        GdipDeleteFont(font)
        GdipDeleteFontFamily(fam)

    def measure_text(self, text, size, bold=False):
        fam, font = self._font(size, bold)
        fmt = _VP()
        GdipCreateStringFormat(0, 0, ctypes.byref(fmt))
        layout = RectF(0, 0, 10000, 10000)
        box = RectF()
        GdipMeasureString(self.g, text, len(text), font, ctypes.byref(layout), fmt, ctypes.byref(box), None, None)
        GdipDeleteStringFormat(fmt)
        GdipDeleteFont(font)
        GdipDeleteFontFamily(fam)
        return box.Width, box.Height

    # --- output ---
    def present(self, hwnd, x=None, y=None):
        """Push the pixels to a WS_EX_LAYERED window (optionally moving it)."""
        size = win32.SIZE(self.w, self.h)
        src = win32.POINT(0, 0)
        blend = win32.BLENDFUNCTION(win32.AC_SRC_OVER, 0, 255, win32.AC_SRC_ALPHA)
        dst = win32.POINT(x, y) if x is not None else None
        screen = win32.GetDC(None)
        win32.UpdateLayeredWindow(hwnd, screen, ctypes.byref(dst) if dst else None, ctypes.byref(size), self.dc,
                                  ctypes.byref(src), 0, ctypes.byref(blend), win32.ULW_ALPHA)
        win32.ReleaseDC(None, screen)

    def to_hicon(self):
        h = W.HICON()
        GdipCreateHICONFromBitmap(self.bitmap, ctypes.byref(h))
        return h

    def close(self):
        if self.g:
            GdipDeleteGraphics(self.g)
            self.g = None
        if self.bitmap:
            GdipDisposeImage(self.bitmap)
            self.bitmap = None
        if self.dc:
            win32.SelectObject(self.dc, self.old)
            win32.DeleteObject(self.dib)
            win32.DeleteDC(self.dc)
            self.dc = None
