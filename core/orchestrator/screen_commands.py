"""/screenshot (plan §27): the owner's own desktop, straight to Telegram."""

from __future__ import annotations

import base64

from channels.base import IncomingMessage, OutgoingMessage
from core.ipc.client import WorkerClient, WorkerDesktopLocked, WorkerError, WorkerUnavailable

LOCKED_REPLY = "🔒 PC locked — unlock করলে screenshot নেওয়া যাবে। Background কাজ চালু আছে।"
NO_WORKER_REPLY = "🖥️ Desktop Worker চলছে না (user login দরকার) — screenshot নেওয়া গেল না।"


class ScreenCommands:
    def __init__(self, desktop: WorkerClient | None) -> None:
        self.desktop = desktop

    async def screenshot(self, msg: IncomingMessage, args: list[str]) -> OutgoingMessage:
        if self.desktop is None:
            return OutgoingMessage(NO_WORKER_REPLY)
        monitor = int(args[0]) if args and args[0].isdigit() else 0
        try:
            r = await self.desktop.call("POST", "/v1/screenshot",
                                        json={"monitor": monitor, "preview_width": 1600})
        except WorkerUnavailable:
            return OutgoingMessage(NO_WORKER_REPLY)
        except WorkerDesktopLocked:
            return OutgoingMessage(LOCKED_REPLY)
        except WorkerError as e:
            return OutgoingMessage(f"❌ screenshot failed: {e}")
        caption = (f"🖥️ {r['width']}×{r['height']}"
                   + (f" · {r['monitors']} monitors" if r.get("monitors", 1) > 1 else "")
                   + (" · ⚠️ blank image" if r.get("blank") else ""))
        return OutgoingMessage(caption, photo=base64.b64decode(r["preview_jpeg_b64"]))
