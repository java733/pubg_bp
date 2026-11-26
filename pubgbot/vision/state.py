"""状态识别：多锚点投票 + 连续帧 + 最短驻留。

识别器持有投票缓冲与当前状态，但对帧本身无副作用（不写盘、不发输入）。
锚点必须在配置里用 "state" 字段绑定到目标状态；未绑定状态的锚点只提供
辅助分数，不参与投票。
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import numpy as np

from ..config import AppConfig
from .template import match_anchor

log = logging.getLogger(__name__)


class VisualState(StrEnum):
    LOBBY = "LOBBY"
    QUEUE = "QUEUE"
    SPAWN = "SPAWN"
    PLANE = "PLANE"
    FREEFALL = "FREEFALL"
    GROUNDED = "GROUNDED"
    MARKING = "MARKING"
    DOWNED = "DOWNED"
    RESULT = "RESULT"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Observation:
    state: VisualState
    scores: dict[str, float]
    confidence: float


class StateRecognizer:
    def __init__(self, cfg: AppConfig, templates_dir: Path) -> None:
        self._cfg = cfg
        self._templates_dir = Path(templates_dir)
        self._vote_frames = max(1, cfg.policy.int_value("vote_frames"))
        self._min_dwell_ms = cfg.policy.int_value("min_dwell_ms")
        self._votes: deque[VisualState | None] = deque(maxlen=self._vote_frames)
        self._state = VisualState.UNKNOWN
        self._pending: VisualState | None = None
        self._pending_since = 0
        self._warned_anchors: set[str] = set()
        if not cfg.anchors.calibrated:
            log.warning(
                "[VISION] calibrated=false：ROI 仍是占位值，识别结果不可信"
                " -> 先在游戏机上运行 python tools/pick_roi.py 并置 calibrated=true"
            )

    @property
    def state(self) -> VisualState:
        return self._state

    def observe(self, frame: np.ndarray, now_ms: int) -> Observation:
        scores: dict[str, float] = {}
        by_state: dict[str, list[tuple[bool, float]]] = {}

        for name, spec in self._cfg.anchors.anchors.items():
            try:
                hit = match_anchor(frame, spec, self._templates_dir)
            except FileNotFoundError as exc:
                if name not in self._warned_anchors:
                    self._warned_anchors.add(name)
                    log.error("[ERR] vision/state: %s -> 该锚点永远不命中；请用 pick_roi.py 生成模板", exc)
                scores[name] = 0.0
                continue
            scores[name] = round(hit.score, 4)
            if spec.state:
                by_state.setdefault(spec.state, []).append((hit.hit, hit.score))

        candidate, confidence = self._vote_candidate(by_state)
        self._advance(candidate, confidence, now_ms)
        return Observation(
            state=self._state,
            scores=scores,
            confidence=confidence if candidate is not None else 0.0,
        )

    def _vote_candidate(
        self, by_state: dict[str, list[tuple[bool, float]]]
    ) -> tuple[VisualState | None, float]:
        best_state: VisualState | None = None
        best_conf = 0.0
        for state_name, hits in by_state.items():
            total = len(hits)
            count = sum(1 for hit, _ in hits if hit)
            qualified = count >= 2 if total >= 2 else count == 1
            if not qualified:
                continue
            conf = max(score for hit, score in hits if hit)
            try:
                state = VisualState(state_name)
            except ValueError:
                if state_name not in self._warned_anchors:
                    self._warned_anchors.add(state_name)
                    log.error(
                        "[ERR] vision/state: 配置里的 state=%r 不是合法状态 -> 可用值见 VisualState",
                        state_name,
                    )
                continue
            if conf > best_conf:
                best_conf = conf
                best_state = state
        return best_state, best_conf

    def _advance(self, candidate: VisualState | None, confidence: float, now_ms: int) -> None:
        self._votes.append(candidate)
        stable: VisualState | None = None
        if len(self._votes) == self._vote_frames and len(set(self._votes)) == 1:
            # 连续 N 帧一致（含"全都不命中" -> UNKNOWN）
            stable = self._votes[0] if self._votes[0] is not None else VisualState.UNKNOWN

        if stable is None:
            self._pending = None
            return
        if stable == self._state:
            self._pending = None
            return
        # UNKNOWN 之间互转不需要驻留：看不见就该立刻停手
        if self._state == VisualState.UNKNOWN or stable == VisualState.UNKNOWN:
            self._switch(stable, confidence, now_ms, "vote-stable")
            return
        if self._pending != stable:
            self._pending = stable
            self._pending_since = now_ms
            return
        if now_ms - self._pending_since >= self._min_dwell_ms:
            self._switch(stable, confidence, now_ms, "dwell-satisfied")

    def _switch(self, state: VisualState, confidence: float, now_ms: int, reason: str) -> None:
        old = self._state
        self._state = state
        self._pending = None
        log.info(
            "[STATE] %s -> %s conf=%.3f (%s) t=%dms", old.value, state.value, confidence, reason, now_ms
        )
