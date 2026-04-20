import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "lib"))

if __name__ == "__main__":
    from lib.router import addon_router

    addon_router()
