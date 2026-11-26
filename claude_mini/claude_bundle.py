"""Claude's error titles, read from its installed files (read only), so a Claude update's new ones are known too.

Claude's web bundle (resources/ion-dist.asar next to Claude.exe) holds the titles its status icon shows when a session
runs into trouble, as a table of messages: {..., network:{defaultMessage:"Connection lost",id:"..."}, ...}. The
largest such table around the "network" message is the one (166 entries in Claude 6.28899). Nothing here writes
anything; if the table can't be found, None comes back and the list built into monitor.py is used.
"""
import json
import os
import re
import struct

_ANCHOR = b"network:{defaultMessage:"
_MESSAGE = re.compile(r'\b\w+:\{defaultMessage:"((?:[^"\\]|\\.)*)"')
MIN_TITLES = 50                   # a table this big is the error table, not some other dialog's few messages
_MAX_FILE = 8 * 1024 * 1024       # skip anything bigger: the table sits in a module of a few hundred KB


def asar_path(exe_path):
    return os.path.join(os.path.dirname(exe_path), "resources", "ion-dist.asar")


def _files(f):
    """(name, offset, size) of every file in an asar archive, offsets from the start of the archive."""
    f.seek(0)
    head = f.read(16)
    if len(head) < 16:
        raise ValueError("not an asar archive")
    pickle_size = struct.unpack("<I", head[4:8])[0]
    json_size = struct.unpack("<I", head[12:16])[0]
    if json_size > 64 * 1024 * 1024:
        raise ValueError("asar header too big")
    tree = json.loads(f.read(json_size).decode("utf-8"))
    base = 8 + pickle_size
    stack = [("", tree)]
    while stack:
        path, node = stack.pop()
        for name, child in (node.get("files") or {}).items():
            if "files" in child:
                stack.append((path + "/" + name, child))
            elif "offset" in child and not child.get("unpacked"):
                offset, size = int(child["offset"]), int(child.get("size", 0))
                if offset >= 0 and size >= 0:              # a malformed entry could otherwise read to the end
                    yield path + "/" + name, base + offset, size


def _table_around(text, i):
    """The {...} object literal that contains position i, or None."""
    depth, j = 0, i
    while j > 0:
        j -= 1
        c = text[j]
        if c == "}":
            depth += 1
        elif c == "{":
            if depth == 0:
                break
            depth -= 1
    else:
        return None
    depth = 0
    for k in range(j, len(text)):
        c = text[k]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[j:k + 1]
    return None


def error_titles(asar):
    """Claude's error titles from its bundle (a list, in Claude's words, "{host}"-style placeholders kept), or None."""
    best = []
    with open(asar, "rb") as f:
        for name, offset, size in list(_files(f)):
            if not name.endswith(".js") or size > _MAX_FILE:
                continue
            f.seek(offset)
            data = f.read(size)
            i = data.find(_ANCHOR)
            if i < 0:
                continue
            text = data.decode("utf-8", "replace")
            table = _table_around(text, text.find(_ANCHOR.decode()))
            if not table:
                continue
            titles = []
            for m in _MESSAGE.finditer(table):
                try:
                    t = json.loads('"' + m.group(1) + '"')
                except ValueError:
                    continue
                if t and t not in titles:
                    titles.append(t)
            if len(titles) > len(best):
                best = titles
    return best if len(best) >= MIN_TITLES else None
