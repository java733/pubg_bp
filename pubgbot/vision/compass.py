"""罗盘黄标检测与闭环转向。

思路：航点标记在顶部罗盘上是一个黄色竖条；它相对罗盘中心的水平偏移
就是需要转动的方向与幅度。逐帧小步转动即可收敛，不需要标定灵敏度。
"""

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


def find_marker_x(
    frame: np.ndarray,
    roi: Rect,
    lower: np.ndarray,
    upper: np.ndarray,
    min_pixels: int = 12,
) -> int | None:
    """返回黄标的加权质心 x（帧绝对坐标）；像素过少视为未检出。"""
    cropped = _crop(frame, roi)
    if cropped is None:
        return None
    sub, ox, _oy = cropped
    hsv = cv2.cvtColor(sub, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, lower, upper)
    if int(cv2.countNonZero(mask)) < min_pixels:
        return None
    # 注意 mask 的取值是 0/255，加权质心要用权重总和归一，不能用像素个数
    col_sums = mask.sum(axis=0, dtype=np.int64)
    weight = int(col_sums.sum())
    if weight == 0:
        return None
    weighted = int((np.arange(mask.shape[1], dtype=np.int64) * col_sums).sum())
    return ox + int(weighted / weight)


def steer_pixels(
    marker_x: int | None,
    center_x: int,
    dead_zone: int = 6,
    max_step: int = 8,
) -> int:
    """正数 = 向右转的像素量，负数 = 向左，0 = 已对准或未检出。"""
    if marker_x is None:
        return 0
    dx = marker_x - center_x
    if abs(dx) <= dead_zone:
        return 0
    return (1 if dx > 0 else -1) * min(abs(dx), max_step)
