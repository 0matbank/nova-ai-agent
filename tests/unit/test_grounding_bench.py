"""Plan §72 CU-8 — the grounding benchmark scores honestly: each model's answer is mapped
to image pixels in that model's own convention, and a missing answer is a miss."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("grounding_bench",
                                               ROOT / "scripts" / "grounding_bench.py")
assert _spec and _spec.loader
gb = importlib.util.module_from_spec(_spec)
sys.modules["grounding_bench"] = gb
_spec.loader.exec_module(gb)


def test_qwen_box_is_on_a_0_1000_grid() -> None:
    assert gb.to_point("qwen", '{"bbox_2d": [100, 200, 300, 400]}', (1000, 500)) == (200, 150)
    assert gb.to_point("qwen", '{"bbox_2d": null}', (1000, 500)) is None


def test_uitars_point_is_image_pixels_unless_the_image_was_shrunk() -> None:
    assert gb.to_point("uitars", "(71,158)", (382, 197)) == (71, 158)
    assert gb.to_point("uitars", "(10,20,30,40)", (382, 197)) == pytest.approx((20, 30))
    assert gb.to_point("uitars", "There are none.", (382, 197)) is None
    big = (8000, 6000)                                   # over the pixel cap: rescaled
    x, y = gb.to_point("uitars", "(100,100)", big)
    rw, rh = gb.smart_resize(*big, min_px=0)
    assert rw < big[0] and (x, y) == (100 / rw * big[0], 100 / rh * big[1])


def test_smart_resize_keeps_multiples_of_28() -> None:
    w, h = gb.smart_resize(1467, 917)
    assert w % 28 == 0 and h % 28 == 0 and abs(w - 1467) < 28 and abs(h - 917) < 28
