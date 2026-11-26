"""白圈检测与标点选点。

地图上的安全区是白色圆环；用 HoughCircles 主检 + 轮廓外接圆兜底。
半径不在配置范围内一律视为未检出（防止把地图边框当成圈）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

from ..config import Rect


@dataclass(frozen=True)
class Circle:
    cx: float
    cy: float
    r: float


def _crop(frame: np.ndarray, roi: Rect) -> tuple[np.ndarray, int, int] | None:
    h, w = frame.shape[:2]
    clipped = roi.intersect(Rect(0, 0, w, h))
    if clipped is None:
        return None
    return frame[clipped.y:clipped.bottom, clipped.x:clipped.right], clipped.x, clipped.y


def detect_circle(
    frame: np.ndarray,
    roi: Rect,
    lower: np.ndarray,
    upper: np.ndarray,
    r_min: int,
    r_max: int,
) -> Circle | None:
    cropped = _crop(frame, roi)
    if cropped is None:
        return None
    sub, ox, oy = cropped
    hsv = cv2.cvtColor(sub, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, lower, upper)
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

    if int(cv2.countNonZero(mask)) == 0:
        return None

    h, w = mask.shape[:2]
    circles = cv2.HoughCircles(
        mask,
        cv2.HOUGH_GRADIENT,
        dp=1.2,
        minDist=max(1, w),
        param1=100,
        param2=30,
        minRadius=int(r_min),
        maxRadius=int(r_max),
    )
    if circles is not None:
        cx, cy, r = circles[0][0]
        if r_min <= r <= r_max:
            refined = _refine_center(mask, float(cx), float(cy), float(r), int(r_min), int(r_max))
            if refined is not None:
                rx, ry, rr = refined
                return Circle(rx + ox, ry + oy, rr)
            return Circle(float(cx) + ox, float(cy) + oy, float(r))

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    (ccx, ccy), cr = cv2.minEnclosingCircle(contour)
    if not (r_min <= cr <= r_max):
        return None
    return Circle(float(ccx) + ox, float(ccy) + oy, float(cr))


def _refine_center(
    mask: np.ndarray, cx: float, cy: float, r: float, r_min: int, r_max: int
) -> tuple[float, float, float] | None:
    """用落在环带上的像素重心修正 Hough 的圆心漂移。

    Hough 在低分辨率/抗锯齿下常偏 2–4 px；对完整的圈环，环带像素的重心
    就是真实圆心。像素太少时返回 None（交给 Hough 原值）。
    """
    ys, xs = np.nonzero(mask)
    if xs.size < 20:
        return None
    dist = np.hypot(xs - cx, ys - cy)
    band = (dist >= 0.85 * r) & (dist <= 1.15 * r)
    if int(band.sum()) < 20:
        return None
    bx = float(xs[band].mean())
    by = float(ys[band].mean())
    br = float(np.hypot(xs[band] - bx, ys[band] - by).mean())
    if not (r_min <= br <= r_max):
        return None
    return bx, by, br


def nearest_edge_point(
    player: tuple[float, float], circle: Circle, factor: float = 0.9
) -> tuple[int, int]:
    """从圈心朝玩家方向走 factor*R 的点：贴在圈内侧、离玩家最近的边。

    标在 0.9R 而不是 1.0R，是为了避免标点压在白圈线上被当成圈外。
    """
    dx = float(player[0]) - circle.cx
    dy = float(player[1]) - circle.cy
    dist = math.hypot(dx, dy)
    if dist < 1e-6:
        dx, dy, dist = 0.0, -1.0, 1.0
    px = circle.cx + dx / dist * circle.r * factor
    py = circle.cy + dy / dist * circle.r * factor
    return int(round(px)), int(round(py))


def clamp_to(rect: Rect, point: tuple[int, int]) -> tuple[int, int]:
    x = min(max(int(point[0]), rect.x), rect.right - 1)
    y = min(max(int(point[1]), rect.y), rect.bottom - 1)
    return x, y
