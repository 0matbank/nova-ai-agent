"""Browser Worker entrypoint.  uv run python workers/browser-worker/run.py"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[1]), str(HERE)]

from core.priority import raise_to_normal  # noqa: E402

raise_to_normal()       # Task Scheduler starts Nova at low CPU / disk / memory priority

from service import register  # noqa: E402

from core.ipc.worker_main import serve  # noqa: E402

sys.exit(serve("browser_worker", "browser", "browser", register))
