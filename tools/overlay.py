"""调试叠加图：把 ROI、匹配结果、罗盘中心、白圈画在帧上。

在游戏机上排查"识别到底看到了什么"时最有用：
    python -c "from pubgbot import capture, win; ..."（或集成进 doctor 的输出）
"""

from __future__ import annotations

import cv2
import numpy as np

from pubgbot.config import AppConfig
from pubgbot.vision.circle import Circle
from pubgbot.vision.state import Observation

GREEN = (0, 255, 0)
RED = (0, 0, 255)
CYAN = (255, 255, 0)
YELLOW = (0, 255, 255)


def render(
    frame: np.ndarray,
    cfg: AppConfig,
    obs: Observation | None = None,
    marker_x: int | None = None,
    circle: Circle | None = None,
) -> np.ndarray:
    """返回叠加后的新帧（不修改入参）。"""
    canvas = frame.copy()

    for name, spec in cfg.anchors.anchors.items():
        roi = spec.roi
        score = 0.0 if obs is None else float(obs.scores.get(name, 0.0))
        color = GREEN if score >= spec.threshold else RED
        cv2.rectangle(canvas, (roi.x, roi.y), (roi.right - 1, roi.bottom - 1), color, 1)
        cv2.putText(
            canvas,
            f"{name} {score:.2f}",
            (roi.x, max(12, roi.y - 3)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.35,
            color,
            1,
            cv2.LINE_AA,
        )

    compass = cfg.anchors.rois.get("compass")
    if compass is not None:
        cx = compass.x + compass.w // 2
        cv2.line(canvas, (cx, compass.y), (cx, compass.bottom), CYAN, 1)

    if marker_x is not None:
        cv2.line(canvas, (marker_x, 0), (marker_x, 48), YELLOW, 2)

    if circle is not None:
        cv2.circle(canvas, (int(circle.cx), int(circle.cy)), int(circle.r), YELLOW, 2)

    if obs is not None:
        cv2.putText(
            canvas,
            f"{obs.state.value} conf={obs.confidence:.2f}",
            (10, canvas.shape[0] - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            GREEN,
            2,
            cv2.LINE_AA,
        )
    return canvas
