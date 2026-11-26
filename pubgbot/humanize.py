"""输入人类化（P0）。

把“精确坐标 + 固定节拍 + 按下即抬起”的键鼠原语换成带随机分布的版本，
只降低输入侧的可统计特征，不触碰游戏进程、内存或反作弊本身。

随机源与 sleep 都可注入，便于单测复现；enabled=false 时行为与旧版一致。
"""

from __future__ import annotations

import math
import random
import time
from typing import Callable

from .config import HumanizeConfig, Rect

# 标点落点允许的像素抖动（地图坐标，过大会把航点标到圈外）
MARK_JITTER_PX = 2


class Humanizer:
    def __init__(
        self,
        cfg: HumanizeConfig,
        rng: random.Random | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.cfg = cfg
        self._rng = rng if rng is not None else random.Random(cfg.seed)
        self._sleep = sleep if sleep is not None else time.sleep

    @property
    def enabled(self) -> bool:
        return self.cfg.enabled

    def _pause(self, span: tuple[int, int]) -> None:
        if not self.enabled:
            return
        ms = self._rng.randint(span[0], span[1])
        if ms > 0:
            self._sleep(ms / 1000.0)

    # 各类停顿：语义上分开，便于以后分别调参
    def reaction(self) -> None:
        """动作前的“反应时间”。"""
        self._pause(self.cfg.reaction_ms)

    def key_hold(self) -> None:
        """按键/鼠标按住时长。"""
        self._pause(self.cfg.key_hold_ms)

    def inter_action(self) -> None:
        """连续动作之间的间隔。"""
        self._pause(self.cfg.inter_action_ms)

    def segment_pause(self) -> None:
        """鼠标分段移动之间的间隔。"""
        self._pause(self.cfg.turn_segment_pause_ms)

    def split_ratio(self) -> float:
        """分段靠近目标时的第一段占比。"""
        if not self.enabled:
            return 1.0
        return self._rng.uniform(0.4, 0.6)

    def turn_plan(self, dx: int) -> list[tuple[int, int]]:
        """把一次水平转向拆成缓入缓出的多段，附带轻微纵向抖动。"""
        if not self.enabled or dx == 0:
            return [(dx, 0)]

        sign = 1 if dx > 0 else -1
        jitter = self.cfg.turn_jitter_ratio
        total = int(round(abs(dx) * (1.0 + self._rng.uniform(-jitter, jitter))))
        count = self._rng.randint(*self.cfg.turn_segments)
        total = max(total, count)  # 保证每段至少 1px

        weights = [math.sin(math.pi * (i + 0.5) / count) for i in range(count)]
        weight_sum = sum(weights)

        steps: list[int] = []
        remaining = total
        for i in range(count):
            left = count - i - 1
            if i == count - 1:
                step = remaining
            else:
                step = int(round(total * weights[i] / weight_sum))
            step = max(1, min(step, remaining - left))
            steps.append(step)
            remaining -= step

        return [(sign * step, self._rng.randint(-1, 1)) for step in steps]

    def scan_steps(self, base_step: int) -> list[int]:
        """扫视的步长序列：同一方向、2~4 次、每次步长 ±20%。"""
        if not self.enabled:
            return [base_step] * 3
        direction = self._rng.choice((-1, 1))
        count = self._rng.randint(2, 4)
        return [
            direction * max(1, int(round(base_step * self._rng.uniform(0.8, 1.2))))
            for _ in range(count)
        ]

    def click_point(self, rect: Rect) -> tuple[int, int]:
        """在 ROI 内做高斯偏移的落点（保留边缘 10% 不放）。"""
        cx = rect.x + rect.w / 2.0
        cy = rect.y + rect.h / 2.0
        if not self.enabled or rect.w <= 0 or rect.h <= 0:
            return int(round(cx)), int(round(cy))
        max_x = max(0.0, rect.w / 2.0 - max(2.0, rect.w * self.cfg.click_offset_ratio))
        max_y = max(0.0, rect.h / 2.0 - max(2.0, rect.h * self.cfg.click_offset_ratio))
        return int(round(cx + self._clamped_gauss(max_x))), int(round(cy + self._clamped_gauss(max_y)))

    def mark_jitter(self, x: int, y: int) -> tuple[int, int]:
        """航点坐标的轻微抖动，避免每次落在完全相同的像素。"""
        if not self.enabled:
            return x, y
        return (
            x + self._rng.randint(-MARK_JITTER_PX, MARK_JITTER_PX),
            y + self._rng.randint(-MARK_JITTER_PX, MARK_JITTER_PX),
        )

    def _clamped_gauss(self, limit: float) -> float:
        if limit <= 0:
            return 0.0
        value = self._rng.gauss(0.0, limit / 3.0)
        return max(-limit, min(limit, value))
