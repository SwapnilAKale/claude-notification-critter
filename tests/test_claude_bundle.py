"""Reading Claude's error titles from its installed bundle, on small fake asar archives shaped like the real one.

Run:  py -3.14 -B -m unittest discover -s tests
"""
import json
import os
import shutil
import struct
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claude_mini import claude_bundle as B           # noqa: E402


def make_asar(path, files, sizes=None, offsets=None):
    """An asar archive holding `files` ({"assets/v1/x.js": bytes}), in the layout Electron writes. `sizes` and
    `offsets` override the size or offset recorded for some of them (a malformed archive)."""
    tree, data = {"files": {}}, b""
    for name, body in files.items():
        node = tree
        *dirs, leaf = name.split("/")
        for d in dirs:
            node = node["files"].setdefault(d, {"files": {}})
        node["files"][leaf] = {"size": (sizes or {}).get(name, len(body)),
                               "offset": str((offsets or {}).get(name, len(data)))}
        data += body
    j = json.dumps(tree, separators=(",", ":")).encode()
    pad = b"\0" * ((4 - len(j) % 4) % 4)
    payload = 4 + len(j) + len(pad)
    with open(path, "wb") as f:
        f.write(struct.pack("<IIII", 4, 4 + payload, payload, len(j)) + j + pad + data)


def table(titles, first="network"):
    keys = [first] + ["k%d" % i for i in range(len(titles) - 1)]
    return ("{%s}" % ",".join('%s:{defaultMessage:%s,id:"x%d"}' % (k, json.dumps(t), i)
                              for i, (k, t) in enumerate(zip(keys, titles)))).encode()


class ErrorTitles(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.asar = os.path.join(self.dir, "ion-dist.asar")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_the_largest_table_around_the_network_message(self):
        big = ["Connection lost", "Couldn\u2019t connect to {host}"] + ["Trouble %d" % i for i in range(60)]
        make_asar(self.asar, {
            "assets/v1/small.js": b"var a=" + table(["Connection lost", "Retry"]) + b";",
            "assets/v1/c221.js": b"var x=1;const iv=" + table(big) + b",L=p(2);",
            "assets/v1/style.css": b"network:{defaultMessage:\"nope\"}",
            "index.html": b"<html></html>"})
        self.assertEqual(B.error_titles(self.asar), big)

    def test_no_big_table_means_none(self):
        make_asar(self.asar, {"assets/v1/small.js": b"var a=" + table(["Connection lost", "Retry"]) + b";",
                              "assets/v1/other.js": b"console.log('hi')"})
        self.assertIsNone(B.error_titles(self.asar))

    def test_an_entry_with_a_negative_size_is_skipped(self):
        # f.read(-1) would read to the end of the archive, past the size limit
        big = ["Connection lost"] + ["Trouble %d" % i for i in range(60)]
        make_asar(self.asar, {"assets/v1/bad.js": b"var x=" + table(big) + b";"}, sizes={"assets/v1/bad.js": -1})
        self.assertIsNone(B.error_titles(self.asar))

    def test_an_entry_with_a_negative_offset_is_skipped(self):
        # base + a negative offset would land inside the archive's own header. Checked on the entry list itself: reading
        # from the wrong place cuts the table short, so error_titles would find nothing whether or not it is skipped.
        big = ["Connection lost"] + ["Trouble %d" % i for i in range(60)]
        make_asar(self.asar, {"assets/v1/bad.js": b"var x=" + table(big) + b";"}, offsets={"assets/v1/bad.js": -8})
        with open(self.asar, "rb") as f:
            self.assertEqual(list(B._files(f)), [])

    def test_not_an_archive(self):
        with open(self.asar, "wb") as f:
            f.write(b"hello")
        with self.assertRaises(ValueError):
            B.error_titles(self.asar)

    def test_the_bundle_sits_next_to_claude_exe(self):
        self.assertEqual(B.asar_path(os.path.join("C:\\", "Claude", "app", "Claude.exe")),
                         os.path.join("C:\\", "Claude", "app", "resources", "ion-dist.asar"))


if __name__ == "__main__":
    unittest.main()
