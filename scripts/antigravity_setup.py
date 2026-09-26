"""One-time Antigravity CLI setup for Nova (Phase 13): merges Nova's safety
rules into agy's own settings.json (backup first; other settings kept).
Sign-in is separate and done by the owner: run `agy` once and sign in with
Google. No credential is read or stored here.

    uv run python scripts/antigravity_setup.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from providers.google_antigravity import settings  # noqa: E402


def main() -> int:
    changed, backup = settings.ensure()
    path = settings.settings_path()
    if not changed:
        print(f"already set: {path}")
    else:
        print(f"updated: {path}" + (f" (backup: {backup.name})" if backup else " (new file)"))
    left = settings.missing(settings.read())
    print("OK" if not left else f"still missing: {left}")
    return 0 if not left else 1


if __name__ == "__main__":
    sys.exit(main())
