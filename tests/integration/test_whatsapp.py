"""Phase 21: WhatsApp on the official Cloud API — signed webhooks only, owner-only,
same handler as Telegram, approval buttons, admin commands stay on Telegram, and the
24-hour rule falls back to Telegram. The Graph API is mocked (no network)."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from channels.base import CallbackReply, IncomingCallback, IncomingMessage, OutgoingMessage
from channels.whatsapp.api import WhatsAppAPI
from channels.whatsapp.channel import ADMIN_ONLY, WhatsAppChannel, valid_signature
from channels.whatsapp.webhook import PATH, create_app

OWNER = "8801700000001"
PHONE_ID = "1111"
SECRET = SecretStr("app-secret")
VERIFY = SecretStr("verify-token-xyz")


class Graph:
    """A fake Graph API: records every call, can be told to fail."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.fail_with: int | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        body: Any = request.content
        if request.headers.get("content-type", "").startswith("application/json"):
            body = json.loads(request.content)
        self.calls.append((request.method, request.url.path, body))
        if self.fail_with is not None and request.url.path.endswith("/messages"):
            return httpx.Response(400, json={"error": {"code": self.fail_with,
                                                      "message": "window closed"}})
        if request.url.path.endswith("/media"):
            return httpx.Response(200, json={"id": "media-1"})
        if request.method == "GET" and request.url.path.endswith("/m-audio"):
            return httpx.Response(200, json={"url": "https://lookaside.example/a",
                                             "file_size": 3})
        if request.url.host == "lookaside.example":
            return httpx.Response(200, content=b"ogg")
        return httpx.Response(200, json={"messages": [{"id": "wamid.out"}], "success": True})

    def messages(self) -> list[dict[str, Any]]:
        return [b for m, p, b in self.calls if p.endswith("/messages") and isinstance(b, dict)
                and "type" in b]


def make(handler=None, callback=None):  # type: ignore[no-untyped-def]
    graph = Graph()
    api = WhatsAppAPI(SecretStr("tok"), PHONE_ID,
                      client=httpx.AsyncClient(transport=httpx.MockTransport(graph.handler)))
    got: list[IncomingMessage] = []
    fallbacks: list[tuple[str, OutgoingMessage]] = []

    async def default_handler(m: IncomingMessage) -> OutgoingMessage | None:
        got.append(m)
        return OutgoingMessage(f"echo: {m.text}")

    async def fallback(chat: str, out: OutgoingMessage) -> None:
        fallbacks.append((chat, out))
    ch = WhatsAppChannel(api, [f"+{OWNER}"], handler or default_handler, callback, fallback)
    return ch, graph, got, fallbacks


def event(*messages: dict[str, Any], phone_id: str = PHONE_ID) -> dict[str, Any]:
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {
        "messaging_product": "whatsapp", "metadata": {"phone_number_id": phone_id},
        "messages": list(messages)}}]}]}


def text(body: str, sender: str = OWNER, mid: str = "wamid.1") -> dict[str, Any]:
    return {"from": sender, "id": mid, "timestamp": "1760000000", "type": "text",
            "text": {"body": body}}


def test_owner_text_goes_to_the_same_handler_and_is_answered() -> None:
    ch, graph, got, _ = make()
    assert asyncio.run(ch.on_webhook(event(text("pc status bolo")))) == 1
    (m,) = got
    assert (m.channel, m.chat_id, m.text) == ("whatsapp", f"wa:{OWNER}", "pc status bolo")
    out = graph.messages()
    assert out[-1]["to"] == OWNER and out[-1]["text"]["body"] == "echo: pc status bolo"
    assert any(b.get("status") == "read" for _, p, b in graph.calls if isinstance(b, dict))


def test_strangers_other_numbers_and_duplicates_are_dropped() -> None:
    ch, graph, got, _ = make()
    asyncio.run(ch.on_webhook(event(text("hi", sender="8801999999999"))))
    asyncio.run(ch.on_webhook(event(text("hi"), phone_id="9999")))
    asyncio.run(ch.on_webhook(event(text("once", mid="wamid.7"))))
    asyncio.run(ch.on_webhook(event(text("once", mid="wamid.7"))))     # Meta redelivery
    assert [m.text for m in got] == ["once"]


def test_admin_commands_stay_on_telegram() -> None:
    ch, graph, got, _ = make()
    asyncio.run(ch.on_webhook(event(text("/lockdown"))))
    assert got == [] and graph.messages()[-1]["text"]["body"] == ADMIN_ONLY


def test_approval_buttons_round_trip() -> None:
    taps: list[IncomingCallback] = []

    async def on_button(cb: IncomingCallback) -> CallbackReply:
        taps.append(cb)
        return CallbackReply("ok", "✅ Approved")
    ch, graph, _, _ = make(callback=on_button)
    asyncio.run(ch.on_webhook(event(text("hello"))))                     # opens the window
    asyncio.run(ch.send(f"wa:{OWNER}", OutgoingMessage(
        "🔐 APPROVAL REQUIRED", [[("✅ Approve", "appr:abc:y"), ("❌ Reject", "appr:abc:n")]])))
    sent = graph.messages()[-1]
    assert sent["type"] == "interactive"
    assert [b["reply"]["id"] for b in sent["interactive"]["action"]["buttons"]] == \
        ["appr:abc:y", "appr:abc:n"]
    tap = {"from": OWNER, "id": "wamid.2", "timestamp": "1760000001", "type": "interactive",
           "interactive": {"type": "button_reply",
                           "button_reply": {"id": "appr:abc:y", "title": "✅ Approve"}}}
    asyncio.run(ch.on_webhook(event(tap)))
    assert taps[0].data == "appr:abc:y" and taps[0].chat_id == f"wa:{OWNER}"
    assert graph.messages()[-1]["text"]["body"] == "✅ Approved"


def test_voice_notes_are_downloaded_for_the_transcriber() -> None:
    ch, graph, got, _ = make()
    audio = {"from": OWNER, "id": "wamid.3", "timestamp": "1760000002", "type": "audio",
             "audio": {"id": "m-audio", "mime_type": "audio/ogg; codecs=opus"}}
    asyncio.run(ch.on_webhook(event(audio)))
    assert got[0].voice is not None and got[0].voice.file_id == "m-audio"
    assert asyncio.run(ch.download("m-audio")) == b"ogg"


def test_outside_the_24h_window_the_message_goes_to_telegram() -> None:
    ch, graph, _, fallbacks = make()
    asyncio.run(ch.send(f"wa:{OWNER}", OutgoingMessage("task done")))      # never wrote to us
    assert graph.messages() == [] and "task done" in fallbacks[-1][1].text
    asyncio.run(ch.on_webhook(event(text("hi"))))
    graph.fail_with = 131047                                               # Meta says: closed
    asyncio.run(ch.send(f"wa:{OWNER}", OutgoingMessage("later")))
    assert "later" in fallbacks[-1][1].text and not ch.window_open(OWNER)


def test_media_and_many_buttons() -> None:
    ch, graph, _, _ = make()
    asyncio.run(ch.on_webhook(event(text("hi"))))
    asyncio.run(ch.send(f"wa:{OWNER}", OutgoingMessage("shot", photo=b"\xff\xd8jpg")))
    assert any(p.endswith("/media") for _, p, _ in graph.calls)
    assert graph.messages()[-1]["type"] == "image"
    asyncio.run(ch.send(f"wa:{OWNER}", OutgoingMessage(
        "pick", [[("a", "1"), ("b", "2"), ("c", "3"), ("d", "4")]])))
    assert graph.messages()[-1]["type"] == "text" and "• d" in graph.messages()[-1]["text"]["body"]


def test_webhook_registration_uses_the_app_token() -> None:
    ch, graph, _, _ = make()
    asyncio.run(ch.api.register_webhook("777", SECRET, "https://x.trycloudflare.com/w", VERIFY))
    method, path, body = graph.calls[-1]
    form = dict(x.split("=", 1) for x in body.decode().split("&"))
    assert path.endswith("/777/subscriptions") and form["fields"] == "messages"
    assert form["verify_token"] == "verify-token-xyz" and form["access_token"].startswith("777%7C")


# ------------------------------------------------------------------ HTTP layer

def sign(body: bytes) -> str:
    return "sha256=" + hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()


def test_webhook_http_checks() -> None:
    ch, _, got, _ = make()
    client = TestClient(create_app(ch, SECRET, VERIFY))
    ok = client.get(PATH, params={"hub.mode": "subscribe", "hub.verify_token": "verify-token-xyz",
                                  "hub.challenge": "99"})
    assert ok.status_code == 200 and ok.text == "99"
    assert client.get(PATH, params={"hub.mode": "subscribe", "hub.verify_token": "nope",
                                    "hub.challenge": "1"}).status_code == 403
    body = json.dumps(event(text("hi"))).encode()
    assert client.post(PATH, content=body, headers={"x-hub-signature-256": "sha256=00"}
                       ).status_code == 401
    assert client.post(PATH, content=body).status_code == 401              # unsigned
    assert client.post(PATH, content=body, headers={"x-hub-signature-256": sign(body)}
                       ).status_code == 200
    assert valid_signature(SECRET, body, sign(body))
    assert not valid_signature(SECRET, body + b" ", sign(body))


@pytest.mark.parametrize("missing", ["WHATSAPP_ACCESS_TOKEN", "WHATSAPP_APP_SECRET"])
def test_service_refuses_to_start_without_its_secrets(missing: str) -> None:
    from core.config.secrets import Secrets
    from core.whatsapp_service import REQUIRED, missing_secrets
    values = {k: SecretStr("v") for k in REQUIRED if k != missing}
    assert missing_secrets(Secrets(values)) == [missing]
