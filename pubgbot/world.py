"""世界视图：把帧里的罗盘黄标、白圈、血量变成 FSM 可用的 WorldView。"""

from __future__ import annotations

import logging
import math
import time
from collections import deque
from typing import Callable

import numpy as np

from .config import AppConfig
from .fsm import WorldView
from .vision.circle import Circle, clamp_to, detect_circle, nearest_edge_point
from .vision.color import color_ratio
from .vision.compass import find_marker_x
from .vision.state import VisualState

log = logging.getLogger(__name__)


class HealthTracker:
    """血量条白色占比骤降检测。

    只看一段时间窗口内的相对下降（百分比），不依赖绝对血量数值，
    因此 HUD 缩放/色盲模式之外的小差异不会误报。
    """

    def __init__(self, cfg: AppConfig) -> None:
        self._roi = cfg.anchors.roi("health")
        self._lo, self._hi = cfg.anchors.hsv_range("health_white")
        self._window_ms = cfg.policy.int_value("health_drop_window_ms")
        self._drop_ratio = cfg.policy.value("health_drop_ratio")
        self._samples: deque[tuple[int, float]] = deque()
        self._triggered = False

    def update(self, frame: np.ndarray, now_ms: int) -> bool:
        ratio = color_ratio(frame, self._roi, self._lo, self._hi)
        self._samples.append((now_ms, ratio))
        while self._samples and now_ms - self._samples[0][0] > self._window_ms:
            self._samples.popleft()

        peak = max(value for _ts, value in self._samples)
        if self._triggered:
            # 血量恢复（新一局/吃急救包）后重新武装
            if ratio >= peak - 0.01:
                self._triggered = False
            return False
        if peak - ratio >= self._drop_ratio:
            self._triggered = True
            log.warning(
                "[WORLD] 血量骤降: %.3f -> %.3f (窗口 %dms)", peak, ratio, self._window_ms
            )
            return True
        return False


class WorldBuilder:
    """每帧构建 WorldView。所有几何参数来自配置，缺键时在初始化阶段就报错。"""

    def __init__(
        self,
        cfg: AppConfig,
        health: HealthTracker,
        clock: Callable[[], int] | None = None,
    ) -> None:
        self._cfg = cfg
        self._health = health
        self._clock = clock or (lambda: int(time.monotonic() * 1000))
        self._compass = cfg.anchors.roi("compass")
        self._center_x = self._compass.x + self._compass.w // 2
        self._marker_lo, self._marker_hi = cfg.anchors.hsv_range("waypoint_yellow")
        self._marker_min_pixels = int(cfg.anchors.value("marker_min_pixels"))
        self._map_roi = cfg.anchors.roi("map")
        self._minimap = cfg.anchors.roi("minimap")
        self._circle_lo, self._circle_hi = cfg.anchors.hsv_range("circle_white")
        self._r_min = int(cfg.anchors.value("circle_r_min"))
        self._r_max = int(cfg.anchors.value("circle_r_max"))
        self._inner = cfg.anchors.value("circle_inner_factor")
        self._last_mark: tuple[int, int] | None = None
        self._last_dist: float | None = None
        self._warned_no_circle = False

    def build(self, frame: np.ndarray, state: VisualState) -> WorldView:
        now = self._clock()
        marker_x = find_marker_x(
            frame, self._compass, self._marker_lo, self._marker_hi, self._marker_min_pixels
        )

        mark_point: tuple[int, int] | None = None
        if state is VisualState.MARKING:
            mark_point = self._build_mark_point(frame)
        elif state in (VisualState.GROUNDED, VisualState.DOWNED):
            self._update_dist(frame)

        health_drop = self._health.update(frame, now)
        return WorldView(
            marker_x=marker_x,
            compass_center_x=self._center_x,
            dist_ratio=self._last_dist,
            health_drop_fast=health_drop,
            mark_point=mark_point,
        )

    # ---- 内部 ----

    def _build_mark_point(self, frame: np.ndarray) -> tuple[int, int] | None:
        circle = detect_circle(
            frame, self._map_roi, self._circle_lo, self._circle_hi, self._r_min, self._r_max
        )
        if circle is None:
            if not self._warned_no_circle:
                self._warned_no_circle = True
                log.warning(
                    "[WORLD] 地图上没检测到白圈 (ROI=%s) -> 检查 map ROI、色盲设置与 hsv.circle_white",
                    self._map_roi,
                )
            return self._last_mark
        self._warned_no_circle = False
        player = (self._map_roi.x + self._map_roi.w / 2.0, self._map_roi.y + self._map_roi.h / 2.0)
        point = clamp_to(self._map_roi, nearest_edge_point(player, circle, self._inner))
        if point != self._last_mark:
            log.info(
                "[WORLD] 白圈 center=(%.0f,%.0f) r=%.0f -> 标点 %s (玩家近似 %s)",
                circle.cx, circle.cy, circle.r, point, player,
            )
        self._last_mark = point
        return point

    def _update_dist(self, frame: np.ndarray) -> None:
        circle: Circle | None = detect_circle(
            frame, self._minimap, self._circle_lo, self._circle_hi, self._r_min, self._r_max
        )
        if circle is None:
            return
        cx = self._minimap.x + self._minimap.w / 2.0
        cy = self._minimap.y + self._minimap.h / 2.0
        dist = math.hypot(circle.cx - cx, circle.cy - cy)
        self._last_dist = dist / circle.r if circle.r > 0 else None
