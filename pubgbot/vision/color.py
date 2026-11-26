"""颜色断言：HSV 区间 + 区域像素占比。"""

from __future__ import annotations

import cv2
import numpy as np

from ..config import Rect


def _crop(frame: np.ndarray, roi: Rect) -> tuple[np.ndarray, int, int] | None:
    h, w = frame.shape[:2]
    clipped = roi.intersect(Rect(0, 0, w, h))
    if clipped is None:
        return None
    return frame[clipped.y:clipped.bottom, clipped.x:clipped.right], clipped.x, clipped.y


def color_ratio(frame: np.ndarray, roi: Rect, lower: np.ndarray, upper: np.ndarray) -> float:
    """roi 内落在 HSV 区间内的像素占比，0.0–1.0。"""
    cropped = _crop(frame, roi)
    if cropped is None:
        return 0.0
    sub, _ox, _oy = cropped
    hsv = cv2.cvtColor(sub, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, lower, upper)
    area = mask.shape[0] * mask.shape[1]
    if area == 0:
        return 0.0
    return float(cv2.countNonZero(mask)) / float(area)
