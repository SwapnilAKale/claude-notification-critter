# Recon findings (Phase 1)

Claude desktop 2.31226.1.0 (MSIX package `Claude_pzs8sxrjxfjjc`, AUMID `Claude_pzs8sxrjxfjjc!Claude`), Windows 11 26300.
All findings below were recorded read-only, with a passive UI Automation + notification recorder,
while you clicked through the steps. Status labels were cross-checked against the app's own
bundled strings (`resources/ion-dist`); Claude's files were only read, never changed.

## Source A: Windows notifications

- **Works unpackaged.** `UserNotificationListener` from a plain desktop process returns
  `Allowed` and lists every toast in Action Center. **No MSIX / sparse package is needed.**
  It depends only on the system toggle *Settings > Privacy & security > Notifications >
  "Let apps access your notifications"* (currently on).
- Claude's toasts: `AppInfo.PackageFamilyName == "Claude_pzs8sxrjxfjjc"`.
  Title: `<session title>` or `<folder> · <session title>`.
  Observed bodies:
  | Trigger | Body |
  |---|---|
  | Permission prompt (Bash, MCP tool, …) | `Allow Claude to run <cmd>?` / `Allow Claude to <tool> …?` |
  | Plan approval | `Allow Claude to ExitPlanMode <plan>?` |
  | Claude asks a question | the question text itself (e.g. `Blob test: pick a colour`) |
  | Turn finished | a one-line summary (older builds: `Claude finished a task`) |
  | Waiting, generic | `Claude is waiting for your input` |
  | Session's own push notification | free text |
- Claude **suppresses** a toast only when its window is *visible AND focused* AND showing that
  session (`isUserViewingSession` = `isVisible() && isFocused() && url contains session`).
  A Claude window folded behind other windows is not focused, so **toasts always fire while folded**.
  While the mini panel is open and focused on a session, that session's toasts are suppressed,
  but you are looking at it anyway.
- Claude does **not** remove a toast when you answer the prompt or open the session. (It only
  replaces a session's previous toast when it posts a new one.) Toasts therefore can't tell us
  when something is handled. Clearing has to come from Source B.
- Chrome (claude.ai) also posts look-alike toasts ("Claude has a question", "Claude needs your
  permission: Bash"). These are ignored: only the Claude app's own toasts count.

## Source B: UI Automation (Code tab sidebar)

- Claude's Chromium UI is fully exposed to UIA. No screenshots/OCR were needed.
- Each session row is a `Button` named `"<Status> <title>[ <subtitle>]"`. Its **first child** is
  the status icon, a `StatusBar` or `Image` whose `Name` is the status label; the next `Text`
  child is the title. Rows scrolled out of view are still present (`IsOffscreen = true`).
- **Complete status list** (the bundle's status map, plus what was observed live):
  | Label | Meaning | Blob |
  |---|---|---|
  | `Awaiting input` | permission prompt, question, plan approval (all observed as this label) | amber |
  | `Awaiting answer` | question (defined in the bundle, not seen on this build) | amber |
  | `Unread response` | turn finished, not yet opened (the **blue dot**) | blue |
  | `Running` | working | spinner |
  | `Using the browser` | working in the Browser pane | spinner |
  | `Idle` | nothing pending (hollow circle) | neutral |
  | `Done` | finished/closed | neutral |
  Also defined, and treated as "needs me" if they ever appear: `Needs input`, `Needs you`,
  `Waiting on you`, `Waiting for your answer`, `Plan ready for review`, `Needs your approval · <tool>`,
  and anything containing failed/error/problem.
- The sidebar's **filter menu has no per-session status filter**. *Status* = `Active / Archived / All`
  (archive state). *Environment* = `All environments, Local, Cloud, Remote Control, SSH, Slack`.
  It's currently Status = All, so nothing is hidden.
- **Mode toggle** (`Claude` | `Code` radio group): the *unselected* tab's name carries an aggregate:
  `"Code, awaiting your input"` or `"Code, unread activity"` (`{tab}, awaiting your input` /
  `{tab}, unread activity`). There are no Chat/Cowork tabs in this build.
- Clearing: opening an unread session turns `Unread response` into `Idle` immediately;
  answering a prompt goes `Awaiting input` → `Running` → `Idle`/`Unread response`.
  So the blob clears when the app's own indicator clears.

### Readability

| Situation | Sidebar rows readable? |
|---|---|
| Claude behind other windows | **Yes** (unchanged) |
| Sidebar collapsed ("Hide sidebar") | **No**: sidebar empty, mode toggle gone |
| Window narrow (≈770 px wide) | **No**: the sidebar auto-collapses |
| Chat ("Claude") mode | Project sessions **yes** (since the 2026-10 build, see below); other Code sessions **no**; the `Code, …` aggregate **yes** |
| Minimized | not tested (the blob never minimizes Claude) |

Consequences: while the mini panel is open, the sidebar is collapsed, so the blob keeps the last
reading from before it expanded and relies on Source A. When the sidebar is hidden or Claude is
in chat mode, the blob shows its small warning mark and says why in the tooltip.

### Chat tab layout (re-checked 2026-10-14)

The chat tab has two `Sidebar` groups: one holds only the `Mode` toggle, the other the list (Search, New, Projects,
Artifacts, Scheduled, Customize, Pinned, the project groups, then Recents by date). Each project group lists a few of
its sessions (then `View all`); a row is `Group{ Button <title>{Group{Text}}, StatusBar <status>, Group{Button "More
options for <title>"} }`: the status sits **beside** the button, not inside it, and is absent when there's nothing to
show. Plain chats in Recents have no status icon. The Code toggle's aggregate can also read `Code, working`.

### Claude 2.37337 layout (2026-10-25)

After the 2.37 update the status is no longer an icon named with the label. A Code-tab row is now
`Button '<title> <status>'{ Group{ Group{ Text <title>, [Text <subtitle>], StatusBar ''{ Text <status> } } },
Group{ Button 'Hide nested sessions ...' } }`, and an idle session's `StatusBar` is empty (no text at all). The labels
themselves are unchanged (`Awaiting input`, `Running`, `Unread response`, ...). The reader handles this layout and the
two older ones.

### Chats (recorded 2026-10-14)

Chats, Cowork tasks and scheduled tasks use the same status icon component and labels as Code sessions (bundle:
`ion-dist/assets/v1/shared-18-*.js`, `uD`/`dD`), but an idle chat's icon has no label. Recorded live: a chat shows
`StatusBar 'Running'` beside its row while writing; when the reply is done the icon simply disappears: no
`Unread response`, no toast, whether or not you were looking. Both toggle buttons carry the summary when not selected:
`{tab}, awaiting your input` / `{tab}, unread activity` / `{tab}, working` (e.g. `Claude, working` on the Code tab).
Internally Claude has chat / cowork / code modes; this build merges chat and cowork into the `Claude` tab.
Error titles come from `shared-14-*.js` (about 115, e.g. "Connection lost"); PR labels: No pull request, Open, Draft,
Approved, Changes requested, Merge conflicts, Queued, Merged, Closed (may be prefixed "#12 · ").

## Projects

Projects exist in chat mode (sidebar `Projects`, project rows with `Toggle sessions for <name>`),
but opening one showed **no Overview pane and no thread groups** (Ready for review / Waiting on
you / Working / Landing / Idle / Resolved). The labels exist in the bundle (`Overview`,
`Waiting on you`, `Ready for review`, `Working`, `Resolved`, `Needs you`), so the reader also
matches them if they ever appear as status icons. Project activity is otherwise covered by Source A.

## Window facts

- Main window: top-level `Chrome_WidgetWin_1`, title `Claude`, owned by `claude.exe` under
  `C:\Program Files\WindowsApps\Claude_…\app\`. The process's `MainWindowHandle` sometimes points
  at small popups, so the blob enumerates windows and takes the large titled one.
- Claude's code also flashes the taskbar button and sets a badge count for blocking requests.
