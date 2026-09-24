"""Telegram setup helper: verify the bot token and list chat_ids that have
messaged the bot, so the owner's chat_id can be put in the whitelist.

    1. Put TELEGRAM_BOT_TOKEN=... in D:\\Personal-Agent\\secrets\\.env
    2. Send any message to the bot from your phone
    3. uv run python scripts/telegram_setup.py

Read-only: it does not reply to anyone and does not acknowledge updates.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from channels.telegram.api import TelegramAPI, TelegramAuthError  # noqa: E402
from core.bootstrap import bootstrap  # noqa: E402
from core.config import ConfigError  # noqa: E402
from core.service import TOKEN_KEY, WHITELIST_KEY  # noqa: E402


async def _run() -> int:
    try:
        ctx = bootstrap(worker="core", console_logs=False)
    except ConfigError as e:
        print(f"config invalid: {e}")
        return 2
    token = ctx.secrets.get(TOKEN_KEY)
    if token is None:
        print(f"{TOKEN_KEY} is not set in {ctx.config.path('secrets_dir') / '.env'}")
        return 2
    api = TelegramAPI(token)
    try:
        me = await api.get_me()
        print(f"Bot OK: @{me.get('username')} (id {me.get('id')})")
        updates = await api.call("getUpdates", timeout=0)
    except TelegramAuthError:
        print("Telegram rejected the token. Check TELEGRAM_BOT_TOKEN.")
        return 3
    finally:
        await api.aclose()

    allowed_raw = ctx.secrets.get(WHITELIST_KEY)
    allowed = set((allowed_raw.get_secret_value() if allowed_raw else "").split(","))
    seen: dict[str, str] = {}
    for u in updates:
        msg = u.get("message") or {}
        chat = msg.get("chat") or {}
        if chat.get("type") != "private":
            continue
        name = " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")]))
        if chat.get("username"):
            name += f" (@{chat['username']})"
        seen[str(chat["id"])] = name
    if not seen:
        print("No private messages found yet. Send any message to the bot, then re-run.")
        return 0
    print("Private chats that messaged the bot:")
    for cid, name in seen.items():
        mark = "authorized" if cid in allowed else "NOT in whitelist"
        print(f"  chat_id={cid}  {name}  [{mark}]")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_run()))
