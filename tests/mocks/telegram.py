"""In-memory fake of the Telegram Bot API, served through httpx.MockTransport."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any

import httpx
from pydantic import SecretStr

from channels.telegram.api import TelegramAPI

TOKEN = "123456789:" + "T" * 35
OWNER = 555000111


class FakeTelegram:
    def __init__(self, token: str = TOKEN) -> None:
        self.token = token
        self.updates: list[dict[str, Any]] = []
        self.sent: list[dict[str, Any]] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.commands: list[dict[str, str]] = []
        self.answered: list[dict[str, Any]] = []
        self.photos: list[dict[str, Any]] = []
        self.edited: list[dict[str, Any]] = []
        # method -> list of scripted responses (dict payload | "hang" | Exception)
        self.script: dict[str, list[Any]] = defaultdict(list)
        self._next_update = 1
        self._next_msg = 1

    # --------------------------------------------------------- test helpers

    def api(self, token: str | None = None) -> TelegramAPI:
        client = httpx.AsyncClient(transport=httpx.MockTransport(self._handle))
        return TelegramAPI(SecretStr(token or self.token), client, base_url="https://tg.test")

    def push_message(self, text: str | None = "hi", chat_id: int = OWNER,
                     user_id: int | None = None, chat_type: str = "private",
                     extra: dict[str, Any] | None = None) -> int:
        msg: dict[str, Any] = {
            "message_id": self._next_msg, "date": 1_700_000_000,
            "chat": {"id": chat_id, "type": chat_type},
            "from": {"id": user_id if user_id is not None else chat_id, "is_bot": False},
        }
        if text is not None:
            msg["text"] = text
        msg.update(extra or {})
        self._next_msg += 1
        uid = self._next_update
        self.updates.append({"update_id": uid, "message": msg})
        self._next_update += 1
        return uid

    def push_callback(self, data: str, chat_id: int = OWNER, user_id: int | None = None,
                      chat_type: str = "private", message_id: int = 1001,
                      message_text: str = "🔐 APPROVAL REQUIRED") -> None:
        cq = {
            "id": f"cb{self._next_update}", "data": data,
            "from": {"id": user_id if user_id is not None else chat_id, "is_bot": False},
            "message": {"message_id": message_id, "text": message_text,
                        "chat": {"id": chat_id, "type": chat_type}},
        }
        self.updates.append({"update_id": self._next_update, "callback_query": cq})
        self._next_update += 1

    def buttons_sent(self) -> list[list[dict[str, str]]]:
        return [m["reply_markup"]["inline_keyboard"] for m in self.sent
                if m.get("reply_markup", {}).get("inline_keyboard")]

    def texts_to(self, chat_id: int = OWNER) -> list[str]:
        return [m["text"] for m in self.sent if str(m["chat_id"]) == str(chat_id)]

    def error(self, method: str, code: int, description: str = "err",
              retry_after: int | None = None) -> None:
        payload: dict[str, Any] = {"ok": False, "error_code": code, "description": description}
        if retry_after is not None:
            payload["parameters"] = {"retry_after": retry_after}
        self.script[method].append(payload)

    # -------------------------------------------------------------- server

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        parts = request.url.path.strip("/").split("/")
        if len(parts) != 2 or parts[0] != f"bot{self.token}":
            return httpx.Response(401, json={"ok": False, "error_code": 401,
                                             "description": "Unauthorized"})
        method = parts[1]
        if request.headers.get("content-type", "").startswith("multipart/form-data"):
            params = self._multipart(request)
        else:
            params = json.loads(request.content or b"{}")
        self.calls.append((method, params))
        if method == "sendPhoto":
            self.photos.append(params)
            return self._ok({"message_id": 2000 + len(self.photos)})

        if self.script[method]:
            action = self.script[method].pop(0)
            if action == "hang":
                await asyncio.sleep(3600)
            if isinstance(action, Exception):
                raise action
            return httpx.Response(200, json=action)

        if method == "getMe":
            return self._ok({"id": 42, "is_bot": True, "username": "test_agent_bot"})
        if method == "getUpdates":
            offset = params.get("offset")
            if offset is not None:
                self.updates = [u for u in self.updates if u["update_id"] >= offset]
            return self._ok(list(self.updates))
        if method == "answerCallbackQuery":
            self.answered.append(params)
            return self._ok(True)
        if method == "editMessageText":
            self.edited.append(params)
            return self._ok(True)
        if method == "setMyCommands":
            self.commands = params["commands"]
            return self._ok(True)
        if method == "sendMessage":
            self.sent.append(params)
            return self._ok({"message_id": 1000 + len(self.sent)})
        return httpx.Response(404, json={"ok": False, "error_code": 404,
                                         "description": "Not Found"})

    @staticmethod
    def _multipart(request: httpx.Request) -> dict[str, Any]:
        from email.parser import BytesParser
        from email.policy import default
        raw = (f"Content-Type: {request.headers['content-type']}\r\n\r\n").encode() + \
            request.read()
        msg = BytesParser(policy=default).parsebytes(raw)
        out: dict[str, Any] = {}
        for part in msg.iter_parts():
            name = part.get_param("name", header="content-disposition")
            payload = part.get_payload(decode=True) or b""
            if part.get_filename():
                out["size"] = len(payload)
                out["filename"] = part.get_filename()
            else:
                out[str(name)] = payload.decode("utf-8")
        return out

    @staticmethod
    def _ok(result: Any) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": result})
