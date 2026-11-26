# Claude mini

A small always-on-top "blob" for the Claude desktop app on Windows. It shows, at a glance, whether any Claude
session needs you, and a hover panel tells you what each one wants, in Claude's own words.

It only reads signals the Claude app already produces:

- **Claude's own Windows notifications**, through the Windows notification listener;
- **Claude's sidebar**, through Windows UI Automation (the accessibility layer), on the Code tab or the chat tab.

It does not modify Claude, talk to the network, open ports or pipes, or write any files while it runs (state lives in
memory; Python is told not to write `__pycache__`).

## Run it

Needs the python.org Python 3.14 that's installed in `C:\Program Files\Python314`. Only the standard library is used
(ctypes + GDI+), so there's nothing to install, and it runs with Smart App Control on.

- **Search "Claude mini" in Windows search / the Start menu** (a Start menu shortcut, with the critter as its
  icon), or double-click `ClaudeMini.pyw` (it opens with `pythonw`, so no console window), or run:

```powershell
& "C:\Program Files\Python314\pythonw.exe" "C:\Users\ASUS\Desktop\Claude-mini\ClaudeMini.pyw"
```

- `--demo` cycles through made-up states (handy for looking at the visuals without real sessions).
- `--verbose` (run with `python.exe`, not `pythonw.exe`) also prints the log (below) to the console as it goes.
- **Quit** from the tray icon (right-click → Quit, then Yes: it asks first, since quitting wipes the in-memory
  diagnostic log). Only one blob runs at a time. Quit is the only way it ends: a
  close or quit message sent to its windows from outside is ignored (and logged).

**Watchdog and log.** The launcher starts a small watchdog process, which runs the critter as its child (two
`pythonw.exe` processes; `--no-watchdog` runs the critter alone). If the critter ever stops without Quit, the watchdog
starts it again within a few seconds, and the new critter's panel says why the last one stopped ("it crashed (access
violation)", "another program ended it (for example Task Manager or taskkill)", ...); click that card to copy the
log. Three unexpected stops within two minutes and it gives up with a message box instead of looping. The log is
kept **in memory only**: the last 3,000 lines of what the critter read and did, with a "still running" line every 5
minutes, so the log shows roughly when a silent stop happened. The watchdog keeps its own copy, so a log survives
the critter crashing, and the restarted critter is handed the earlier runs. Nothing is written to disk; the log is
gone when you quit or restart Windows. Copy it any time from the tray menu (**Copy diagnostic log**). If both
processes are killed at once (e.g. `taskkill /IM pythonw.exe`), nothing can restart them.
- The critter is only on screen while Claude is running (window open, or closed to the tray, where sessions keep
  working). When Claude starts, it walks in from the screen edge to its spot; when Claude quits, it finishes what
  it's doing (packs up its laptop...) and walks off the nearest edge. The app itself keeps running quietly (tray
  icon only) and waits for Claude.

## Staying up across Claude updates

Claude has no official way to ask what its sessions are doing, and its screen changes with its updates (in Nov 2026
alone: the sidebar rows twice, the usage ring's wording, the chat tab's summary). So the critter leans on what changes
least, and treats the screen as a bonus:

| What | Comes from | Exposed to Claude's updates? |
|---|---|---|
| Working, turn ended, stopped at the limit | each local session's transcript (Claude Code's own record) | rarely |
| Needs your permission, has a question, finished | Claude's Windows notifications | rarely |
| Usage | your `get_usage` hooks and Claude's own usage file | rarely |
| Unread, which session is open, cloud sessions | Claude's screen (sidebar) | yes |

(Checked 2026-11-19: in the desktop app the `Notification` and `PermissionRequest` hooks leave no trace in the
transcript, so a pending permission prompt can't be read from there; notifications cover it.)

When something it reads breaks, it says so: after 20 seconds of the same problem, a red **"Claude changed something"**
card (what stopped working, what still works; click it to copy the diagnostic log), the ⚠ mark, and a Windows
notification from the critter itself, once per problem. It checks: the sidebar shown but no row readable (a changed
layout; it then carries on from transcripts and notifications), the Claude | Code switch gone, a usage ring it can't
read, the transcripts or Claude's session records unreadable, and your usage hook stopping (at least 2 replies
finished, the first over 5 minutes ago, without the usage reading the hook writes with every reply). It only reports
what it has evidence for: not using Claude for a while, a compaction or a refused request (neither is a reply) never
counts. A problem is announced once: it counts as fixed only when a reading shows it working again (the sidebar's
rows read, the ring readable, a new usage reading...), not just because it went out of sight while Claude was behind
another app. Each break and recovery is also logged. (Claude's own usage file stopped getting new samples on 2026-11-19, with
Claude 5.11; that's noted in the log, not raised: Claude never promised that file.)

## Notification access

Already **Allowed** on this PC. If it's ever turned off, the blob shows its warning mark and says so in the panel. To
turn it back on: **Settings → Privacy & security → Notifications → "Let apps access your notifications" → On**.
No packaging (MSIX/sparse package) is needed: the listener works from a plain process on this Windows build.

## Start it with Windows

Set up on this PC: the shortcut below is in your Startup folder, so Claude mini starts with Windows and stays out
of sight until you open Claude.

Put a shortcut in your Startup folder. Either press <kbd>Win</kbd>+<kbd>R</kbd>, type `shell:startup`, and create a
shortcut there with the target below, or run this once in PowerShell:

```powershell
$s = (New-Object -ComObject WScript.Shell).CreateShortcut("$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\Claude mini.lnk"); $s.TargetPath = "C:\Program Files\Python314\pythonw.exe"; $s.Arguments = '"C:\Users\ASUS\Desktop\Claude-mini\ClaudeMini.pyw"'; $s.WorkingDirectory = "C:\Users\ASUS\Desktop\Claude-mini"; $s.Save()
```

To stop it starting with Windows, delete that shortcut.

## What you see

The "blob" is Claude Code's pixel critter standing on your desktop (no background), acting out what's going on
(see [Avatar](#avatar)), with a marker at its top-right:

| Blob | Means |
|---|---|
| **Amber count badge** (gentle pulse) | that many sessions need you: permission prompt, question, plan approval, failed/errored |
| **Blue dot** | something new: a session finished (unread), or another Claude notification |
| **Critter typing at its laptop** | something is working, nothing needs you |
| **Critter breathing** | all quiet |
| **Critter asleep, z's drifting up** | all quiet and every session's prompt cache has expired (the Context list is empty): your next message anywhere is read in full again. It stretches, dozes off, and wakes up (the same, backwards) as soon as anything happens |
| **Faded critter + ⚠** | Claude's status can't be read right now (the panel says why) |
| **No critter** | Claude isn't running |

The whole area around the critter is clickable and hoverable, not just its pixels.

Highest priority wins. A state clears when Claude's own indicator clears (you answer, open the session, ...), never
on a timer. Sleeping is the one exception, on purpose: it follows the prompt caches' lifetime (an hour, or five
minutes, after each session's last request), since that's what it tells you about. It only sleeps when the transcripts
could be read, so an empty Context list really means every cache has expired.

- **Hover** the blob → the panel: one card per session, e.g. "Needs your permission: Allow Claude to run npm test?",
  "Has a question: …", "Plan ready for your review: …", "Finished: <summary>". The gap between blob and panel counts as
  part of the hover area.
- **New notification** → the critter's marks and pose change; the panel never opens by itself (only when you hover).
- **Click a card** → Claude opens normally, on that session.
- **Click the blob** → Claude opens on the session that needs you. Clicking again steps through all of them in turn.
- **Drag** the blob anywhere; it stays exactly where you drop it. Drag it *past* a screen edge and it half-tucks into
  that edge. Nothing ever snaps, hides or moves on its own.
- **Tray icon** (the critter, with an amber or blue dot or a ⚠ like the blob; faded while Claude isn't running): status
  line, Open Claude full size, Hide critter / Show critter, Reset blob position, Copy diagnostic log, Quit. **Hide
  critter** walks it off the screen while everything else keeps running (status reading, tray icon and its marks,
  the log); **Show critter** brings it back to where it stood (tucked into an edge again if it was).

Cloud sessions count like any other. Their sidebar row carries a caption after the status icon, "Cloud" or "Cloud ·
<state>" ("Cloud · Working", "Cloud · Needs input", "Cloud · Ready for review", or a pull-request state such as
"Cloud · Merged"). When the status icon itself says nothing, a state in the caption counts instead: working makes the
critter work, "Needs input" needs you, "Ready for review" is news. A caption state the blob doesn't know is shown but
never taken for a problem, and every change in a cloud row goes into the log (in Claude's own words), so a new label
can be spotted. Under Context, the panel lists your cloud sessions (at most 4, the ones needing you first) with that
state, while they're active (working, needing you, ready for review) and for an hour after their last activity, like
the 1-hour cache of local sessions. Their rows carry no time, so the hour counts from the last change the blob saw
itself; a cloud session that had already finished when the blob started isn't listed. Only being active or finishing
counts: a change between finished states (Claude redrawing a row as "Cloud", then "Cloud · Merged") doesn't. Their transcripts live on
Anthropic's servers, which the blob never contacts, so they have no context size or cache time.

Counting follows Claude's own "N sessions need input" pill: sessions open in Claude's panes are left out while you
can actually see them (Claude is the window you're using, or at least half of it is uncovered on screen). When another
app covers Claude, or Claude is minimized or closed to the tray, open sessions count like any other, so a session
that auto-continues behind your browser still makes the critter get to work. On Claude's chat tab, Code sessions
aren't on screen, so they always count there (only sessions the chat tab itself lists can be open in front of you).

## Usage meter

A slim bar under the critter's feet shows how much of your plan's limit is used, like a health bar: Claude's
terracotta below 70%, amber from 70%, red from 90%. It follows the fullest of your limits (5-hour, weekly, per
model); hover for all of them and the reset time, e.g. "Weekly · all models: 25% used · resets in 2 days ·
5-hour limit 9%".

How it stays live without costing usage: hooks in `C:\Users\ASUS\.claude\settings.json` call the desktop app's own
`get_usage` tool when a reply finishes (`Stop`), when one fails, e.g. on hitting the limit (`StopFailure`), and
whenever a sub-agent finishes (`SubagentStop`, so a long workflow reply keeps it fresh too). Each is this entry:

```json
{ "hooks": [ { "type": "mcp_tool", "server": "ccd_session_mgmt", "tool": "get_usage", "input": {} } ] }
```

Context: under Plan usage the panel lists every session whose prompt cache is still warm (newest first, at most 5):
its title, how long its cache lives on ("cache 42 min") and how full its context is (amber from 70%, red from 85%;
Claude Code compacts at 97%). A session is on the list exactly while its cache is warm: each request to Anthropic keeps
the cache for its lifetime (your sessions write the 1-hour cache; the default is 5 minutes), so it drops off one cache
lifetime after its last request, and its next message is processed in full again. Archived sessions are left out
(Claude's session record says so), until you unarchive one and use it. Click a line, under Context or Cloud, to open
that session in Claude, like a card. The data comes from each reply's
own API usage in the transcript (tokens sent, read from and written to the cache, and which cache type), so it also
works for sessions started before the hook. The window size (for the %) comes from the hook's `get_usage` result for
that session, or else from another session on the same model (each reply records its model); only if no session on
that model has reported one does the context show as tokens ("136k").

Rewinding or editing an earlier message makes Claude carry the session on in a new transcript; the old transcript is
an abandoned branch. The panel keeps only the newest transcript per sidebar session, so a rewound session shows once.
Which transcripts belong to one sidebar session comes from Claude's own session records
(`%LOCALAPPDATA%\Claude-Data\<profile>\claude-code-sessions\...\local_<id>.json`: `cliSessionId` and
`priorCliSessionIds`), read only. That matters after a compaction: a session rewound after compacting starts its new
transcript at the compaction summary, so nothing in the transcript itself ties it to the old one. Transcripts with no
record fall back to first message and title (a rewind copies both; a fork copies the first message but Claude retitles
it "(fork)", so it keeps its own line).

After a compaction the line says "compacted" instead of a cache time: the size from before is gone and the old cache
doesn't fit the new, shorter conversation. The new size shows once the next reply comes, or straight away if you run
`/context` (its "Tokens" figure is read from the transcript). A compaction is followed by large records (reloaded
files, tool lists), so when the end of a transcript holds no request the blob reads further back (up to 16 MB).

Nobody reports how long a cache has left, not even Claude (its own "the cache has expired" notice is the same kind of
timer), so the blob checks its timer against the outcome instead: for each pair of requests it compares "the timer
said warm" with whether the API actually read most of the context from the cache (`usage.cache_check`; compactions,
model switches and gaps within a minute of the expiry are skipped). If they disagree, the line says "cache timer off
⚠". If a session's cache type or last request can't be read, the line says "cache ?" (kept for at most an hour) rather
than guessing or dropping it.

At the limit: a session that stops because a limit is full shows Claude's generic "Something went wrong" (and Claude
may send its generic "Claude is waiting for your input" notification). Claude records the refused request in the
transcript ("You've hit your session limit · resets 1:10am", with the exact reset time), which tells the blob which
session stopped at the limit. Such a session is shown as a grey "Paused at the usage limit" card ("Stopped at the
usage limit · resets in …") and doesn't count as needing you, also after the reset if Claude didn't carry it on by
itself ("… which has reset since. Send it a message to carry on"); transcripts up to a week old are checked for this.
It counts again once a request gets through, so an error after that is a real problem. A real permission prompt, plan
or question always counts. A session whose row still says "Running" is paused too once its last request was
refused and the limit hasn't reset yet: out of view, Claude redraws its sidebar late, and background tasks keep
retrying, so without this the critter kept working after every turn had stopped. (A refused request isn't a request
either: it doesn't renew the cache timer or change the context size.)

Claude's own usage ring also counts: when its percentage changes (Claude fetched new figures, e.g. because you
opened it), that figure is used straight away. Its reset time is read the way Claude writes it: "Resets in 1 hr 1
min", "Resets at 6:30 AM", "Resets Wed 4:30 PM", "Resets Nov 5, 4:30 PM" (local clock times; until 2026-11-04 "4:30"
was misread as 4 min 30 s, so a weekly limit seemed to reset within minutes and fell to 0%). Within one window usage
only grows, so a higher figure on the ring with the same reset time counts as newer even if the ring hasn't changed
since the blob started. After a reset (when the blob doesn't know the new window's reset time yet), the ring's figure
counts if it resets within one window from now and was first shown after that reset.

Between replies, Claude's own samples keep both figures current: the app fetches the 5-hour and weekly figures about
every 15 minutes while it runs, whether or not any session replies, and keeps them in
`%LOCALAPPDATA%\Claude-Data\<profile>\plan-usage-history.json`, which the blob reads (only reads). A sample newer
than the last hook reading updates those two figures; a limit whose reset time had passed by then has reset, and its
next reset time shows again with the next reply or the ring (the weekly limit resets at the same time a week
on, so its time is carried forward). So the meter is at most about 15 minutes behind even when no reply
finishes for a long time (a long turn, every session stopped at the limit).

No model is involved (Claude Code makes the call itself, not Claude), and the result isn't fed back into the
conversation; Claude Code just records it in that session's transcript under `~/.claude/projects`. The blob reads
the newest such record (`claude_mini/usage.py`, read-only; it scans only transcripts that changed, about 0.01 s).
Usage only grows while Claude works and every reply refreshes it, so an older reading is usually still right. Only
use elsewhere (claude.ai, the phone app) moves it unseen, so after 30 minutes the bar fades and the panel says how old
the figure is. When a limit's reset time passes, the bar goes until the next reply. If the hook has never run, the
blob falls back to Claude's own usage ring next to the message box, which Claude only refreshes when you open it.

Notes: the hook only works in the desktop app (the terminal `claude` has no `ccd_session_mgmt` tools, so it would
report a hook error there). Sessions started before the hook was added pick it up when they restart. To turn the
meter off, delete that hook entry.

## Avatar

The blob is an animated version of Claude Code's pixel mascot (the terracotta critter with two square eyes, stubby
arms and four legs), with its own loop for every state:

| State | When | What it does |
|---|---|---|
| permission | a session needs permission | holds up a key, polite double hop |
| question | Claude asked you something | a "?" bubble pops up, head tilts toward it, then looks at you |
| plan | a plan waits for review | reads its clipboard, then holds it up at you |
| problem | a session failed / errored | shakes with `> <` eyes, red "!" bubble, sweat drop |
| waiting | needs you, reason unknown | taps a foot looking away, then glances at you |
| finished | a session finished (unread) | happy jump with `^ ^` eyes, a tick and sparkles |
| update | other news (e.g. a project post) | waves, a little envelope floats by |
| working | something runs, nothing needs you | pulls a laptop out of its pocket, sets it down and opens it, sits behind it and types; now and then glances up at you, or looks up to think |
| idle | all quiet | slow breathing, an occasional blink |
| unreadable | status can't be read right now | scratches its head, looks around, "…" (plus the ⚠ mark) |
| sleeping | all quiet and every prompt cache has expired (nothing on screen waiting or working either) | a big stretch with squinting eyes, its eyes grow heavy and it sinks down to doze; asleep it breathes slowly with closed eyes while z's drift up and fade |

- `avatar_state(snapshot)` in `claude_mini/avatar.py` picks the state from the snapshot's level and its most urgent
  card. The critter is drawn in code (no image files) on a grid of whole pixels (3 px per unit at 125%, about 108 px
  wide), so it stays crisp. It has no background: a one-unit drop shadow and a soft ground shadow keep it readable on
  light and dark windows alike, and an almost invisible disc around it keeps that area clickable. It stays clear of
  the badge, blue dot and ⚠ mark, and keeps its props on the left (on the right when tucked into the left screen
  edge; tucked into a side edge, the badge / blue dot move to the bottom corner, away from the props).
- It looks 3D, like a little voxel toy lit from the top left: everything is a slab with depth (4 units for the critter,
  1 for its props) seen from a bit above and to the right, so you see a lighter top face and a darker right side. The
  front has a highlight along its top edge, the legs are a shade darker under the body, each eye has a tiny shine, and
  the ground shadow sits under its 3D footprint, shrinking and fading when it hops. Glints (sparkles, the "…") stay
  flat. The tray icon and the Start menu icon (`tools/make_icon.py`) use the same 3D critter.
- Changes of state are smooth: the old animation settles back into a plain standing pose while its props fade out,
  then the new one rises out of it as its props fade in; the badge, the blue dot, the warning mark and the dimming
  fade too. Working plays its laptop intro on the way in and backwards on the way out (closes the lid, gets up,
  pockets it); sleeping does the same with its stretch and doze, so waking up is the doze played backwards. If a state
  comes back mid-change, the critter turns around from where it got to. The sleeping z's are drawn without the drop
  shadow, which would otherwise add a second stroke and turn them into X's on a light window.
- Calm states redraw at 8-10 fps, the others at 15-25 fps (25 while changing state, 30 for the badge pulse); a
  frame costs about 1 ms (recent poses are kept ready to draw, so repeated ones, like idle or typing, cost less).
- See every state as frame strips on a light and a dark desktop without running the app (writes
  `design/avatar-preview.png`; add `--scale 3.75 --frames 3` for a close-up):

```powershell
& "C:\Program Files\Python314\python.exe" -B "C:\Users\ASUS\Desktop\Claude-mini\tools\preview_avatar.py"
```

- See it live: run the app with `--demo`; it steps through all 11 states, 6 s each.

## Statuses found in recon

Per session (from the sidebar, read via UI Automation): **Awaiting input** (permission, question and plan approval all
show as this), **Awaiting answer**, **Unread response** (the blue dot), **Running**, **Using the browser**, **Idle**,
**Done**, **Archived**. Also recognised if they appear: Needs input, Needs you, Waiting on you, Waiting for your answer,
Plan ready for review, Needs your approval · <tool>, and anything with failed/error/problem. On older Claude versions
(a status icon of its own) unknown labels are shown as a problem, not ignored; since 5.11 any other text is a subtitle
(see **Errors** under Limitations). The Claude/Code toggle also carries an aggregate ("Code, awaiting your input" /
"Code, unread activity"). The sidebar's own filter has no status filter (it's Active/Archived/All plus environment).
Details: [RECON.md](RECON.md).

## Differences from the original spec, and why

- **Python instead of C#/WPF.** Smart App Control blocks any new unsigned `.exe`. A script run by the signed python.org
  interpreter is allowed, with nothing to turn off or sign.
- **No mini-player of the real Claude window.** Claude won't let its window shrink below 768×509 px (~40% of your
  screen), so a small interactive Claude isn't possible without changing Claude. You chose cards instead; clicking
  the blob or a card opens Claude normally.
- **No quick-action buttons (Allow/Deny) on cards.** A prompt's buttons only exist while that session is on screen.
  Showing it in the background works, but Claude then brings itself to the front about 5 s later whenever a pending
  permission prompt is displayed, and in split view the background switch can swap the wrong pane. Since it can't be
  done without Claude popping up, it was dropped (your call).
- **Projects Overview groups aren't used.** Your Projects have no Overview pane in this Claude version, so project
  activity comes from Claude's notifications instead.
- **Notification-only items** (e.g. a project thread that isn't in the sidebar) clear when the notification leaves
  Action Center or when you open Claude from the blob, since there's no other indicator to follow for them.
- **A request for you is over once its session carries on.** If a session's transcript shows a new request to the API
  after it asked for your permission, a question or a plan, it got its answer, so that card goes even if the sidebar
  or the notification never says so. Claude cuts long session titles short in its notifications ("Local Voice setup
  and agent connection (…"); such a title is matched by its beginning.
- **Sessions already waiting before the blob started** show as "Waiting for you" (no notification text to quote).
- **The blob never resizes, pins, minimizes or switches Claude on its own.** The only things that touch Claude are
  your clicks on the blob/cards (bring Claude forward, select that session in the sidebar).

## Limitations

- **Claude 6.28 (2026-11-25):** the sidebar rows became links instead of buttons; both are read. Which sessions are
  open in Claude's panes comes from their headers ("<title>, rename session") on every reading, so it keeps working
  even when the sidebar can't be read; it's forgotten after 10 seconds without a reading. Claude's own usage samples
  older than 30 minutes are not used.

- **Claude 5.11 (2026-11-19)** changed the sidebar: rows have no status element any more, just the title, then the
  status as plain text ("Running"), none when idle. Rows are recognised by their own "More options for <title>"
  button. On the chat tab Claude now shows no status for Code sessions at all, and no "Code, working / awaiting your
  input" summary on its toggle. There, a Code session counts as working while its transcript shows a turn under way
  (a request since the turn's Stop hook, an interruption or a refused request, and written within the last 15
  minutes); questions, permissions and news come from Claude's notifications. Without a summary, news remembered
  from the Code tab stays until the Code tab or a notification says otherwise.

- **Chat tab:** Claude's chat sidebar lists your chats and, under each project, a few of its sessions. Code sessions
  the blob already knows from the Code tab are read live there. Every other Code session keeps its last Code-tab
  status, kept in check by the summary on Claude's own Code toggle ("Code, awaiting your input", "unread activity",
  "working"): anything more urgent than that summary is treated as over. If the summary says something no listed
  session explains, you get a generic "Claude Code" card (or the critter works). Notifications still arrive for every
  Code session. Clicking a card opens it right there if the chat tab lists it, otherwise Claude switches to its Code
  tab and opens it there (and goes back to the chat tab if it isn't there either). Cards are found by the
  notification's title, so this works even if the blob has never seen the Code tab.
- **Chats are not tracked** (nor Cowork tasks, which share the Claude tab's list). A chat's reply is written on
  Anthropic's servers, and the Claude app only finds out it's done when it next checks: within about half a minute
  while it's on its Claude tab, but only every few minutes while it's on the Code tab (seen: 4 minutes for a
  10-second answer). It marks nothing when it does find out (no unread label, no notification). So the blob couldn't
  tell you more than a glance at Claude, and chats running on the chat tab don't make it work either.
- **Errors:** Claude shows 166 different titles (in 6.28899) for what went wrong ("Connection lost", "Session was
  interrupted", ...). Where a row's status has its own icon (older Claude versions), any label that isn't a known
  status or a pull-request state is shown as a problem card in Claude's own words. Since Claude 5.11 a status is plain
  text next to other texts, such as a chat's "Includes an artifact" or a scheduled task's "Daily". There only a known
  status or one of Claude's error titles counts. Any other text is taken for a subtitle and noted once in the
  diagnostic log ("row texts taken for subtitles, not statuses: ..."). The error titles are read from Claude's own
  installed files (`resources\ion-dist.asar` next to `Claude.exe`, read only, once per Claude version), so a later
  Claude's new titles count too. The log says how many it found ("Claude's error titles: ..."). If they can't be read,
  the 166 copied from Claude 6.28899 are used, and a new title then shows up in the log instead of as a card, unless
  it has a word like "couldn't" or "failed" in it.
- A reading that can't find the Claude | Code switch is treated like the chat tab: only sessions already seen on the
  Code tab or with a Claude Code transcript (chats have none) count, so chats are never taken for Code sessions.
- If Claude's sidebar is hidden or the window is narrow, the blob keeps the last reading, adds new notifications on
  top, and shows a warning if that lasts more than a few seconds.
- Labels are matched in English.
- Reading Claude through UI Automation switches on Chromium's accessibility support in Claude (normal for screen
  readers; slightly more work for Claude).

## Project layout

| Path | What |
|---|---|
| `ClaudeMini.pyw` | launcher |
| `ClaudeMini.ico` | the shortcuts' icon (made by `tools/make_icon.py`) |
| `claude_mini/app.py` | controller: blob, cards, tray, clicks, demo mode |
| `claude_mini/watchdog.py` | restarts the critter if it stops without Quit, and says why |
| `claude_mini/diag.py` | the in-memory diagnostic log |
| `claude_mini/blob.py` | the blob window: drawing, drag, manual-only tuck |
| `claude_mini/avatar.py` | the animated pixel critter inside the blob |
| `claude_mini/cards.py` | the hover card panel |
| `claude_mini/monitor.py` | background thread combining both sources into a snapshot; all status rules |
| `claude_mini/usage.py` | plan usage from the get_usage Stop hook's records in Claude Code transcripts |
| `claude_mini/uia.py` | UI Automation reader for Claude's sidebar (raw COM via ctypes) |
| `claude_mini/notifications.py` | Windows notification listener (raw WinRT via ctypes) |
| `claude_mini/claude_window.py` | finding Claude's window, bringing it forward |
| `claude_mini/model.py` | snapshot + card model |
| `claude_mini/win32.py`, `gdiplus.py`, `com.py`, `wnd.py` | ctypes bindings |
| `tests/test_monitor.py` | scenario tests for the status rules |
| `tests/test_avatar.py` | avatar state mapping, drawing, layout and frame cost, the usage meter |
| `tests/test_usage.py` | reading the get_usage hook's records |
| `tests/test_watchdog.py` | why the critter stopped, and the log |
| `tools/preview_avatar.py` | renders every avatar state into a PNG, offscreen |
| `tools/make_icon.py` | draws the critter into `ClaudeMini.ico` |
| `design/` | the mascot's reference animation and the avatar preview |

Run the tests with:

```powershell
& "C:\Program Files\Python314\python.exe" -B -m unittest discover -s "C:\Users\ASUS\Desktop\Claude-mini\tests"
```
