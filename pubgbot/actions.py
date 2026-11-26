"""动作层：把语义命令翻译成键鼠原语。

硬性要求：任何按下的键都必须成对释放。所有按住状态记录在 self._held，
release_all() 是唯一的兜底出口，异常/F12/看门狗路径都会调用它。
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Callable, Iterator, Protocol

from .config import AppConfig, Rect
from .fsm import Command

log = logging.getLogger(__name__)


class InputBackend(Protocol):
    def key_down(self, key: str) -> None: ...
    def key_up(self, key: str) -> None: ...
    def move_rel(self, dx: int, dy: int) -> None: ...
    def click_right_at(self, x: int, y: int) -> None: ...
    def click_left_at(self, x: int, y: int) -> None: ...


class PyDirectBackend:
    """基于 pydirectinput 的真实键鼠后端（scancode 级）。

    pydirectinput 默认每次调用后 sleep(PAUSE=0.1) 且按键名是字符；
    这里关掉内置暂停，节奏由主循环控制。
    """

    def __init__(self) -> None:
        import pydirectinput

        self._pdi = pydirectinput
        pydirectinput.PAUSE = 0.0
        pydirectinput.FAILSAFE = True

    def key_down(self, key: str) -> None:
        self._pdi.keyDown(key)

    def key_up(self, key: str) -> None:
        self._pdi.keyUp(key)

    def move_rel(self, dx: int, dy: int) -> None:
        self._pdi.moveRel(int(dx), int(dy), relative=True)

    def click_right_at(self, x: int, y: int) -> None:
        self._pdi.moveTo(int(x), int(y))
        self._pdi.mouseDown(button="right")
        self._pdi.mouseUp(button="right")

    def click_left_at(self, x: int, y: int) -> None:
        self._pdi.moveTo(int(x), int(y))
        self._pdi.mouseDown(button="left")
        self._pdi.mouseUp(button="left")


def center_of(rect: Rect) -> tuple[int, int]:
    return rect.x + rect.w // 2, rect.y + rect.h // 2


class ActionExecutor:
    def __init__(
        self,
        backend: InputBackend,
        cfg: AppConfig,
        foreground_ok: Callable[[], bool],
    ) -> None:
        self._backend = backend
        self._cfg = cfg
        self._foreground_ok = foreground_ok
        self._held: list[str] = []

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
            self.press("w")
        elif kind == "release_w":
            self.release("w")
        elif kind == "jump":
            self._tap("f")
        elif kind == "prone":
            self.release_all()
            self._tap("c")
        elif kind == "open_map":
            self._tap("m")
        elif kind == "close_map":
            self._tap("m")
        elif kind == "turn":
            self._backend.move_rel(int(cmd.arg or 0), 0)
        elif kind == "mark":
            x, y = cmd.arg  # type: ignore[misc]
            self._backend.click_right_at(int(x), int(y))
        elif kind == "click_start":
            self._click_roi("lobby_button")
        elif kind == "click_return":
            self._click_roi("return_button")
        elif kind == "scan":
            step = int(self._cfg.anchors.value("scan_step_px"))
            for _ in range(3):
                self._backend.move_rel(step, 0)
        else:
            log.warning("[ACT] 未知命令 %r -> 已忽略", kind)

    def _tap(self, key: str) -> None:
        self._backend.key_down(key)
        self._backend.key_up(key)

    def _click_roi(self, roi_name: str) -> None:
        try:
            rect = self._cfg.anchors.roi(roi_name)
        except Exception as exc:
            log.error("[ERR] actions: 缺少 ROI %s (%s) -> 请先在 config 中配置该按钮区域", roi_name, exc)
            return
        x, y = center_of(rect)
        self._backend.click_left_at(x, y)
