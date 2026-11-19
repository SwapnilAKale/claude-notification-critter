"""Diagnostic log, kept in memory only: the last few thousand lines of what Claude mini saw and did. Nothing is
written to disk. Each line also goes to the watchdog (see watchdog.py) through this process's output, so the lines
leading up to a crash outlive it; the tray menu copies the whole log to the clipboard."""
import collections
import sys
import threading
import time

LINES = 3000

_lines = collections.deque(maxlen=LINES)
_lock = threading.Lock()
_out = None             # the watchdog's end of our output, if it started us
earlier = ""            # the watchdog's log of earlier runs, handed over when it restarted us


def log(text):
    global _out
    line = "%s %s" % (time.strftime("%d-%m %H:%M:%S"), text)
    with _lock:
        _lines.append(line)
        if _out is not None:
            try:
                _out.write(line + "\n")
                _out.flush()
            except (OSError, ValueError):
                _out = None             # the watchdog is gone: the log stays here only


def text():
    with _lock:
        now = "\n".join(_lines)
    return (earlier.rstrip("\n") + "\n--- this run ---\n" + now) if earlier.strip() else now


class _Errors(object):
    """sys.stderr: Python's error reports (tracebacks) go into the log, line by line."""

    def __init__(self):
        self.part = ""

    def write(self, s):
        self.part += s
        while "\n" in self.part:
            line, self.part = self.part.split("\n", 1)
            log("! " + line)
        return len(s)

    def flush(self):
        pass


def start(child, echo=False):
    """Route Python's error output into the log. `child`: started by the watchdog, which reads our output and hands
    over its earlier log on our input. `echo`: also print each line (run with python.exe to watch it live)."""
    global _out
    if echo and sys.stdout is not None:
        _out = sys.stdout
    if child and sys.stdout is not None:
        _out = sys.stdout
        try:
            # Crashes Python can't recover from (access violations...) are reported straight to the watchdog.
            import faulthandler
            faulthandler.enable(file=sys.__stderr__ or sys.stdout, all_threads=True)
        except (OSError, ValueError, AttributeError):
            pass
        if sys.stdin is not None:
            threading.Thread(target=_read_earlier, name="claude-mini-earlier-log", daemon=True).start()
    sys.stderr = _Errors()


def _read_earlier():
    global earlier
    try:
        earlier = sys.stdin.read()
    except (OSError, ValueError):
        pass
