"""Draw the critter into ClaudeMini.ico (used by the Start menu and Startup shortcuts), offscreen.

    "C:\\Program Files\\Python314\\python.exe" -B tools\\make_icon.py [--out FILE]

The .ico holds PNG images at several sizes (Windows Vista and later read PNG icon entries).
"""
import argparse
import ctypes
import os
import struct
import sys
import tempfile

sys.dont_write_bytecode = True
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from claude_mini import avatar, win32               # noqa: E402
from claude_mini import gdiplus as G                # noqa: E402

GdipSaveImageToFile = G._fn("GdipSaveImageToFile", ctypes.c_void_p, ctypes.c_wchar_p, ctypes.POINTER(win32.GUID),
                            ctypes.c_void_p)
PNG_ENCODER = win32.GUID.parse("557CF406-1A04-11D3-9A73-0000F81EF32E")
SIZES = (256, 128, 64, 48, 32)


def png(n):
    """The standing critter, as PNG bytes, n x n with a clear background."""
    cv = G.Canvas(n, n)
    cv.clear(0)
    avatar.draw_fitted(cv, n * 0.03, n * 0.03, n * 0.94, n * 0.94, avatar.standing_frame())
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        if GdipSaveImageToFile(cv.bitmap, path, ctypes.byref(PNG_ENCODER), None) != 0:
            raise SystemExit("couldn't encode the %d px image" % n)
        with open(path, "rb") as f:
            return f.read()
    finally:
        cv.close()
        os.remove(path)


def write_ico(path):
    images = [png(n) for n in SIZES]
    out = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    for n, data in zip(SIZES, images):
        side = 0 if n >= 256 else n                      # 0 means 256
        out += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    with open(path, "wb") as f:
        f.write(out + b"".join(images))
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=os.path.join(ROOT, "ClaudeMini.ico"))
    args = ap.parse_args()
    G.startup()
    print(write_ico(args.out))


if __name__ == "__main__":
    main()
