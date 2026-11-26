"""主循环与命令行入口。

数据流：capture -> vision -> fsm -> action，safety 每帧横切检查。
观察器模式（--observe）把动作层替换为空实现：只识别、不发送任何输入。
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Callable

from .actions import ActionExecutor, PyDirectBackend
from .capture import FrameGate, FrameInvalid, open_frame_source
from .config import AppConfig, ConfigError, Rect, load_config
from .fsm import BotFsm, WorldView
from .jsonlog import JsonlLogger
from .safety import Safety, SafetyStop
from .vision.state import Observation, StateRecognizer, VisualState

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger(__name__)

DEFAULT_ANCHORS = ROOT / "config" / "anchors.1920x1080.json"
DEFAULT_POLICY = ROOT / "config" / "policy.json"
TEMPLATES_DIR = ROOT / "templates" / "1920x1080"


class ResolutionMismatch(Exception):
    """客户区尺寸与配置不一致。"""


def validate_resolution(client: Rect, cfg: AppConfig) -> None:
    """尺寸不符必须拒绝启动，绝不做缩放适配（会整体错位）。"""
    exp_w, exp_h = cfg.anchors.resolution
    if (client.w, client.h) != (exp_w, exp_h):
        raise ResolutionMismatch(
            f"resolution mismatch: expected {exp_w}x{exp_h}, got {client.w}x{client.h}"
            " -> 请把游戏设为窗口化 1920x1080 且不要缩放窗口；改了分辨率就必须重新标定 ROI"
        )


class NullExecutor:
    """观察器模式：什么都不做。"""

    def execute(self, cmd) -> None:
        return None

    def release_all(self) -> None:
        return None


def summarize_run(rows: list[dict]) -> dict:
    """统计一次运行的帧数、状态分布、告警与停止原因。"""
    states: dict[str, int] = {}
    alerts: list[dict] = []
    stops: list[str] = []
    for row in rows:
        state = row.get("state")
        if state:
            states[state] = states.get(state, 0) + 1
        event = row.get("event")
        if event == "alert":
            alerts.append({"state": row.get("state"), "kind": row.get("kind"), "ts_ms": row.get("ts_ms")})
        elif event == "stop":
            stops.append(str(row.get("reason", "")))
    return {"frames": len(rows), "states": states, "alerts": alerts, "stops": stops}


def alerts_for(state: str, elapsed_ms: int, cfg: AppConfig) -> list[str]:
    """持续 N 毫秒仍未识别/仍在队列时应告警的条目。"""
    kinds: list[str] = []
    if state == VisualState.UNKNOWN.value:
        if elapsed_ms >= cfg.policy.value("unknown_alert_ms"):
            kinds.append("unknown_too_long")
    elif state == VisualState.QUEUE.value:
        if elapsed_ms >= cfg.policy.value("queue_alert_ms"):
            kinds.append("queue_too_long")
    return kinds


def run(
    source_factory: Callable[[], object],
    gate: Callable[[object], None],
    recognizer: object,
    fsm: object,
    executor: object,
    safety: Safety,
    logger: JsonlLogger,
    clock: Callable[[], int],
    max_frames: int | None = None,
    interval_ms: int = 0,
    world_builder: Callable[[object, VisualState], WorldView] | None = None,
    cfg: AppConfig | None = None,
) -> dict:
    """主循环。返回 {"frames", "states", "stops"}，便于测试断言。"""
    frames = 0
    states: dict[str, int] = {}
    stops: list[str] = []
    alerted: set[tuple[str, str]] = set()
    last_state: str | None = None
    state_since = 0

    try:
        while max_frames is None or frames < max_frames:
            safety.check()

            frame = source_factory()
            now = clock()
            try:
                gate(frame)
            except FrameInvalid as exc:
                log.warning("[LOOP] 丢弃无效帧: %s", exc)
                logger.write({"event": "invalid_frame", "ts_ms": now, "reason": str(exc)})
                safety.heartbeat()
                continue

            obs: Observation = recognizer.observe(frame, now)
            world = (
                world_builder(frame, obs.state)
                if world_builder is not None
                else WorldView(None, 0, None, False)
            )
            cmd = fsm.tick(obs, world, now)
            executor.execute(cmd)

            state_name = obs.state.value
            states[state_name] = states.get(state_name, 0) + 1
            if state_name != last_state:
                last_state = state_name
                state_since = now

            if cfg is not None:
                for kind in alerts_for(state_name, now - state_since, cfg):
                    key = (state_name, kind)
                    if key in alerted:
                        continue
                    alerted.add(key)
                    log.warning("[ALERT] %s 持续 %dms -> %s", state_name, now - state_since, kind)
                    logger.write(
                        {"event": "alert", "kind": kind, "state": state_name, "ts_ms": now}
                    )

            logger.write(
                {
                    "frame": frames,
                    "ts_ms": now,
                    "state": state_name,
                    "confidence": round(float(obs.confidence), 4),
                    "scores": obs.scores,
                    "world": {
                        "marker_x": world.marker_x,
                        "dist_ratio": world.dist_ratio,
                        "mark_point": world.mark_point,
                    },
                    "mode": getattr(fsm, "mode", ""),
                    "command": cmd.kind,
                    "arg": cmd.arg,
                    "reason": cmd.reason,
                }
            )
            frames += 1
            safety.heartbeat()

            if interval_ms > 0:
                _sleep_to_fill(now, interval_ms)
    except SafetyStop as exc:
        log.warning("[LOOP] 安全停止: %s", exc)
        stops.append(str(exc))
        logger.write({"event": "stop", "ts_ms": clock(), "reason": str(exc)})
    except KeyboardInterrupt:
        log.warning("[LOOP] 收到 Ctrl+C")
        stops.append("KeyboardInterrupt")
        logger.write({"event": "stop", "ts_ms": clock(), "reason": "KeyboardInterrupt"})
    finally:
        executor.release_all()
        logger.write({"event": "shutdown", "ts_ms": clock(), "frames": frames})

    return {"frames": frames, "states": states, "stops": stops}


def _sleep_to_fill(started_ms: int, interval_ms: int) -> None:
    elapsed = time.monotonic() * 1000 - started_ms
    remaining = interval_ms - elapsed
    if remaining > 0:
        time.sleep(remaining / 1000.0)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pubgbot",
        description="PUBG PC 纯视觉挂机脚本（默认只识别不操作，加 --live 才真正发送键鼠）",
    )
    parser.add_argument("--observe", action="store_true", default=True,
                        help="观察器模式：只识别、不发送输入（默认）")
    parser.add_argument("--live", dest="observe", action="store_false",
                        help="实操作模式：真正发送键鼠输入")
    parser.add_argument("--anchors", default=str(DEFAULT_ANCHORS), help="锚点配置文件")
    parser.add_argument("--policy", default=str(DEFAULT_POLICY), help="策略配置文件")
    parser.add_argument("--log", default=str(ROOT / "logs" / "run.jsonl"), help="JSONL 日志路径")
    parser.add_argument("--max-frames", type=int, default=None, help="只跑 N 帧后退出（自检用）")
    parser.add_argument("--prefer", default="dxcam", choices=["dxcam", "mss"], help="优先捕获后端")
    return parser.parse_args(argv)


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _setup_logging()
    from . import win

    try:
        cfg = load_config(Path(args.anchors), Path(args.policy))
    except ConfigError as exc:
        print(f"[ERR] main: {exc}")
        return 2

    win.enable_dpi_awareness()
    if not win.is_admin():
        print(
            "[ERR] main: 当前不是管理员 -> 请用「以管理员身份运行」重新打开终端。"
            "否则 F10/F12 热键和 SendInput 会被系统静默拦截。"
        )
        return 2

    try:
        hwnd = win.find_game_window()
    except Exception as exc:
        print(f"[ERR] main: {exc}")
        return 1

    try:
        client = win.get_client_rect(hwnd)
        validate_resolution(client, cfg)
    except ResolutionMismatch as exc:
        print(f"[ERR] main: {exc}")
        return 3
    except Exception as exc:
        print(f"[ERR] main: 读取客户区失败: {exc} -> 确认游戏窗口未被最小化")
        return 1

    log.info("[MAIN] 客户区 %dx%d @ (%d,%d)", client.w, client.h, client.x, client.y)
    log.info("[MAIN] 模式 = %s", "OBSERVE(只识别)" if args.observe else "LIVE(会操作游戏)")

    def region_provider() -> Rect:
        return win.compute_capture_region(win.get_client_rect(hwnd), win.monitor_rect_for(hwnd))

    try:
        source = open_frame_source(region_provider, prefer=args.prefer)
    except Exception as exc:
        print(f"[ERR] main: {exc}")
        return 1

    gate = FrameGate(cfg.anchors.resolution, cfg.policy.value("min_frame_mean"))
    recognizer = StateRecognizer(cfg, TEMPLATES_DIR)
    fsm = BotFsm(cfg)
    backend = NullExecutor() if args.observe else PyDirectBackend()
    executor = (
        NullExecutor()
        if args.observe
        else ActionExecutor(backend, cfg, lambda: win.is_foreground(hwnd))
    )
    logger = JsonlLogger(Path(args.log))
    clock = lambda: int(time.monotonic() * 1000)

    safety = Safety(cfg, executor.release_all, clock, lambda: win.is_foreground(hwnd))
    safety.install_hotkeys()
    safety.start()

    log.info("[MAIN] 开始循环：F12 急停，Ctrl+C 退出，日志 %s", args.log)
    result = run(
        source.grab,
        gate.validate,
        recognizer,
        fsm,
        executor,
        safety,
        logger,
        clock,
        max_frames=args.max_frames,
        interval_ms=cfg.policy.int_value("frame_interval_ms"),
        cfg=cfg,
    )
    logger.close()
    safety.shutdown()
    log.info(
        "[MAIN] 结束：frames=%d states=%s stops=%s",
        result["frames"],
        result["states"],
        result["stops"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
