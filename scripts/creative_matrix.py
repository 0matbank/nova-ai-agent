"""Print the creative capability matrix (config/creative.yaml + what is installed on
this PC) as Markdown — the source for docs/CREATIVE.md.

    uv run python scripts/creative_matrix.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.creative.registry import SoftwareRegistry  # noqa: E402

MARK = {"tested": "✅", "partial": "🟡", "unsupported": "⛔", "future": "🔜"}


def main() -> int:
    reg = SoftwareRegistry.load()
    found = reg.installed()
    caps = reg.config.capability_names
    print("| Software | On this PC | Automation | " + " | ".join(caps) + " |")
    print("|---|---|---|" + "---|" * len(caps))
    for key, s in reg.config.software.items():
        inst = found[key]
        where = f"✔ {inst.version or ''}".strip() if inst.found else "—"
        row = [MARK.get(s.capabilities.get(c, ""), "·") for c in caps]
        print(f"| {s.name} | {where} | {', '.join(s.automation)} | " + " | ".join(row) + " |")
    print("\n✅ tested · 🟡 partial · ⛔ unsupported · 🔜 future · · not applicable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
