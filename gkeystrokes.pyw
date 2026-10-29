"""Double-click to start gkeystrokes (runs in the tray, no console window)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gk.main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
