"""Keeps Claude mini running. The launcher starts this small process, which starts the critter as its child and holds
everything the critter logs in memory. If the critter ever stops without being quit from the tray, it's started
again, told why the last one stopped (shown on its panel) and handed the log so far. Nothing is written to disk."""
import collections
import ctypes
import os
import subprocess
import sys
import threading
import time

from . import win32

LINES = 3000
RESTART_DELAY = 2.0
RESTART_CODE = 75               # the critter asks to be started again (Restart in the tray menu: loads updates)
GIVE_UP = (3, 120.0)            # this many unexpected stops this close together: stop restarting
SM_SHUTTINGDOWN = 0x2000

# Exit codes, as Windows reports them for a process that ended.
_CAUSES = {
    2: "its message loop failed",
    3: "Python stopped with a fatal error",
    0xC0000005: "it crashed (access violation)",
    0xC00000FD: "it crashed (stack overflow)",
    0xC0000374: "it crashed (heap corruption)",
    0xC0000409: "it crashed (fail-fast)",
    0xC0000142: "Windows couldn't start part of it",
    0xC000013A: "Windows ended it (Ctrl+C or the console closing)",
    0x40010004: "it was ended by a debugger",
    0xFFFFFFFF: "another program ended it (for example PowerShell's Stop-Process)",
}


def explain(code, last_lines):
    """Why the critter stopped, in a few words, from its exit code and its last log lines."""
    if code == 1:
        if any("Traceback" in line for line in last_lines):
            return "it hit a Python error"
        return "another program ended it (for example Task Manager or taskkill)"
    if code in _CAUSES:
        return _CAUSES[code]
    return "it stopped unexpectedly (exit code 0x%08X)" % code


def main(script, argv):
    mutex = win32.CreateMutexW(None, True, "Local\\ClaudeMiniWatchdog")
    if ctypes.get_last_error() == 183:      # ERROR_ALREADY_EXISTS: already watched
        return 0
    lines = collections.deque(maxlen=LINES)
    stops = collections.deque(maxlen=GIVE_UP[0])
    cause = None
    env = dict(os.environ, PYTHONIOENCODING="utf-8:replace", PYTHONUNBUFFERED="1")
    try:
        while True:
            args = [sys.executable, "-B", script, "--child"] + list(argv)
            if cause:
                args += ["--restarted", cause]
            started = time.time()
            p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 env=env)
            last = collections.deque(maxlen=60)     # this run's last lines
            reader = threading.Thread(target=_read, args=(p.stdout, lines, last), daemon=True)
            reader.start()
            threading.Thread(target=_hand_over, args=(p.stdin, "\n".join(lines)), daemon=True).start()
            code = p.wait() & 0xFFFFFFFF
            reader.join(5.0)
            if code == 0 or win32.GetSystemMetrics(SM_SHUTTINGDOWN):
                return 0                    # quit from the tray (or another critter is running), or Windows is closing
            if code == RESTART_CODE:
                # Restart from the tray: start it again right away from the files on disk (so it runs any update),
                # handing over the log. Not an unexpected stop: no notice, and it doesn't count towards giving up.
                cause = None
                lines.append("%s --- Claude mini restarted from the tray menu" % time.strftime("%d-%m %H:%M:%S"))
                continue
            cause = explain(code, last)
            lines.append("%s --- Claude mini stopped after %d s: %s (exit code 0x%08X)" % (
                time.strftime("%d-%m %H:%M:%S"), time.time() - started, cause, code))
            stops.append(time.time())
            if len(stops) == stops.maxlen and stops[-1] - stops[0] < GIVE_UP[1]:
                win32.MessageBoxW(None, "Claude mini keeps stopping: %s.\n\nIt won't be restarted again until you "
                                  "start it yourself." % cause, "Claude mini", 0x30)   # MB_ICONWARNING
                return 1
            time.sleep(RESTART_DELAY)
    finally:
        win32.CloseHandle(mutex)


def _read(pipe, lines, last):
    for raw in pipe:
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        lines.append(line)
        last.append(line)


def _hand_over(pipe, text):
    try:
        pipe.write(text.encode("utf-8"))
        pipe.close()
    except OSError:
        pass                                # it stopped before reading it
