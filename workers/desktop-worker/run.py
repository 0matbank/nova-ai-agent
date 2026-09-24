"""Desktop Worker entrypoint.  uv run python workers/desktop-worker/run.py"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[1]), str(HERE)]

from service import register  # noqa: E402

from core.ipc.worker_main import serve  # noqa: E402

sys.exit(serve("desktop_worker", "desktop", "desktop", register))
