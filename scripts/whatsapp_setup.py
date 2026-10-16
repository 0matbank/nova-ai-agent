"""Phase 21 setup / check for WhatsApp on the official Cloud API.

    uv run python scripts/whatsapp_setup.py           # check everything
    uv run python scripts/whatsapp_setup.py --tunnel  # also fetch cloudflared

1. cloudflared (Cloudflare's tunnel client, Apache-2.0): downloaded from the official
   GitHub release, pinned version, SHA-256 checked (the hash is published in the
   release notes and as the asset digest) → <root>/tools/cloudflared/cloudflared.exe
2. secrets/.env: which WhatsApp keys are set (names only — values are never shown)
3. the access token: asks Meta which phone number it belongs to
4. Nova's webhook verify token (generated once in the secrets folder)
"""

import argparse
import asyncio
import hashlib
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from channels.whatsapp.api import WhatsAppAPI, WhatsAppError  # noqa: E402
from core.config import load_config, load_secrets  # noqa: E402
from core.whatsapp_service import REQUIRED, missing_secrets, verify_token  # noqa: E402

CLOUDFLARED_VERSION = "2026.10.1"
CLOUDFLARED_SHA256 = "4d2490e74082c5d48dbfedaf92576fa5bd1702379ab88eb7faa0cfbe8dc891b6"
CLOUDFLARED_URL = (f"https://github.com/cloudflare/cloudflared/releases/download/"
                   f"{CLOUDFLARED_VERSION}/cloudflared-windows-amd64.exe")


def fetch_cloudflared(dest: Path) -> bool:
    if dest.is_file() and hashlib.sha256(dest.read_bytes()).hexdigest() == CLOUDFLARED_SHA256:
        print(f"✓ cloudflared {CLOUDFLARED_VERSION} already at {dest}")
        return True
    print(f"… downloading cloudflared {CLOUDFLARED_VERSION} from the official release")
    with httpx.Client(follow_redirects=True, timeout=300) as c:
        r = c.get(CLOUDFLARED_URL)
    if r.status_code != 200:
        print(f"✗ download failed: HTTP {r.status_code}")
        return False
    digest = hashlib.sha256(r.content).hexdigest()
    if digest != CLOUDFLARED_SHA256:
        print(f"✗ SHA-256 mismatch ({digest}) — refusing to use it")
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(r.content)
    print(f"✓ cloudflared saved to {dest} (SHA-256 verified)")
    return True


async def check_token(cfg, sec) -> bool:  # type: ignore[no-untyped-def]
    token, phone = sec.get("WHATSAPP_ACCESS_TOKEN"), sec.get("WHATSAPP_PHONE_NUMBER_ID")
    assert token is not None and phone is not None
    api = WhatsAppAPI(token, phone.get_secret_value().strip(), cfg.default.whatsapp.graph_version)
    try:
        info = await api.check()
    except WhatsAppError as e:
        print(f"✗ Meta refused the token / phone number id: {e}")
        return False
    finally:
        await api.aclose()
    print(f"✓ token works — number {info.get('display_phone_number')} "
          f"({info.get('verified_name')})")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tunnel", action="store_true", help="download cloudflared")
    args = ap.parse_args()
    cfg = load_config()
    ok = True
    exe = cfg.root / cfg.default.whatsapp.cloudflared
    if args.tunnel:
        ok &= fetch_cloudflared(exe)
    else:
        print(("✓" if exe.is_file() else "✗") + f" cloudflared at {exe}"
              + ("" if exe.is_file() else " — run with --tunnel"))
        ok &= exe.is_file()
    sec = load_secrets(cfg.path("secrets_dir"))
    missing = missing_secrets(sec)
    for key in REQUIRED:
        print(("✗ missing " if key in missing else "✓ set     ") + key)
    if missing:
        print("→ fill these in secrets/.env (see docs/WHATSAPP.md), then run this again")
        return 1
    ok &= asyncio.run(check_token(cfg, sec))
    verify_token(cfg.path("secrets_dir"))
    print("✓ webhook verify token ready (secrets folder)")
    print(f"→ whatsapp.enabled is {cfg.default.whatsapp.enabled} in config/default.yaml")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
