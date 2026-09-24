"""Thin async Telegram Bot API client (long polling, plan §6).

Errors are mapped onto the plan's error classes (§17F). Exception messages
never include the request URL, because the URL contains the bot token.
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import SecretStr

API_BASE = "https://api.telegram.org"
MAX_MESSAGE_LEN = 4096


class TelegramError(Exception):
    """Non-retryable API error (bad request etc.)."""


class TelegramTransientError(TelegramError):
    """TRANSIENT: network failure, timeout, 5xx — retry with backoff."""


class TelegramRetryAfter(TelegramError):
    """429 flood control — wait `retry_after` seconds."""

    def __init__(self, method: str, retry_after: int) -> None:
        super().__init__(f"{method}: rate limited, retry after {retry_after}s")
        self.retry_after = retry_after


class TelegramConflict(TelegramTransientError):
    """409 — another getUpdates poller or a webhook is active."""


class TelegramAuthError(TelegramError):
    """CRITICAL: bot token rejected (401/404). Polling must stop."""


class TelegramAPI:
    def __init__(
        self,
        token: SecretStr,
        client: httpx.AsyncClient | None = None,
        base_url: str = API_BASE,
    ) -> None:
        self._token = token
        self._client = client or httpx.AsyncClient()
        self._base = base_url.rstrip("/")

    async def aclose(self) -> None:
        await self._client.aclose()

    async def call(self, method: str, http_timeout: float = 30.0, **params: Any) -> Any:
        url = f"{self._base}/bot{self._token.get_secret_value()}/{method}"
        payload = {k: v for k, v in params.items() if v is not None}
        try:
            r = await self._client.post(url, json=payload, timeout=http_timeout)
        except httpx.TimeoutException:
            raise TelegramTransientError(f"{method}: timeout") from None
        except httpx.TransportError as e:
            raise TelegramTransientError(f"{method}: network error ({type(e).__name__})") from None

        try:
            data = r.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):
            if r.status_code >= 500:
                raise TelegramTransientError(f"{method}: HTTP {r.status_code}")
            raise TelegramError(f"{method}: HTTP {r.status_code}, non-JSON response")

        if data.get("ok"):
            return data.get("result")

        code = int(data.get("error_code") or r.status_code)
        desc = str(data.get("description", ""))
        if code == 429:
            retry = int((data.get("parameters") or {}).get("retry_after", 5))
            raise TelegramRetryAfter(method, retry)
        if code in (401, 404):
            raise TelegramAuthError(f"{method}: bot token rejected ({code})")
        if code == 409:
            raise TelegramConflict(f"{method}: conflict - {desc}")
        if code >= 500:
            raise TelegramTransientError(f"{method}: server error {code}")
        raise TelegramError(f"{method}: {code} {desc}")

    async def get_me(self) -> dict[str, Any]:
        result: dict[str, Any] = await self.call("getMe")
        return result

    async def get_updates(self, offset: int | None, timeout: int) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = await self.call(
            "getUpdates", http_timeout=timeout + 15, offset=offset, timeout=timeout,
            allowed_updates=["message", "callback_query"],
        )
        return result

    async def set_my_commands(self, commands: list[tuple[str, str]]) -> None:
        await self.call("setMyCommands", commands=[
            {"command": c.lstrip("/"), "description": d} for c, d in commands
        ])

    async def send_message(
        self, chat_id: str, text: str, reply_markup: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        result: dict[str, Any] = await self.call(
            "sendMessage", chat_id=chat_id, text=text, reply_markup=reply_markup,
            link_preview_options={"is_disabled": True},
        )
        return result


def chunk_text(text: str, limit: int = MAX_MESSAGE_LEN) -> list[str]:
    """Split on line boundaries where possible; never exceed `limit`."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) > limit:
            chunks.append(current)
            current = ""
        current += line
    if current:
        chunks.append(current)
    return chunks
