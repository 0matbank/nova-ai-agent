"""Image ↔ screen coordinates for the computer-use engine (plan §72 CU-3).

A vision model answers in the pixels of the image it saw (or on a 0–1000 scale); input
needs physical screen pixels. The frame records where the image came from — capture
origin (can be negative on a monitor left of / above the main one), the captured size,
and any downscale — so the mapping is exact, and a point outside the captured window is
refused instead of clicking somewhere else. Pattern after Dodaios' CoordinateMapper
(Apache-2.0) and mobilerun's normalized-coordinate tests (MIT); written for Nova."""

from __future__ import annotations

from dataclasses import dataclass


class GeometryError(ValueError):
    pass


@dataclass(frozen=True)
class Frame:
    origin_x: int            # physical screen pixel of the image's left edge
    origin_y: int
    width: int               # physical pixels captured
    height: int
    image_width: int         # pixels of the image the model saw (may be downscaled)
    image_height: int

    @classmethod
    def captured(cls, origin: tuple[int, int], size: tuple[int, int],
                 image_size: tuple[int, int] | None = None) -> Frame:
        iw, ih = image_size or size
        if min(size) <= 0 or iw <= 0 or ih <= 0:
            raise GeometryError("empty frame")
        return cls(origin[0], origin[1], size[0], size[1], iw, ih)

    def to_screen(self, x: float, y: float) -> tuple[int, int]:
        """A point in image pixels → physical screen pixels (inside this frame only)."""
        if not (0 <= x < self.image_width and 0 <= y < self.image_height):
            raise GeometryError(f"point ({x:.0f}, {y:.0f}) is outside the "
                                f"{self.image_width}×{self.image_height} image")
        sx = self.origin_x + x * self.width / self.image_width
        sy = self.origin_y + y * self.height / self.image_height
        return int(sx), int(sy)

    def normalized_to_screen(self, x: float, y: float, scale: float = 1000.0
                             ) -> tuple[int, int]:
        """A point on a 0–scale grid of the image (Qwen/UI-TARS style) → screen pixels."""
        if not (0 <= x <= scale and 0 <= y <= scale):
            raise GeometryError(f"normalized point ({x}, {y}) is outside 0–{scale:g}")
        px = min(x / scale * self.image_width, self.image_width - 1)
        py = min(y / scale * self.image_height, self.image_height - 1)
        return self.to_screen(px, py)

    def contains(self, sx: int, sy: int) -> bool:
        return (self.origin_x <= sx < self.origin_x + self.width
                and self.origin_y <= sy < self.origin_y + self.height)


def center(rect: tuple[int, int, int, int]) -> tuple[int, int]:
    left, top, right, bottom = rect
    if right <= left or bottom <= top:
        raise GeometryError(f"empty rectangle {rect}")
    return (left + right) // 2, (top + bottom) // 2


def inside(point: tuple[int, int], rect: tuple[int, int, int, int]) -> bool:
    x, y = point
    return rect[0] <= x < rect[2] and rect[1] <= y < rect[3]
