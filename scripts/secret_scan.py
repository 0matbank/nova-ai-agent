"""Pre-push secret scan for the PUBLIC repo (run it after `git add`, before every push).

    uv run python scripts/secret_scan.py

Looks for known token shapes and for EVERY real value from secrets/.env and
secrets/*.token in: the whole git history (`git log -p --all`), uncommitted and staged
changes, and new untracked files (a new test file once leaked the owner's chat id
because untracked files were not scanned). Prints key names only — never a value.
"""

import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import load_config  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
PATTERNS = {
    "telegram bot token": r"\b\d{8,10}:AA[0-9A-Za-z_-]{30,}",
    "google api key": r"AIza[0-9A-Za-z_-]{35}",
    "openai key": r"\bsk-[A-Za-z0-9_-]{20,}",
    "github token": r"\b(ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{20,}",
    "meta token": r"\bEAA[A-Za-z0-9]{40,}",
    "private key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "aws key": r"\bAKIA[0-9A-Z]{16}\b",
}


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True,  # noqa: S603,S607
                          encoding="utf-8", errors="replace").stdout


def corpus() -> str:
    text = git("log", "-p", "--all", "--no-color") + git("diff", "HEAD") + git("diff", "--cached")
    for f in git("ls-files", "--others", "--exclude-standard").split():
        try:
            text += (REPO / f).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return text


def secret_values(secrets_dir: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    env = secrets_dir / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                v = v.split(" #")[0].strip().strip('"').strip("'")
                if len(v) >= 8:
                    values[k.strip()] = v
    for tok in secrets_dir.glob("*.token"):
        values[tok.name] = tok.read_text(encoding="utf-8").strip()
    return values


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    text = corpus()
    bad = 0
    for name, rx in PATTERNS.items():
        n = len(re.findall(rx, text))
        if n:
            bad += 1
            print(f"LEAK: {name}: {n} hit(s)")
    values = secret_values(load_config().path("secrets_dir"))
    for key, value in values.items():
        if value in text:
            bad += 1
            print(f"LEAK: the value of {key} is in the repo / history / new files")
    print(f"scanned {len(text):,} chars, {len(values)} secret values, {len(PATTERNS)} patterns")
    print("CLEAN" if not bad else "LEAK FOUND — do not push")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
