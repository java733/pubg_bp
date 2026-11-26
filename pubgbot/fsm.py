"""状态机与决策：输入观测，输出语义命令。不碰键鼠。

委托给动作层执行命令，因此同一个 FSM 可直接用于观察器模式（空执行器）。

命令分两类：
- 一次性（MOMENTARY_KINDS）：M/Z 等是 toggle，重复发送会让地图/姿态反复翻转，
  所以只在状态切换时发一次，由 FSM 的闩锁保证；
- 电平型（press_w/release_w/turn/scan）：每帧都可以重复，动作层本身幂等。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .config import AppConfig, ConfigError
from .vision.compass import steer_pixels
from .vision.state import Observation, VisualState

log = logging.getLogger(__name__)

# 卡住时的小角度绕行步长（像素）
_UNSTICK_STEP_PX = 25

# 一次性命令：动作层每个动作只应执行一次，重复发送会变成 toggle/重复点击
MOMENTARY_KINDS = frozenset(
    {
        "jump",
        "open_map",
        "close_map",
        "mark",
        "prone",
        "stand",
        "click_start",
        "click_return",
    }
)

POSTURE_UNKNOWN = "unknown"
POSTURE_STANDING = "standing"
POSTURE_PRONE = "prone"


@dataclass(frozen=True)
class WorldView:
    """FSM 需要的最小世界信息，全部来自视觉层。"""

    marker_x: int | None
    compass_center_x: int
    dist_ratio: float | None
    health_drop_fast: bool
    mark_point: tuple[int, int] | None = None


@dataclass(frozen=True)
class Command:
    kind: str
    arg: int | tuple[int, int] | None = None
    reason: str = ""

    @property
    def momentary(self) -> bool:
        return self.kind in MOMENTARY_KINDS


class BotFsm:
    def __init__(self, cfg: AppConfig) -> None:
        self._p = cfg.policy
        self._dead_zone = cfg.policy.int_value("dead_zone_px")
        self._steer_step = cfg.policy.int_value("steer_step_px")
        self._zone_margin = cfg.policy.value("zone_margin_factor")
        self._state = VisualState.UNKNOWN
        self._mode = "idle"
        self._posture = POSTURE_UNKNOWN
        self._entered_ms = 0
        self._jumped = False
        self._last_click_ms: int | None = None
        self._marked = False
        self._mark_wait_since: int | None = None
        self._last_mark_ms: int | None = None
        self._mark_fail_count = 0
        self._map_close_pending = False
        self._last_map_close_ms: int | None = None
        self._map_reopen_cooldown = float(self._p.values.get("map_reopen_cooldown_ms", 2000))
        self._lost_since: int | None = None
        self._scan_since: int | None = None
        self._stuck_ref: float | None = None
        self._stuck_since: int | None = None
        self._stuck_count = 0
        self._locked = False
        self._missing: set[str] = set()
        self._marking_timeout = self._resolve_marking_timeout()

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def posture(self) -> str:
        return self._posture

    def tick(self, obs: Observation, world: WorldView, now_ms: int) -> Command:
        if obs.state != self._state:
            self._on_enter(obs.state, now_ms)
        state = obs.state
        if state is VisualState.UNKNOWN:
            # 地图可能已经打开、但识别器给不出状态：仍要按超时兜底补关图，
            # 否则地图会永久停住，后续所有 GROUNDED 判断都失效。
            if self._mode == "marking":
                return self._marking_tick(world, now_ms)
            return Command("idle")
        if state in (VisualState.QUEUE, VisualState.SPAWN):
            return Command("idle")
        if state is VisualState.LOBBY:
            return self._click_tick(now_ms, "click_start")
        if state is VisualState.RESULT:
            return self._click_tick(now_ms, "click_return")
        if state is VisualState.PLANE:
            return self._plane_tick(now_ms)
        if state is VisualState.FREEFALL:
            return self._freefall_tick(now_ms)
        if state is VisualState.GROUNDED:
            return self._grounded_tick(world, now_ms)
        if state is VisualState.MARKING:
            return self._marking_tick(world, now_ms)
        if state is VisualState.DOWNED:
            return self._downed_tick(now_ms)
        return Command("idle")

    # ---- 各状态 ----

    def _click_tick(self, now_ms: int, kind: str) -> Command:
        cooldown = self._p.value("start_click_cooldown_ms")
        if self._last_click_ms is None or now_ms - self._last_click_ms >= cooldown:
            self._last_click_ms = now_ms
            log.info("[FSM] 点击 %s (t=%dms)", kind, now_ms)
            return Command(kind)
        return Command("idle")

    def _plane_tick(self, now_ms: int) -> Command:
        if not self._jumped and now_ms - self._entered_ms >= self._p.value("jump_delay_ms"):
            self._jumped = True
            log.info("[FSM] 起飞后 %dms，按 F 跳伞", now_ms - self._entered_ms)
            return Command("jump")
        return Command("idle")

    def _freefall_tick(self, now_ms: int) -> Command:
        timeout = self._opt("freefall_timeout_ms")
        if timeout is not None and now_ms - self._entered_ms >= timeout:
            log.error(
                "[FSM] FREEFALL 已持续 %dms (>%d) -> 可能卡在跳伞/被地形卡住，改为原地趴下",
                now_ms - self._entered_ms,
                int(timeout),
            )
            return Command("release_w")
        return Command("press_w")

    def _downed_tick(self, now_ms: int) -> Command:
        crawl = self._opt("downed_crawl_ms")
        if crawl is not None and now_ms - self._entered_ms < crawl:
            return Command("press_w")
        cmd = self._prone_command()
        return cmd if cmd is not None else Command("idle")

    def _grounded_tick(self, world: WorldView, now_ms: int) -> Command:
        if world.health_drop_fast:
            self._enter_prone()
            cmd = self._prone_command()
            if cmd is not None:
                log.warning("[FSM] 血量骤降 -> 立即停步趴下")
                return cmd
            return Command("release_w")

        # 本局还没有成功标点：无论小地图能否看见圈，都先开图强制标一次。
        # 刚关过图时先等冷却，避免 M 连按把地图又翻回去。
        if (
            self._last_mark_ms is None
            and not self._locked
            and self._mode in ("idle", "prone")
        ):
            return self._start_marking(now_ms, "no_mark_this_match")

        if self._mode == "running":
            return self._running_tick(world, now_ms)
        if self._mode == "marking":
            return self._marking_pending_tick(now_ms)
        if self._mode == "prone":
            cmd = self._prone_command()
            if cmd is not None:
                return cmd
            if not self._locked and self._needs_new_mark(world):
                return self._start_marking(now_ms, f"outside_zone dist_ratio={world.dist_ratio}")
            return self._prone_tick(world, now_ms)

        # idle：刚落地或刚从标记回来
        if world.dist_ratio is None:
            # 小地图上认不出圈：保守趴下，宁可不动也不乱跑
            self._enter_prone()
            cmd = self._prone_command()
            return cmd if cmd is not None else Command("release_w")
        if world.dist_ratio > 1 - self._zone_margin:
            return self._start_marking(now_ms, f"outside_zone dist_ratio={world.dist_ratio:.2f}")
        self._enter_prone()
        cmd = self._prone_command()
        return cmd if cmd is not None else self._prone_tick(world, now_ms)

    def _needs_new_mark(self, world: WorldView) -> bool:
        if world.dist_ratio is None:
            return False
        if world.dist_ratio > 1 - self._zone_margin:
            log.info("[FSM] 已在圈外 (dist_ratio=%.2f) -> 重新开图标点", world.dist_ratio)
            return True
        return False

    def _remark_due(self, now_ms: int) -> bool:
        if self._last_mark_ms is None:
            return False
        return now_ms - self._last_mark_ms >= self._p.value("remark_interval_ms")

    def _start_marking(self, now_ms: int, reason: str = "need_new_mark") -> Command:
        if self._map_reopen_blocked(now_ms):
            # 刚关过图：等冷却结束再按 M，否则两次 toggle 会互相抵消
            return Command("idle", None, "map_reopen_cooldown")
        self._mode = "marking"
        self._mark_wait_since = now_ms
        log.info("[FSM] 开地图标点 (t=%dms, %s)", now_ms, reason)
        return Command("open_map", None, reason)

    def _marking_pending_tick(self, now_ms: int) -> Command:
        """已按 M、但识别器还没切到 MARKING：等待超时，随后无条件补一次 close_map。

        超时没有补关图的话，地图会一直停在打开状态，之后所有 GROUNDED 决策都失效。
        """
        if (
            self._mark_wait_since is not None
            and now_ms - self._mark_wait_since < self._marking_timeout
        ):
            return Command("idle", None, "waiting_for_map_state")
        self._mark_fail_count += 1
        limit = self._p.int_value("mark_fail_limit")
        log.warning(
            "[FSM] 按 M 后没有识别到地图状态 (%d/%d) -> 补发关图；请检查 anchors.map_open 模板",
            self._mark_fail_count,
            limit,
        )
        self._mark_wait_since = None
        self._map_close_pending = False
        self._last_map_close_ms = now_ms
        if self._mark_fail_count >= limit:
            self._lock_prone()
        else:
            self._mode = "idle"
        return Command("close_map", None, "map_state_not_recognized")

    def _prone_tick(self, world: WorldView, now_ms: int) -> Command:
        if not self._locked and self._remark_due(now_ms):
            return self._start_marking(now_ms, "remark_interval elapsed")
        # 趴着时只松开 W；姿态键绝不再按（Z 是 toggle）
        return Command("release_w") if world.dist_ratio is None else Command("idle")

    def _running_tick(self, world: WorldView, now_ms: int) -> Command:
        if world.health_drop_fast:
            self._enter_prone()
            cmd = self._prone_command()
            if cmd is not None:
                log.warning("[FSM] 跑圈中血量骤降 -> 停止前进并趴下")
                return cmd
            return Command("release_w")

        # 趴下后必须显式起身，否则 press_w 只是爬行
        if self._posture != POSTURE_STANDING:
            if self._posture == POSTURE_PRONE:
                self._posture = POSTURE_STANDING
                log.info("[FSM] 起身后再跑圈")
                return Command("stand")
            # 从未按过姿态键：角色默认站立，无需发按键
            self._posture = POSTURE_STANDING

        if world.dist_ratio is not None and world.dist_ratio <= 1 - self._zone_margin:
            log.info("[FSM] 已进入圈内 (dist_ratio=%.2f) -> 停止前进", world.dist_ratio)
            self._enter_prone()
            cmd = self._prone_command()
            return cmd if cmd is not None else Command("release_w")

        if world.marker_x is None:
            if self._lost_since is None:
                self._lost_since = now_ms
                log.warning("[FSM] 罗盘上找不到黄标 -> 原地等待并准备扫视")
            if now_ms - self._lost_since >= self._p.value("marker_lost_scan_ms"):
                if self._scan_since is None:
                    self._scan_since = now_ms
                    log.info("[FSM] 黄标持续丢失 %dms，开始扫视", now_ms - self._lost_since)
                if now_ms - self._scan_since >= self._p.value("scan_max_ms"):
                    log.warning("[FSM] 扫视失败 -> 回趴下，等下一轮重新标点")
                    self._enter_prone()
                    cmd = self._prone_command()
                    return cmd if cmd is not None else Command("release_w")
                return Command("scan")
            return Command("idle")

        self._lost_since = None
        self._scan_since = None

        if self._stuck_check(world, now_ms):
            return self._stuck_command(now_ms)

        step = steer_pixels(world.marker_x, world.compass_center_x, self._dead_zone, self._steer_step)
        if step != 0:
            return Command(
                "turn", step, f"marker_x={world.marker_x} center_x={world.compass_center_x}"
            )
        return Command("press_w", None, f"aligned dist_ratio={world.dist_ratio}")

    def _stuck_check(self, world: WorldView, now_ms: int) -> bool:
        if world.dist_ratio is None:
            return False
        if self._stuck_ref is None or abs(world.dist_ratio - self._stuck_ref) > 1e-3:
            self._stuck_ref = world.dist_ratio
            self._stuck_since = now_ms
            return False
        return self._stuck_since is not None and (
            now_ms - self._stuck_since >= self._p.value("stuck_detect_ms")
        )

    def _stuck_command(self, now_ms: int) -> Command:
        self._stuck_count += 1
        self._stuck_ref = None
        self._stuck_since = now_ms
        limit = self._p.int_value("run_fail_limit")
        log.warning("[FSM] 距离长时间未变化，疑似卡住 (%d/%d)", self._stuck_count, limit)
        if self._stuck_count >= limit:
            log.warning("[FSM] 连续卡住达上限 -> 就地趴下，不再尝试")
            self._lock_prone()
            cmd = self._prone_command()
            return cmd if cmd is not None else Command("release_w")
        return Command("turn", _UNSTICK_STEP_PX)

    def _marking_tick(self, world: WorldView, now_ms: int) -> Command:
        if self._map_close_pending:
            # 已经发过关图：等识别器切走，期间绝不再按 M（否则会把地图又打开）
            return Command("idle", None, "waiting_for_map_close")
        self._mode = "marking"
        if self._marked:
            self._map_close_pending = True
            self._last_map_close_ms = now_ms
            self._mode = "running"
            log.info("[FSM] 标点完成，关地图开始跑圈")
            return Command("close_map")
        if world.mark_point is not None:
            self._marked = True
            self._last_mark_ms = now_ms
            self._mark_wait_since = None
            self._mark_fail_count = 0
            log.info("[FSM] 地图右键标点 %s", world.mark_point)
            return Command("mark", world.mark_point)
        if self._mark_wait_since is None:
            self._mark_wait_since = now_ms
        if now_ms - self._mark_wait_since >= self._marking_timeout:
            self._mark_wait_since = None
            self._mark_fail_count += 1
            limit = self._p.int_value("mark_fail_limit")
            log.warning(
                "[FSM] 地图上 %dms 未找到可标的白圈 (%d/%d)",
                int(self._marking_timeout),
                self._mark_fail_count,
                limit,
            )
            self._map_close_pending = True
            self._last_map_close_ms = now_ms
            if self._mark_fail_count >= limit:
                log.warning("[FSM] 标点连续失败达上限 -> 就地趴下")
                self._lock_prone()
            else:
                self._mode = "idle"
            return Command("close_map")
        return Command("idle")

    # ---- 进入/工具 ----

    def _on_enter(self, state: VisualState, now_ms: int) -> None:
        old = self._state
        self._state = state
        self._entered_ms = now_ms
        self._jumped = False
        self._lost_since = None
        self._scan_since = None
        self._stuck_ref = None
        self._stuck_since = None
        if state is VisualState.MARKING:
            if not self._map_close_pending:
                self._marked = False
                self._mark_wait_since = now_ms
                self._mode = "marking"
        elif state is VisualState.GROUNDED:
            # 从标记阶段回到地面：地图已关，进入跑圈（无论是否标成功）
            self._map_close_pending = False
            if self._mode == "marking":
                self._mode = "running"
                log.info("[FSM] 标记阶段结束，转入跑圈")
            if old is VisualState.DOWNED:
                # 被救起后角色自动站起，之前记录的姿态不再可信
                self._posture = POSTURE_UNKNOWN
        elif state is VisualState.UNKNOWN:
            # UNKNOWN 可能只是地图/特效遮挡：保留 mode 与开图闩锁，避免重复按 M
            pass
        else:
            self._mode = "idle"
        if state is VisualState.DOWNED:
            self._posture = POSTURE_UNKNOWN
        if state in (VisualState.LOBBY, VisualState.QUEUE, VisualState.SPAWN):
            # 新一局：清空上一局的计数与姿态，否则会一直趴着/不标点
            self._mark_fail_count = 0
            self._stuck_count = 0
            self._locked = False
            self._last_mark_ms = None
            self._posture = POSTURE_UNKNOWN
            self._map_close_pending = False
            self._last_map_close_ms = None
        log.debug("[FSM] %s -> %s", old.value, state.value)

    def _map_reopen_blocked(self, now_ms: int) -> bool:
        if self._last_map_close_ms is None:
            return False
        return now_ms - self._last_map_close_ms < self._map_reopen_cooldown

    def _prone_command(self) -> Command | None:
        """姿态切到趴下时返回一次性命令；已经趴着则返回 None。"""
        if self._posture == POSTURE_PRONE:
            return None
        self._posture = POSTURE_PRONE
        log.info("[FSM] 趴下")
        return Command("prone")

    def _enter_prone(self) -> None:
        if self._mode != "prone":
            log.info("[FSM] 进入趴下模式")
        self._mode = "prone"

    def _lock_prone(self) -> None:
        self._locked = True
        self._enter_prone()

    def _resolve_marking_timeout(self) -> float:
        """开图超时必须覆盖「连续投票 + 最短驻留」，否则识别器永远来不及切状态。"""
        timeout = self._p.value("marking_timeout_ms")
        needed = (
            self._p.values.get("vote_frames", 3) * self._p.values.get("frame_interval_ms", 80)
            + self._p.values.get("min_dwell_ms", 500)
            + 500  # 余量：地图动画/掉帧
        )
        if timeout < needed:
            log.warning(
                "[FSM] marking_timeout_ms=%.0f 小于识别所需 %.0fms -> 已提升到 %.0fms",
                timeout,
                needed,
                needed,
            )
            return needed
        return timeout

    def _opt(self, key: str) -> float | None:
        """可选参数：缺失时停用对应功能并告警一次（而不是启动即崩）。"""
        try:
            return self._p.value(key)
        except ConfigError:
            if key not in self._missing:
                self._missing.add(key)
                log.warning("[ERR] fsm: 配置缺少 %s -> 相关功能已停用，请检查 config/policy.json", key)
            return None
