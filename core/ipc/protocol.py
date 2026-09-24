"""Internal RPC protocol (plan §17A). Every request carries:

    Authorization: Bearer <token>
    X-Request-ID:        unique per request (8–64 chars, [A-Za-z0-9-])
    X-Task-ID:           numeric task id, or "system" for non-task calls
    X-Protocol-Version:  must equal the receiver's version (worker update mismatch)
"""

from __future__ import annotations

import re
from enum import StrEnum

HEADER_REQUEST_ID = "X-Request-ID"
HEADER_TASK_ID = "X-Task-ID"
HEADER_PROTOCOL = "X-Protocol-Version"
SYSTEM_TASK = "system"

_REQUEST_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")


class RpcError(StrEnum):
    UNAUTHORIZED = "UNAUTHORIZED"
    BAD_REQUEST = "BAD_REQUEST"
    PROTOCOL_MISMATCH = "PROTOCOL_MISMATCH"
    NOT_LOOPBACK = "NOT_LOOPBACK"
    ACTION_NOT_ALLOWED = "ACTION_NOT_ALLOWED"


def valid_request_id(value: str | None) -> bool:
    return bool(value and _REQUEST_ID.match(value))


def valid_task_id(value: str | None) -> bool:
    return bool(value) and (value == SYSTEM_TASK or str(value).isdigit())
