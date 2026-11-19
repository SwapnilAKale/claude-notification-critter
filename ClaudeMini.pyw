# Launcher: double-click (opens with pythonw, no console) or run `pyw ClaudeMini.pyw [--demo]`.
import os
import sys

sys.dont_write_bytecode = True          # no __pycache__: the app writes no files at runtime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from claude_mini.app import main  # noqa: E402

sys.exit(main())
