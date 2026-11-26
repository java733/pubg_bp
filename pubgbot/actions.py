"""动作层：把语义命令翻译成键鼠原语。

硬性要求：任何按下的键都必须成对释放。所有按住状态记录在 self._held，
release_all() 是唯一的兜底出口，异常/F12/看门狗路径都会调用它。

P0 人类化：按键按住时长、动作间停顿、转向分段、点击落点都由 Humanizer 采样，
让输入不再呈现“固定节拍 + 精确坐标”的特征。Humanizer 可注入，便于单测。
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Callable, Iterator, Protocol

from .config import AppConfig, Rect
from .fsm import Command
from .humanize import Humanizer

log = logging.getLogger(__name__)


class InputBackend(Protocol):
    def key_down(self, key: str) -> None: ...
    def key_up(self, key: str) -> None: ...
    def move_rel(self, dx: int, dy: int) -> None: ...
    def cursor_pos(self) -> tuple[int, int]: ...
    def mouse_down(self, button: str) -> None: ...
    def mouse_up(self, button: str) -> None: ...


class PyDirectBackend:
    """基于 pydirectinput 的真实键鼠后端（scancode 级）。

    pydirectinput 默认每次调用后 sleep(PAUSE=0.1) 且按键名是字符；
    这里关掉内置暂停，节奏由主循环和 Humanizer 控制。
    鼠标移动一律走相对位移，不用 SetCursorPos 瞬移。
    """

    def __init__(self) -> None:
        import pydirectinput
        import win32api

        self._pdi = pydirectinput
        self._win32api = win32api
        pydirectinput.PAUSE = 0.0
        pydirectinput.FAILSAFE = True

    def key_down(self, key: str) -> None:
        self._pdi.keyDown(key)

    def key_up(self, key: str) -> None:
        self._pdi.keyUp(key)

    def move_rel(self, dx: int, dy: int) -> None:
        self._pdi.moveRel(int(dx), int(dy), relative=True)

    def cursor_pos(self) -> tuple[int, int]:
        x, y = self._win32api.GetCursorPos()
        return int(x), int(y)

    def mouse_down(self, button: str) -> None:
        self._pdi.mouseDown(button=button)

    def mouse_up(self, button: str) -> None:
        self._pdi.mouseUp(button=button)


class ActionExecutor:
    def __init__(
        self,
        backend: InputBackend,
        cfg: AppConfig,
        foreground_ok: Callable[[], bool],
        humanizer: Humanizer | None = None,
        origin_provider: Callable[[], Rect] | None = None,
    ) -> None:
        self._backend = backend
        self._cfg = cfg
        self._foreground_ok = foreground_ok
        self._origin_provider = origin_provider
        self._held: list[str] = []
        self._human = humanizer if humanizer is not None else Humanizer(cfg.policy.humanize)
        self._warned_keys: set[str] = set()

    def held_keys(self) -> set[str]:
        return set(self._held)

    def press(self, key: str) -> None:
        if key in self._held:
            return
        self._backend.key_down(key)
        self._held.append(key)
        log.debug("[ACT] key_down %s", key)

    def release(self, key: str) -> None:
        if key not in self._held:
            return
        self._backend.key_up(key)
        self._held.remove(key)
        log.debug("[ACT] key_up %s", key)

    def release_all(self) -> None:
        if self._held:
            log.info("[ACT] 释放全部按键: %s", ",".join(self._held))
        while self._held:
            key = self._held.pop()
            self._backend.key_up(key)

    @contextmanager
    def held(self) -> Iterator["ActionExecutor"]:
        try:
            yield self
        finally:
            self.release_all()

    def execute(self, cmd: Command) -> None:
        if not self._foreground_ok():
            # 游戏不在前台：一律不发输入（用户可能在用其它软件）
            return
        kind = cmd.kind
        if kind == "idle":
            return
        if kind == "press_w":
            self._press_named("forward")
        elif kind == "release_w":
            self._release_named("forward")
        elif kind == "jump":
            self._tap_named("jump")
        elif kind == "prone":
            self.release_all()
            self._tap_named("prone")
        elif kind == "stand":
            self.release_all()
            self._tap_named("stand")
        elif kind == "open_map":
            self._tap_named("map")
        elif kind == "close_map":
            self._tap_named("map")
        elif kind == "turn":
            self._turn(int(cmd.arg or 0))
        elif kind == "mark":
            x, y = cmd.arg  # type: ignore[misc]
            jx, jy = self._human.mark_jitter(int(x), int(y))
            self._click_frame(jx, jy, button="right", what="mark")
        elif kind == "click_start":
            self._click_roi("lobby_button")
        elif kind == "click_return":
            self._click_roi("return_button")
        elif kind == "scan":
            step = int(self._cfg.anchors.value("scan_step_px"))
            for dx in self._human.scan_steps(step):
                self._backend.move_rel(dx, 0)
                self._human.segment_pause()
        else:
            log.warning("[ACT] 未知命令 %r -> 已忽略", kind)

    # ---- 键位 ----

    def _key(self, name: str) -> str | None:
        key = self._cfg.policy.key(name)
        if key is None and name not in self._warned_keys:
            self._warned_keys.add(name)
            log.warning("[ERR] actions: keys.%s = null -> 该动作已停用", name)
        return key

    def _press_named(self, name: str) -> None:
        key = self._key(name)
        if key:
            self.press(key)

    def _release_named(self, name: str) -> None:
        key = self._key(name)
        if key:
            self.release(key)

    def _tap_named(self, name: str) -> None:
        key = self._key(name)
        if key:
            self._tap(key)

    def _tap(self, key: str) -> None:
        self._human.reaction()
        self._backend.key_down(key)
        try:
            self._human.key_hold()
        finally:
            self._backend.key_up(key)
        self._human.inter_action()

    def _turn(self, dx: int) -> None:
        for step_x, step_y in self._human.turn_plan(dx):
            self._backend.move_rel(step_x, step_y)
            self._human.segment_pause()

    def _move_to(self, x: int, y: int) -> None:
        """相对位移分两段靠近目标，避免一步瞬移。"""
        cx, cy = self._backend.cursor_pos()
        dx, dy = int(x) - cx, int(y) - cy
        if dx == 0 and dy == 0:
            return
        ratio = self._human.split_ratio()
        first_x, first_y = int(round(dx * ratio)), int(round(dy * ratio))
        self._backend.move_rel(first_x, first_y)
        self._human.segment_pause()
        self._backend.move_rel(dx - first_x, dy - first_y)
        self._human.segment_pause()

    def _click(self, x: int, y: int, button: str) -> None:
        self._human.reaction()
        self._move_to(x, y)
        self._backend.mouse_down(button)
        try:
            self._human.key_hold()
        finally:
            self._backend.mouse_up(button)
        self._human.inter_action()

    def _click_roi(self, roi_name: str) -> None:
        try:
            rect = self._cfg.anchors.roi(roi_name)
        except Exception as exc:
            log.error("[ERR] actions: 缺少 ROI %s (%s) -> 请先在 config 中配置该按钮区域", roi_name, exc)
            return
        x, y = self._human.click_point(rect)
        self._click_frame(x, y, button="left", what=roi_name)

    # ---- 坐标 ----

    def _client_rect(self) -> Rect:
        """客户区在屏幕上的绝对矩形；未注入时按 (0,0) 起点处理（单测/观察器）。"""
        if self._origin_provider is not None:
            return self._origin_provider()
        w, h = self._cfg.anchors.resolution
        return Rect(0, 0, int(w), int(h))

    def _to_screen(self, x: int, y: int, what: str) -> tuple[int, int] | None:
        """帧坐标（客户区左上为原点）-> 屏幕绝对坐标。越界一律拒绝点击。"""
        rect = self._client_rect()
        if not (0 <= x < rect.w and 0 <= y < rect.h):
            log.warning(
                "[ACT] %s 落点 (%d,%d) 超出客户区 %dx%d -> 已跳过",
                what,
                x,
                y,
                rect.w,
                rect.h,
            )
            return None
        return rect.x + x, rect.y + y

    def _click_frame(self, x: int, y: int, button: str, what: str) -> None:
        point = self._to_screen(int(x), int(y), what)
        if point is None:
            return
        self._click(point[0], point[1], button)
