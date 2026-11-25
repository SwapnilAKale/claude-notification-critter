"""Claude Code's pixel critter (the blob), with one looping animation per state and smooth changes between them.

Procedural pixel art (no image files) on a fine grid of "F" units, after Claude Code's mascot
(design/working-reference-frames.png): a 24 x 18 F terracotta body, two 3 x 3 F dark eyes, 6 x 6 F stubby arms and
four 3 x 6 F legs, so the critter is 36 F wide and 24 F tall. Props (bubbles, key,
z's...) go above it and to the left; `mirror` moves them to the right. `Animator` plays a state and eases from one
state to the next.

It looks 3D: everything is a slab extruded DEPTH F back into the screen (props a thinner PROP_DEPTH), seen from a
little above and to the right, lit from the top left: a lighter top face, a darker right side, a highlight along the
front's top edge, darker legs under the body, a shine in each eye, and a soft ground shadow (blob.py) that shrinks
when it hops.

A frame is a list of rects. They are painted into a small byte grid (overlaps resolve exactly, alpha stays even); the
extrusion and the one-cell drop shadow that keeps it readable on light windows are shifted copies of that grid, made
with a few big-int operations per row. Then it's drawn as one crisp FillRectangles call per colour. Everything is pure
Python + GDI+ and costs about a millisecond per frame.
"""
import ctypes
import math
import re

from . import gdiplus as G
from . import model

PERMISSION, PLAN, QUESTION, PROBLEM, WAITING, FINISHED, UPDATE = (
    model.PERMISSION, model.PLAN, model.QUESTION, model.PROBLEM, model.WAITING, model.FINISHED, model.UPDATE)
WORKING, IDLE, UNREADABLE, SLEEPING = "working", "idle", "unreadable", "sleeping"
STATES = (PERMISSION, QUESTION, PLAN, PROBLEM, WAITING, FINISHED, UPDATE, WORKING, IDLE, UNREADABLE, SLEEPING)
AWAY = "away"           # Claude isn't running: no animation, the critter walks off screen (see blob.py)

W, H = 36, 24           # critter size (F)

# loop length (s), and how often the blob needs a new frame: calm states are cheap
LOOP = {PERMISSION: 2.2, QUESTION: 2.6, PLAN: 3.2, PROBLEM: 2.4, WAITING: 2.6, FINISHED: 1.8, UPDATE: 2.4,
        WORKING: 6.0, IDLE: 4.0, UNREADABLE: 3.0, SLEEPING: 4.0}
INTRO = {WORKING: 2.6, SLEEPING: 1.8}   # played once before the loop (and backwards when leaving: waking up)
ENTER_S, EXIT_S = 0.4, 0.35  # other states ease in / out this fast
OUTRO_SPEED = 1.6            # an intro played backwards runs this much faster
TRANSITION_MS = 40
FRAME_MS = {PERMISSION: 50, QUESTION: 50, PLAN: 66, PROBLEM: 50, WAITING: 50, FINISHED: 40, UPDATE: 50,
            WORKING: 50, IDLE: 125, UNREADABLE: 100, SLEEPING: 100}

COLORS = {"c": "D97757",    # body (terracotta)
          "d": "A8563B",    # body's side, when turned
          "a": "C2664A",    # legs, in the body's shadow
          "h": "E8977A",    # highlight along the body's top edge
          "e": "1C1B19",    # eyes
          "i": "FFF6EC",    # the shine in an eye
          "k": "1C1B19",    # ink outlines of props
          "w": "FFFFFF", "p": "F5EBDD", "r": "DC2626", "y": "FBBF24", "g": "16A34A", "b": "7DD3FC",
          "m": "D6D3CD", "n": "B07A4A", "z": "94A3B8", "j": "FBBF24", "o": "D97757", "l": "4B4F58", "s": "000000",
          "Z": "7C8799"}    # z: dots, fine on light or dark; Z: the sleeping z's, readable on both without a shadow
CRITTER = "cdahei"      # keys drawn by critter(); everything else is a prop
FLAT = "jzZ"            # glints, dots and z's: no 3D depth
NO_SHADOW = "Z"         # thin glyphs whose drop shadow would read as a second stroke (a z turned into an X)
SHADOW = 0.35           # drop shadow opacity
DEPTH, PROP_DEPTH = 4, 1    # how far the critter / its props reach back into the screen (F, up and to the right)
TOP, SIDE = 0.24, -0.32     # the extrusion's top face is this much lighter, its right side this much darker


def _shade(rgb, k):
    c = [int(rgb[i:i + 2], 16) for i in (0, 2, 4)]
    c = [v + (255 - v) * k if k > 0 else v * (1 + k) for v in c]
    return "%02X%02X%02X" % tuple(int(round(v)) for v in c)


for _k in list(COLORS):
    COLORS[_k + "^"], COLORS[_k + "_"] = _shade(COLORS[_k], TOP), _shade(COLORS[_k], SIDE)


def avatar_state(snapshot):
    """Which animation the blob shows for a snapshot (pure)."""
    lvl = snapshot.level
    if lvl == model.UNAVAILABLE:
        return AWAY
    kinds = [c.kind for c in snapshot.cards]        # most urgent first
    if lvl == model.NEEDS_ME:
        return next((k for k in kinds if k in model.URGENT), WAITING)
    if lvl == model.NEW:
        return next((k for k in kinds if k in (FINISHED, UPDATE)), FINISHED if snapshot.finished else UPDATE)
    if lvl == model.WORKING:
        return WORKING
    if snapshot.degraded:
        return UNREADABLE
    return SLEEPING if snapshot.asleep else IDLE


# ---------- frame building ----------
class Frame(object):
    def __init__(self, motion=1.0, props=1.0):
        self.items = []         # (x, y, w, h, key, alpha, block)
        self.air = 0            # how high the critter is off the ground (F), for its ground shadow
        self.motion = motion    # 1 = full animation, 0 = plain standing pose (used while changing state)
        self.props = props      # props' opacity; below 1 they also sit a little lower (fading in / out)
        self._prop_dy = int(round((1 - props) * 4))

    def rect(self, x, y, w, h, key, a=1.0):
        if key not in CRITTER:
            a, y = a * self.props, y + self._prop_dy
        self.items.append((int(round(x)), int(round(y)), w, h, key, a, None))

    def art(self, x, y, rows, cell=1, a=1.0, readable=False):
        """Pixel art from strings ('.' = clear, other chars = COLORS keys). A readable piece (text, a tick)
        keeps its orientation when the frame is mirrored; only its position moves."""
        x, y = int(round(x)), int(round(y))
        block = (x, len(rows[0]) * cell) if readable else None
        for j, row in enumerate(rows):
            for m in re.finditer(r"([^.])\1*", row):
                key, ka, ky = m.group(1), a, y + j * cell
                if key not in CRITTER:
                    ka, ky = a * self.props, ky + self._prop_dy
                self.items.append((x + m.start() * cell, ky, (m.end() - m.start()) * cell, cell, key, ka, block))

    def mirror(self):
        out = []
        for x, y, w, h, key, a, block in self.items:
            if block:
                bx, bw = block
                x = W - bx - bw + (x - bx)
            else:
                x = W - x - w
            out.append((x, y, w, h, key, a, block))
        self.items = out


LEGS_X = (6, 12, 21, 27)
EYES_X = {0: (9, 24), 1: (8, 22), 2: (7, 19)}         # front, turning, side-on (facing the props side)
ARMS = {                                            # left arm (x, y from the body top, w, h); right is mirrored
    "out": ((0, 6, 6, 6),),
    "up": ((3, -4, 6, 4),),                         # both up: the reference's stretch
    "raise": ((2, -5, 4, 9),),                      # holding something up
    "wave": ((2, 1, 4, 4), (0, -3, 4, 4)),
    "down": ((3, 8, 3, 7),),
    "hold": ((0, 9, 6, 5),),
    "hold_up": ((0, 7, 6, 5),),
    "head": ((3, -3, 3, 9),),                       # scratching its head
    "head2": ((3, -2, 3, 8),),
    "none": (),
}
SQUINT = (("#..", ".##", "#.."), ("..#", "##.", "..#"))


def critter(f, dx=0, dy=0, squash=0, lean=0, eyes="open", look=(0, 0), arms=("out", "out"), legs="stand",
            lift=(), side=0):
    """dy < 0: in the air; squash: body top pressed down (breath, landing); lean: top of the body shifted sideways
    (head tilt); lift: leg indices raised 2 F; legs 'tuck' in the air; side 0 front, 1 turning, 2 side-on.
    f.motion < 1 scales all of that back toward a plain standing pose."""
    m = f.motion
    if m < 1.0:
        dx, dy, squash, lean = (int(round(v * m)) for v in (dx, dy, squash, lean))
        look = (int(round(look[0] * m)), int(round(look[1] * m)))
        if m < 0.5:
            eyes, arms, lift, legs = "open", ("out", "out"), (), "stand"
        side = 0 if m < 0.4 else (min(side, 1) if m < 0.8 else side)
    top, bottom = dy + squash, 18 + dy
    f.air = max(0, -dy)
    shifts = (lean, int(lean / 2.0), 0)
    for i, lx in enumerate(LEGS_X):
        h = (24 - bottom) if dy >= 0 else (4 if legs == "tuck" else 6)
        x = lx + dx
        if i in lift:
            h -= 2
            x -= 1 if side == 2 else 0
        if h > 0:
            f.rect(x, bottom, 3, h, "a")
    edges = (top, top + 6, top + 12, bottom)
    for k in range(3):
        x = 6 + dx + shifts[k]
        f.rect(x, edges[k], 24, edges[k + 1] - edges[k], "c")
        sw = (3 if side == 2 else 2) if side else 0
        if sw:
            f.rect(x + 24 - sw, edges[k], sw, edges[k + 1] - edges[k], "d")
        if k == 0:
            f.rect(x, top, 24 - sw, 1, "h")                 # top-lit
    kinds = (eyes, eyes) if isinstance(eyes, str) else eyes
    ey = top + 3 + look[1]
    for i, (x, kind) in enumerate(zip(EYES_X[side], kinds)):
        x += dx + lean + look[0]
        if kind == "open":
            f.rect(x, ey, 3, 3, "e")
            f.art(x, ey, ("i..",), readable=True)          # the shine stays top-left when mirrored
        elif kind == "blink":
            f.rect(x, ey + 1, 3, 1, "e")
        elif kind == "closed":
            f.rect(x, ey + 2, 3, 1, "e")
        elif kind == "wide":
            f.rect(x, ey - 1, 3, 4, "e")
            f.art(x, ey - 1, ("i..",), readable=True)
        elif kind == "happy":                       # ^ ^
            f.rect(x + 1, ey, 1, 1, "e")
            f.rect(x, ey + 1, 1, 2, "e")
            f.rect(x + 2, ey + 1, 1, 2, "e")
        elif kind == "squint":                      # > <
            f.art(x, ey, [r.replace("#", "e") for r in SQUINT[i]])
    if side == 2:
        arms = ("none", "none")
    elif side == 1:
        arms = (arms[0], "none")
    for which, name in enumerate(arms):
        for ax, ay, aw, ah in ARMS[name]:
            x = ax if which == 0 else W - ax - aw
            f.rect(x + dx + (shifts[0] if ay < 6 else shifts[1]), top + ay, aw, ah, "c")


def _hop(p, h):
    """Height (as a negative dy) of a hop h F high, p = 0..1 through it."""
    return -int(round(h * 4 * p * (1 - p)))


# ---------- props (1 cell = 1 F unless noted) ----------
GLYPHS = {"?": ("###", "..#", ".##", "...", ".#."), "!": (".#.", ".#.", ".#.", "...", ".#.")}
KEY = (".kkkkk.", "kyyyyyk", "kykkkyk", "kyyyyyk", ".kkykk.", "..kyk..", "..kyk..", "..kyyyk", "..kykkk",
       "..kyyk.", "..kyk..", "..kkk..")
CLIPBOARD = ("...kkkkk...", "kkkkmmmkkkk", "knnnmmmnnnk", "knpppppppnk", "knpkkkkkpnk", "knpppppppnk",
             "knpkkkkppnk", "knpppppppnk", "knpkkkkkpnk", "knpppppppnk", "knpkkkpppnk", "knpppppppnk",
             "knnnnnnnnnk", ".kkkkkkkkk.")
ENVELOPE = ("kkkkkkkkkkk", "kkwwwwwwwkk", "kwkwwwwwkwk", "kwwkwwwkwwk", "kwwwkwkwwwk", "kwwwwkwwwwk",
            "kwwwwwwwwwk", "kkkkkkkkkkk")
SWEAT = ("..k..", ".kbk.", "kbbbk", "kbbbk", ".kkk.")
SPARKLE = ("..j..", "..j..", "jjjjj", "..j..", "..j..")
SPARKLE_S = (".j.", "jjj", ".j.")
CHECK = ("......gg", ".....gg.", "gg..gg..", ".gggg...", "..gg....")


def bubble(f, x, y, glyph, key, a=1.0):
    """A 10 x 14 F speech bubble with a 3 x 5 glyph drawn 2 F per cell; its tail points down-right."""
    w, h = 10, 14
    f.rect(x + 1, y, w - 2, h, "k", a)
    f.rect(x, y + 1, w, h - 2, "k", a)
    f.rect(x + 1, y + 1, w - 2, h - 2, "w", a)
    f.art(x + w - 5, y + h - 1, ("kwwwk", ".kwwk", "..kwk", "...kk"), a=a)
    f.art(x + 2, y + 2, [r.replace("#", key) for r in GLYPHS[glyph]], cell=2, a=a, readable=True)


def _centered(f, cx, cy, rows, a=1.0):
    f.art(cx - len(rows[0]) // 2, cy - len(rows) // 2, rows, a=a)


# ---------- the animations (t is seconds into the loop) ----------
def _permission(f, t):
    # holds a key up toward you, with a polite double hop
    dy, legs = 0, "stand"
    for s in (0.0, 0.34):
        if s <= t < s + 0.3:
            dy, legs = _hop((t - s) / 0.3, 3), "tuck"
    critter(f, dy=dy, legs=legs, arms=("raise", "out"), eyes="blink" if 1.5 <= t < 1.62 else "open")
    f.art(1, dy - 17, KEY)


def _question(f, t):
    # a "?" pops up; head tilts toward it, then looks back at you
    lean = -2 if 0.2 <= t < 1.3 else (-1 if (0.1 <= t < 0.2 or 1.3 <= t < 1.4) else 0)
    critter(f, lean=lean, look=(-1, -1) if t < 1.4 else (0, 0), eyes="blink" if 2.0 <= t < 2.12 else "open")
    if t < 2.25:
        bubble(f, 1, -18 + (4 if t < 0.06 else (-1 if t < 0.14 else 0)), "?", "k")


def _plan(f, t):
    # reads its clipboard, then holds it up and looks at you
    showing = 1.3 <= t < 2.5
    dy = _hop((t - 1.3) / 0.25, 2) if 1.3 <= t < 1.55 else 0
    f.art(-6, dy + (0 if showing else 2), CLIPBOARD)
    critter(f, dy=dy, legs="tuck" if dy < 0 else "stand", arms=("hold_up" if showing else "hold", "out"),
            look=(-2, 1) if t < 1.3 else (0, 0), eyes="blink" if 2.8 <= t < 2.92 else "open")


def _problem(f, t):
    # shakes with > < eyes, then stares wide-eyed with a sweat drop; red "!" above
    shaking = t < 0.6
    dx = (-1, 1)[int(t * 16) % 2] if shaking else 0
    eyes = "squint" if shaking else ("blink" if 1.9 <= t < 2.02 else "wide")
    critter(f, dx=dx, eyes=eyes, arms=("out", "out") if shaking else ("down", "down"))
    bubble(f, 1 + dx, -18 + (4 if t < 0.06 else (-1 if t < 0.14 else 0)), "!", "r")
    if 0.6 <= t < 2.2:
        p = (t - 0.6) / 1.6
        f.art(31, 1 + int(6 * p), SWEAT, a=1.0 if p < 0.7 else (1 - p) / 0.3)


def _waiting(f, t):
    # taps a foot looking away, then glances at you
    tapping = t < 1.3
    lift = (3,) if tapping and int(t / 0.13) % 2 == 0 else ()
    critter(f, lift=lift, look=(2, 0) if tapping else (0, 0), arms=("down", "down"),
            eyes="blink" if 2.1 <= t < 2.22 else "open")


def _finished(f, t):
    # happy jump, a tick, sparkles
    dy, squash, legs, arms = 0, 0, "stand", ("out", "out")
    if t < 0.6:
        dy, legs, arms = _hop(t / 0.6, 6), "tuck", ("up", "up")
    elif t < 0.7:
        squash = 2
    elif t < 0.78:
        squash = 1
    critter(f, dy=dy, squash=squash, legs=legs, arms=arms, eyes="happy")
    if 0.25 <= t < 1.6:
        f.art(14, -17, CHECK, readable=True)
    for x, y, ph in ((-6, 2, 0.0), (27, -13, 0.33), (39, 10, 0.66)):
        s = (t / LOOP[FINISHED] + ph) % 1.0
        if s < 0.25:
            _centered(f, x, y, SPARKLE)
        elif s < 0.5:
            _centered(f, x, y, SPARKLE_S)
        elif s < 0.62:
            f.rect(x, y, 1, 1, "j")


def _update(f, t):
    # waves, with a little envelope floating by
    arm = ("raise" if int(t / 0.15) % 2 == 0 else "wave") if t < 1.2 else "out"
    critter(f, arms=(arm, "out"), eyes="blink" if 1.9 <= t < 2.02 else "open")
    f.art(-4, -14 + int(round(math.sin(t / LOOP[UPDATE] * 2 * math.pi))), ENVELOPE)


def _slab(f, x, y):
    """The laptop, closed (18 x 3 F)."""
    f.rect(x, y, 18, 3, "k")
    f.rect(x + 1, y + 1, 16, 1, "m")


def _laptop(f, lid):
    """The laptop on the floor in front of the critter, screen toward the critter: you see the back of its lid,
    `lid` F tall (0 = closed), with a little logo."""
    _slab(f, 9, 21)
    if lid > 0:
        f.rect(10, 21 - lid, 16, lid, "k")
        if lid > 2:
            f.rect(11, 22 - lid, 14, lid - 2, "l")
        if lid >= 7:
            f.art(17, 21 - lid + (lid - 3) // 2, (".o.", "ooo", ".o."))


def _working(f, t):
    if t < INTRO[WORKING]:
        _working_intro(f, t)
    else:
        _working_loop(f, t - INTRO[WORKING])


def _working_intro(f, t):
    # pulls a laptop out of its pocket, shows it, puts it down in front, opens it, sits down behind it
    if t < 0.3:
        critter(f, arms=("down", "out"), look=(-1, 1))
    elif t < 0.75:
        _slab(f, 8 - 18 * (t - 0.3) / 0.45, 11)         # slides out from behind the body
        critter(f, arms=("hold", "out"), look=(-1, 1))
    elif t < 1.05:
        p = (t - 0.75) / 0.3
        dy = _hop(p, 2)
        _slab(f, -10, 11 - int(round(5 * p)) + dy)       # ta-da
        critter(f, dy=dy, legs="tuck" if dy < 0 else "stand", arms=("hold_up", "out"))
    elif t < 1.55:
        p = (t - 1.05) / 0.5
        e = p * p * (3 - 2 * p)
        critter(f, squash=1 if p > 0.5 else 0, arms=("hold", "hold"), look=(0, 1))
        _slab(f, -10 + 19 * e, 6 + 15 * e)               # swung round in front and set down
    elif t < 1.95:
        critter(f, look=(0, 1))
        _laptop(f, int(round(9 * (t - 1.55) / 0.4)))    # opens the lid
    elif t < 2.3:
        critter(f, dy=2 if t < 2.12 else 4, look=(0, 1))
        _laptop(f, 9)
    else:
        critter(f, dy=4, eyes="blink" if 2.42 <= t < 2.52 else "open")
        _laptop(f, 9)


def _working_loop(f, t):
    # types away looking down at the screen; now and then glances up at you, or looks up to think
    glance = 1.4 <= t < 1.9 or 4.6 <= t < 5.0
    think = 3.0 <= t < 3.7
    look = (0, 0) if glance else ((-1, -2) if think else (0, 1))
    critter(f, dy=4, look=look, arms=("none", "none"), eyes="blink" if 1.6 <= t < 1.72 else "open")
    left = right = 0
    if not (glance or think):
        k = int(t * 9)
        left, right = k % 2, (k + 1) % 2
    f.rect(1, 15 + left, 5, 3, "c")                     # elbows out, hands on the keys
    f.rect(30, 15 + right, 5, 3, "c")
    _laptop(f, 9)


def _idle(f, t):
    # slow breathing, an occasional blink
    critter(f, squash=1 if t % 2.0 >= 1.0 else 0, eyes="blink" if 3.2 <= t < 3.45 else "open")


ZED = ("ZZZZZ", "...Z.", "..Z..", ".Z...", "ZZZZZ")      # a z drifting up from the sleeping critter
ZED_S = ("ZZZZ", "..Z.", ".Z..", "ZZZZ")                  # ... and a smaller one


def _sleeping(f, t):
    if t < INTRO[SLEEPING]:
        _sleeping_intro(f, t)
    else:
        _sleeping_loop(f, t - INTRO[SLEEPING])


def _sleeping_intro(f, t):
    # a big stretch with a yawn, the eyes grow heavy, then it sinks down to doze (backwards: waking up)
    if t < 0.6:
        critter(f, arms=("up", "up"), eyes="squint", squash=-1 if t < 0.35 else 0)
    elif t < 1.0:
        critter(f, eyes="blink" if t < 0.8 else "closed", squash=1)
    else:
        p = min(1.0, (t - 1.0) / 0.6)
        critter(f, dy=int(round(4 * p * p * (3 - 2 * p))), eyes="closed", arms=("down", "down"))


def _sleeping_loop(f, t):
    # asleep: lying low, slow breathing, z's drifting up and away
    critter(f, dy=4, squash=1 if t % 4.0 >= 2.0 else 0, eyes="closed", arms=("down", "down"))
    for phase, glyph in ((0.0, ZED), (0.5, ZED_S)):
        s = (t / LOOP[SLEEPING] + phase) % 1.0
        if s < 0.8:
            a = 1.0 if s < 0.55 else (0.8 - s) / 0.25          # fades out near the top
            f.art(-2 - int(round(6 * s)), 1 - int(round(16 * s)), glyph, a=a)


def _unreadable(f, t):
    # puzzled: scratches its head, looks around, "..."
    arm = ("head" if int(t / 0.125) % 2 == 0 else "head2") if t < 1.5 else "out"
    look = (-2, -1) if t < 1.0 else ((2, -1) if t < 2.0 else (0, 0))
    critter(f, look=look, arms=(arm, "out"), eyes="blink" if 2.5 <= t < 2.65 else "open")
    for i in range(min(3, int((t % 1.5) / 0.3))):
        f.rect(11 + 6 * i, -6, 2, 2, "z")


_BUILD = {PERMISSION: _permission, QUESTION: _question, PLAN: _plan, PROBLEM: _problem, WAITING: _waiting,
          FINISHED: _finished, UPDATE: _update, WORKING: _working, IDLE: _idle,
          UNREADABLE: _unreadable, SLEEPING: _sleeping}


def _clock(state, t):
    intro = INTRO.get(state, 0.0)
    return t if t < intro else intro + (t - intro) % LOOP[state]


def frame(state, t, mirror=False, presence=1.0):
    """One frame of `state`, t seconds after it started (intro, then the loop). presence < 1: eased toward a plain
    standing pose with its props faded, for changing state."""
    presence = min(1.0, max(0.0, presence))
    f = Frame(motion=presence * presence * (3 - 2 * presence), props=presence)
    _BUILD[state](f, _clock(state, t))
    if mirror:
        f.mirror()
    return f


def walk_frame(t, right=False, turning=False):
    """The critter walking side-on (to the left, or to the right), t seconds into the walk; turning: half turned."""
    f = Frame()
    if turning:
        critter(f, side=1)
    else:
        step = int(t * 8) % 2
        critter(f, side=2, squash=step, lift=(0, 2) if step == 0 else (1, 3))
    if right:
        f.mirror()
    return f


def standing_frame(mirror=False):
    f = Frame()
    critter(f)
    if mirror:
        f.mirror()
    return f


class Animator(object):
    """Plays the current state and eases from one state to the next: the old one settles back into a plain standing
    pose while its props fade out, then the new one rises out of it while its props fade in. A state with an intro
    (working: laptop out of the pocket, sit down) plays it on the way in, and backwards on the way out. If the state
    comes back while leaving, it turns around from where it got to. Target None: settle into a plain standing pose
    and stay there (before walking off). Pure: pass it the clock."""

    def __init__(self):
        self.state = self.target = None
        self.start = 0.0        # when the current state started coming in
        self.leave = None       # when it started leaving, or None
        self.depth = 0.0        # how far in it was then: intro position (s), or presence (0..1)

    def set(self, state, now):
        if state == self.target:
            return
        self.target = state
        if self.state is None:
            self.state, self.start = state, now
        elif self.leave is None:
            self.leave, self.depth = now, self._depth(now - self.start)
        elif state == self.state:
            d = self._depth_leaving(now)
            self.start = now - (d if state in INTRO else d * ENTER_S)
            self.leave = None

    def _depth(self, t):
        return min(t, INTRO[self.state]) if self.state in INTRO else min(1.0, t / ENTER_S)

    def _exit_len(self):
        return self.depth / OUTRO_SPEED if self.state in INTRO else self.depth * EXIT_S

    def _depth_leaving(self, now):
        u = now - self.leave
        return max(0.0, self.depth - (u * OUTRO_SPEED if self.state in INTRO else u / EXIT_S))

    def _advance(self, now):
        if self.leave is not None and now - self.leave >= self._exit_len():
            self.start = self.leave + self._exit_len()
            self.state, self.leave = self.target, None

    def busy(self, now):
        """True while changing state (worth drawing at full frame rate)."""
        self._advance(now)
        if self.leave is not None:
            return True
        return self.state is not None and now - self.start < INTRO.get(self.state, ENTER_S)

    def settled(self, now):
        """True once it has settled into the plain standing pose (target None)."""
        self._advance(now)
        return self.state is None and self.leave is None

    def frame(self, now, mirror=False):
        self._advance(now)
        if self.state is None:
            return standing_frame(mirror)
        if self.leave is not None:
            d = self._depth_leaving(now)
            if self.state in INTRO:
                return frame(self.state, d, mirror)
            return frame(self.state, now - self.start, mirror, presence=d)
        t = now - self.start
        return frame(self.state, t, mirror, presence=1.0 if self.state in INTRO else t / ENTER_S)


# ---------- rasterising and drawing ----------
GX0, GY0, GW, GH = -16, -30, 72, 60     # the grid window, in F around the critter's 0..36 x 0..24 box
_RUNS = re.compile(rb"([^\x00])\1*", re.S)
_OCCUPIED = bytes([0] + [1] * 255)
_FULL = (1 << (8 * GW)) - 1             # a row as an int, 0xFF in every cell
_CLEAR = bytes(GW)


_TABLES = {}                            # palette -> lookup tables for _extrude (frames reuse a handful of palettes)


def _depth_tables(pal):
    """For a palette: its top / side entries, byte tables mapping a cell to its top / side entry (0: clear, or flat),
    and 0/1 lanes for the critter's cells and for every cell that has depth."""
    key = tuple(pal)
    t = _TABLES.get(key)
    if t is None:
        n = len(pal)
        extra, tops, sides, crit, solid = [], bytearray(256), bytearray(256), bytearray(256), bytearray(256)
        for i in range(1, n):
            k, a = pal[i]
            if k in FLAT:
                continue
            tops[i], sides[i] = n + len(extra), n + len(extra) + 1
            extra += [(k + "^", a), (k + "_", a)]
            crit[i], solid[i] = k in CRITTER, 1
        if len(_TABLES) > 512:
            _TABLES.clear()
        t = _TABLES[key] = (extra, bytes(tops), bytes(sides), bytes(crit), bytes(solid))
    return t


def _extrude(rows, pal):
    """Add the 3D slab behind what's painted: every clear cell shows the first painted cell met looking back into the
    screen (down and to the left, one cell per F of depth). That cell's top edge gives a top face (lighter), else its
    right edge gives a side (darker). Big-int "lanes" (one byte per cell) keep it to a few operations per row."""
    if 3 * len(pal) > 255:
        return                                                # (never happens: a frame has a dozen colours)
    extra, tops, sides, crit, solid = _depth_tables(pal)
    pal.extend(extra)
    # what each row shows from behind: (critter, its mask, critter + props, their mask), masks 0xFF per cell
    src, occs = [None] * GH, [0] * GH
    above = _FULL                                             # clear above (as a mask)
    for r in range(GH):
        row = rows[r]
        if row == _CLEAR:
            above = _FULL
            continue
        occ = occs[r] = int.from_bytes(row.translate(_OCCUPIED), "big") * 255
        sm = int.from_bytes(row.translate(solid), "big") * 255
        if sm:
            both = (int.from_bytes(row.translate(tops), "big") & above) | \
                (int.from_bytes(row.translate(sides), "big") & (_FULL ^ above))
            cm = int.from_bytes(row.translate(crit), "big") * 255
            src[r] = (both & cm, cm, both, sm)
        above = _FULL ^ occ
    for r in range(GH):
        free, add = None, 0
        for j in range(1, DEPTH + 1):
            sr = src[r + j] if r + j < GH else None
            if sr is None:
                continue
            c, cm = (sr[2], sr[3]) if j <= PROP_DEPTH else (sr[0], sr[1])
            if not cm:
                continue
            if free is None:
                free = _FULL ^ occs[r]
                if not free:
                    break
            sh = 8 * j
            add |= (c >> sh) & free
            free &= ~(cm >> sh)
        if add:
            rows[r] = bytearray((int.from_bytes(rows[r], "big") | add).to_bytes(GW, "big"))


def raster(items, shadow=False, depth=False):
    """Paint items into GH rows of GW palette indices (0 = clear). Returns (rows, palette of (key, alpha)).
    depth: extrude it into a 3D slab; shadow: add the drop shadow."""
    rows = [bytearray(GW) for _ in range(GH)]
    pal, index = [None], {}
    for x, y, w, h, key, a, _ in items:
        x0, x1 = max(x - GX0, 0), min(x + w - GX0, GW)
        y0, y1 = max(y - GY0, 0), min(y + h - GY0, GH)
        if x0 >= x1 or y0 >= y1 or a <= 0:
            continue
        i = index.get((key, a))
        if i is None:
            i = index[(key, a)] = len(pal)
            pal.append((key, a))
        run = bytes((i,)) * (x1 - x0)
        for r in range(y0, y1):
            rows[r][x0:x1] = run
    if depth:
        _extrude(rows, pal)
    if shadow:
        # everything fully opaque casts a shadow one cell down-right; one byte per cell as an int "lane"
        cast = bytearray(256)
        for i in range(1, len(pal)):
            cast[i] = pal[i][1] >= 1.0 and pal[i][0] not in NO_SHADOW
        si = len(pal)
        pal.append(("s", SHADOW))
        above = 0
        for r in range(GH):
            row = rows[r]
            if row == _CLEAR and not above:
                continue
            here = int.from_bytes(row.translate(cast), "big")
            if above:
                sh = (above >> 8) & ~int.from_bytes(row.translate(_OCCUPIED), "big")
                if sh:
                    rows[r] = bytearray((int.from_bytes(row, "big") | sh * si).to_bytes(GW, "big"))
            above = here
    return rows, pal


def draw(canvas, cx, cy, size_px, fr, alpha=1.0, depth=True):
    """Draw a Frame, the critter's 36 x 24 F box centred on (cx, cy) and size_px wide (its 3D depth reaches DEPTH F
    further up and to the right; depth=False draws it flat)."""
    key = (tuple(fr.items), size_px, cx, cy, alpha, depth)
    out = _DRAWN.get(key)
    if out is None:
        f = size_px / float(W)
        rows, pal = raster(fr.items, shadow=True, depth=depth)
        ox, oy = cx - W / 2.0 * f, cy - H / 2.0 * f
        xs = [int(round(ox + (i + GX0) * f)) for i in range(GW + 1)]
        ys = [int(round(oy + (j + GY0) * f)) for j in range(GH + 1)]
        groups = {}
        for r, row in enumerate(rows):
            y0, y1 = ys[r], ys[r + 1]
            if y1 <= y0 or row == _CLEAR:
                continue
            for m in _RUNS.finditer(row):
                x0, x1 = xs[m.start()], xs[m.end()]
                if x1 > x0:
                    i = row[m.start()]
                    g = groups.get(i)
                    if g is None:
                        g = groups[i] = []
                    g += (x0, y0, x1 - x0, y1 - y0)
        out = [(G.hexc(COLORS[pal[i][0]], int(round(255 * alpha * pal[i][1]))), (ctypes.c_float * len(flat))(*flat))
               for i, flat in groups.items()]
        if len(_DRAWN) >= 128:
            _DRAWN.clear()
        _DRAWN[key] = out
    canvas.fill_rect_groups(out)


_DRAWN = {}     # recent frames, ready to fill: a pose is often drawn many times over (idle, typing, the badge pulse)


def draw_fitted(canvas, x, y, w, h, fr, alpha=1.0):
    """Draw a frame of the plain critter (no props) as large as fits in the w x h box at (x, y), its 3D depth and
    shadow included (for icons)."""
    sw, sh = W + DEPTH + 1, H + DEPTH + 1
    f = min(w / float(sw), h / float(sh))
    cx = x + (w - sw * f) / 2.0 + W / 2.0 * f
    cy = y + (h - sh * f) / 2.0 + (DEPTH + H / 2.0) * f
    draw(canvas, cx, cy, W * f, fr, alpha)
