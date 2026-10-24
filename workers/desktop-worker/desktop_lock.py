"""One desktop, one input stream (plan §72 CU-3, Report C): only one action at a time
may click/type/press keys, so two tasks never interleave their input. Reading the screen
does not need the lease."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from desktop_ui import UIError

_INPUT = threading.Lock()
WAIT_SECONDS = 5.0


@contextmanager
def input_lease(wait: float = WAIT_SECONDS) -> Iterator[None]:
    if not _INPUT.acquire(timeout=wait):
        raise UIError("desktop_busy: another action is using the keyboard/mouse")
    try:
        yield
    finally:
        _INPUT.release()


def leased(fn):  # type: ignore[no-untyped-def]
    """Wrap a worker function so it runs holding the input lease."""
    def run(*args, **kwargs):  # type: ignore[no-untyped-def]
        with input_lease():
            return fn(*args, **kwargs)
    run.__name__ = getattr(fn, "__name__", "leased")
    return run
