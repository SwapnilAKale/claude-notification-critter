"""Render every avatar state as a strip of blob frames into one PNG, offscreen (no window, nothing shown).

    "C:\\Program Files\\Python314\\python.exe" -B tools\\preview_avatar.py [--out FILE] [--scale 1.25] [--frames 8]
                                                                         [--tucked none|left|right|top|bottom]

Each row is one state, drawn exactly as the blob draws it (badge, blue dot, warning mark), with frames spread evenly
over that state's loop, first on a light desktop, then on a dark one. --scale 3.75 gives a 3x close-up.
"""
import argparse
import ctypes
import os
import sys

sys.dont_write_bytecode = True
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from claude_mini import avatar, blob, win32          # noqa: E402
from claude_mini import gdiplus as G                 # noqa: E402
from claude_mini.app import DEMO_COUNT, demo_state   # noqa: E402

GdipSaveImageToFile = G._fn("GdipSaveImageToFile", ctypes.c_void_p, ctypes.c_wchar_p, ctypes.POINTER(win32.GUID),
                            ctypes.c_void_p)
PNG_ENCODER = win32.GUID.parse("557CF406-1A04-11D3-9A73-0000F81EF32E")


def snapshots_by_state():
    out = {}
    for i in range(DEMO_COUNT):
        snap = demo_state(i)
        out.setdefault(avatar.avatar_state(snap), snap)
    missing = [s for s in avatar.STATES if s not in out]
    if missing:
        raise SystemExit("demo mode doesn't cover: %s" % ", ".join(missing))
    return out


DESKTOPS = ("F3F2EF", "202124")       # a light window, a dark one


def render(path, scale=1.25, frames=6, tucked=blob.NONE):
    snaps = snapshots_by_state()
    box = blob.box_px(scale)
    label_w = int(110 * scale)
    w = label_w + len(DESKTOPS) * frames * box
    h = len(avatar.STATES) * box
    cv = G.Canvas(w, h)
    cv.fill_rects([(0, 0, label_w, h)], G.hexc("E6E3DE"))
    for k, bg in enumerate(DESKTOPS):
        cv.fill_rects([(label_w + k * frames * box, 0, frames * box, h)], G.hexc(bg))
    for row, state in enumerate(avatar.STATES):
        y = row * box
        cv.draw_text(state, 6 * scale, y, label_w - 8 * scale, box, 12 * scale, G.hexc("1C1B19"), bold=True,
                     align=0)
        for k in range(len(DESKTOPS)):
            for i in range(frames):
                t = (avatar.INTRO.get(state, 0) + avatar.LOOP[state]) * i / frames
                blob.paint(cv, label_w + (k * frames + i) * box, y, scale, snaps[state], t, tucked, state)
    status = GdipSaveImageToFile(cv.bitmap, os.path.abspath(path), ctypes.byref(PNG_ENCODER), None)
    cv.close()
    if status != 0:
        raise SystemExit("GdipSaveImageToFile failed: %d" % status)
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=os.path.join(ROOT, "design", "avatar-preview.png"))
    ap.add_argument("--scale", type=float, default=1.25)
    ap.add_argument("--frames", type=int, default=6, help="frames per desktop colour")
    ap.add_argument("--tucked", default=blob.NONE, choices=[blob.NONE, blob.LEFT, blob.RIGHT, blob.TOP, blob.BOTTOM])
    args = ap.parse_args()
    G.startup()
    print(render(args.out, args.scale, args.frames, args.tucked))


if __name__ == "__main__":
    main()
