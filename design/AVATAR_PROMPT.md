You're adding a cute animated Claude avatar inside the "blob" of an existing Windows app called **Claude mini**,
in `C:\Users\ASUS\Desktop\Claude-mini`. Start by reading `README.md` there (what the app is and how it's laid out).
You don't need any other history: everything you need is below and in the repo.

## The goal

The blob is a small round always-on-top button (52 DIP circle inside a 76 DIP window; 65 px on this 125%-scaled
screen) that shows the state of the user's Claude sessions. Today it shows a static starburst glyph on a coloured
circle. Replace that glyph with a tiny animated character based on **Claude Code's pixel-art mascot** (the
terracotta critter with two dark square eyes, stubby side arms and four legs), and give it a distinct, readable,
loopable animation for every state, so the user can tell at a glance what Claude wants.

Reference for the look and for the **working** animation: `design/working-reference-frames.png` (frames at 6 fps)
and `design/working-reference.mp4` (5 s). In it the critter fades in, winks, raises its arms, turns side-on, walks
with its legs stepping (with a tiny pick/tool), turns back to face you, and settles. Match that style and loop.

## States to animate (all of them)

| state | when | idea (refine freely, keep it cute and obvious at 65 px) |
|---|---|---|
| `permission` | a session needs permission to run something | holds up a little key/ticket toward you, polite "may I?" hop |
| `question` | Claude asked the user a question | head tilt + a "?" bubble popping above |
| `plan` | a plan is waiting for review | holds a tiny scroll/clipboard, looks at you |
| `problem` | a session failed / hit an error | dizzy or a sweat drop, small red "!" |
| `waiting` | needs the user, reason unknown | taps a foot, glances at you |
| `finished` | a session finished, unread | happy jump, little sparkles or a ✓ |
| `update` | other news (e.g. a project post) | small wave or a tiny envelope |
| `working` | something is running, nothing needs the user | the reference loop above |
| `idle` | all quiet | gentle breathing, an occasional blink |
| `offline` | Claude isn't running | asleep, "z z", dimmed |
| `unreadable` | Claude's status can't be read right now | puzzled look / "…" (the blob also draws a ⚠ mark) |

Map the app's snapshot to a state with a pure function `avatar_state(snapshot)`:
`snapshot.level` (constants in `claude_mini/model.py`: `UNAVAILABLE`, `IDLE`, `WORKING`, `NEW`, `NEEDS_ME`) plus
`snapshot.cards[0].kind` (cards are already sorted most urgent first; kinds are `permission`, `plan`, `question`,
`problem`, `waiting`, `finished`, `update`). `UNAVAILABLE` → `offline`; `IDLE` with `snapshot.degraded` → `unreadable`.

## How the blob is drawn (read these files)

- `claude_mini/blob.py` → `Blob._render()` draws one frame with GDI+ into a per-pixel-alpha window. It already uses a
  time `t`, the DPI `scale`, and a `fade` factor for the dimmed state. It redraws every 33 ms only while
  `_update_animation()` enables `TIMER_ANIM` (today only for NEEDS_ME and WORKING). The badge (count) sits at -45°
  and the ⚠ mark at +45° on the circle's rim (mirrored when the blob is tucked into a screen edge): keep the character
  inside the circle and clear of those.
- `claude_mini/gdiplus.py` → `Canvas`: `fill_ellipse`, `fill_ellipse_vgradient`, `fill_ellipse_radial`,
  `draw_ellipse`, `draw_arc`, `draw_line`, `fill_polygon`, `fill_round_rect`, `draw_text`, `measure_text`. Add what
  you need (e.g. a crisp `fill_rect` via `GdipFillRectangle` with anti-aliasing off for pixel art; the existing
  `_fn` helper declares GDI+ functions).
- The circle's background colour already encodes the level (amber = needs you, blue = new, dark = working/idle,
  dimmed = offline). Make sure the critter reads well on each, e.g. a light outline or a cream variant on amber.
- `claude_mini/app.py` → `demo_state(i)` drives `--demo`; extend it so `--demo` cycles through every avatar state.

## Deliverables

1. `claude_mini/avatar.py`: `avatar_state(snapshot)` and a draw function such as
   `draw(canvas, cx, cy, size_px, state, t, alpha)`, procedural (drawn in code, no image files loaded at runtime).
2. Wire it into `Blob._render()` in place of the starburst glyph. Decide whether the WORKING spinner arc stays; the
   avatar can replace it. Animate every state, but keep calm states cheap (e.g. idle/offline at ~8-10 fps).
3. A preview tool, `tools/preview_avatar.py`, that renders every state as a frame strip into one PNG offscreen (GDI+
   `GdipSaveImageToFile`; see how `Canvas` is built), at 125% scale, on each state's real background colour.
4. `tests/test_avatar.py`: mapping for every level/card kind, every state draws without error, and one frame stays
   within a few milliseconds (it's pure Python at ~30 fps).
5. A short "Avatar" section in `README.md`.

## Hard rules

- Python 3.14 from `C:\Program Files\Python314`, **standard library + ctypes only**: no pip installs, no Pillow or
  other native modules (Smart App Control is on; unsigned native code won't load). Run Python with `-B`, and keep the
  app writing no files at runtime.
- Verify visuals by rendering PNGs offscreen (the preview tool) and reading them, or by running
  `"C:\Program Files\Python314\python.exe" -B ClaudeMini.pyw --demo` in the background and capturing the screen area
  around the blob with GDI BitBlt into a PNG from a script.
- Don't interact with the Claude app's UI at all, and don't change the status logic (`monitor.py`, `uia.py`,
  `notifications.py`); this task is visuals only.
- Keep all existing tests passing:
  `"C:\Program Files\Python314\python.exe" -B -m unittest discover -s tests`.
- Before finishing, show the user the preview PNG of all states, and say how to see it live (`--demo`).
