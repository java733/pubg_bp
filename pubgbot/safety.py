"""安全层：全局热键、看门狗、会话上限、前台校验、退出释放。

设计原则：所有"要停下来"的路径都先释放按键，再抛 SafetyStop，
由主循环统一转成正常退出码。
"""

from __future__ import annotations

import logging
from typing import Callable

from .config import AppConfig

log = logging.getLogger(__name__)


class SafetyStop(Exception):
    """需要立即停止脚本（已释放按键）。"""


class Safety:
    def __init__(
        self,
        cfg: AppConfig,
        release: Callable[[], None],
        clock: Callable[[], int],
        foreground_ok: Callable[[], bool],
    ) -> None:
        self._cfg = cfg
        self._release = release
        self._clock = clock
        self._foreground_ok = foreground_ok
        self._started_ms: int | None = None
        self._last_heartbeat: int | None = None
        self._stop = False
        self._stop_reason = ""
        self._released = False
        self._armed = False
        self._hotkeys: list[object] = []

    # ---- 生命周期 ----

    def install_hotkeys(self) -> None:
        """F10 开始（已开始则忽略）、F12 急停。失败不致命，降级为无热键运行。"""
        try:
            import keyboard

            self._hotkeys.append(keyboard.add_hotkey("f10", self._on_start))
            self._hotkeys.append(keyboard.add_hotkey("f12", self._on_stop))
            log.info("[SAFE] 热键已注册: F10=开始/重置心跳, F12=急停")
        except Exception as exc:
            log.error(
                "[ERR] safety: 全局热键注册失败: %s -> 请以管理员身份运行；否则只能用 Ctrl+C 停止", exc
            )

    def start(self) -> None:
        now = self._clock()
        self._started_ms = now
        self._last_heartbeat = now
        log.info("[SAFE] 会话开始 t=%dms", now)

    def arm(self) -> None:
        """F10：进入执行状态（未 arm 前动作层丢弃所有命令）。"""
        if self._armed:
            return
        self._armed = True
        if self._started_ms is None:
            self.start()
        log.info("[SAFE] 已进入执行状态（F10）")

    def armed(self) -> bool:
        return self._armed

    def heartbeat(self) -> None:
        self._last_heartbeat = self._clock()

    def stop_requested(self) -> bool:
        return self._stop

    def request_stop(self, reason: str) -> None:
        if self._stop:
            return
        self._stop = True
        self._stop_reason = reason
        log.warning("[SAFE] 收到停止请求: %s -> 立即释放全部按键", reason)
        self._release_once()

    def shutdown(self) -> None:
        self._release_once()
        try:
            import keyboard

            keyboard.unhook_all_hotkeys()
        except Exception:
            pass

    # ---- 每帧检查 ----

    def check(self) -> None:
        now = self._clock()
        if self._stop:
            self._release_once()
            raise SafetyStop(f"stop requested: {self._stop_reason}")

        if not self._foreground_ok():
            self._release_once()
            raise SafetyStop("foreground lost -> 游戏不在前台（切出/锁屏），已停止且未发送任何输入")

        started = self._started_ms if self._started_ms is not None else now
        session_limit = self._cfg.policy.value("session_limit_ms")
        if now - started >= session_limit:
            self._release_once()
            raise SafetyStop(f"session limit reached ({session_limit:.0f}ms) -> 正常收工")

        watchdog = self._cfg.policy.value("watchdog_ms")
        if self._last_heartbeat is not None and now - self._last_heartbeat >= watchdog:
            self._release_once()
            raise SafetyStop(
                f"watchdog timeout ({now - self._last_heartbeat}ms > {watchdog:.0f}ms)"
                " -> 主循环可能卡死，已强制释放按键"
            )

    # ---- 内部 ----

    def _release_once(self) -> None:
        if self._released:
            return
        self._released = True
        try:
            self._release()
        except Exception as exc:  # 释放本身失败也不能吞掉原因
            log.error("[ERR] safety: 释放按键失败: %s -> 请手动按一次 W/S/A/D 确认没有卡键", exc)

    def _on_start(self) -> None:
        log.info("[SAFE] F10 按下")
        self.arm()

    def _on_stop(self) -> None:
        self.request_stop("F12")
