"""Double-click source launcher for Windows users with Python installed."""

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from media_dup_finder.app import main  # noqa: E402


if __name__ == "__main__":
    main()

