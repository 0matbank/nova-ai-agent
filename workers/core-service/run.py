"""Core Service entrypoint.  uv run python workers/core-service/run.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.service import main  # noqa: E402

sys.exit(main())
