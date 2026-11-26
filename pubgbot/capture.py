"""截屏帧源。

dxcam（DXGI Desktop Duplication）优先，mss（GDI）回退。
输出统一为 BGR uint8 ndarray，尺寸应与配置中的客户区一致。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Protocol

import numpy as np

from .config import Rect

log = logging.getLogger(__name__)


class FrameInvalid(Exception):
    """该帧不可用于识别（黑帧 / 尺寸不符 / 为空）。"""


def to_bgr(frame: np.ndarray, source: str) -> np.ndarray:
    """把各后端的原始格式转成 BGR。

    dxcam: RGB(A) -> BGR
    mss:   BGRA    -> BGR
    """
    if frame is None or frame.ndim != 3 or frame.shape[2] < 3:
        raise FrameInvalid(f"无法转换帧: shape={getattr(frame, 'shape', None)} source={source}")
    if source == "dxcam":
        return np.ascontiguousarray(frame[..., :3][..., ::-1])
    if source == "mss":
        return np.ascontiguousarray(frame[..., :3])
    raise ValueError(f"未知捕获后端: {source}")


class FrameGate:
    """帧门禁：黑帧、错尺寸、空帧一律拒绝，不进入识别与决策。"""

    def __init__(self, expect: tuple[int, int], min_mean: float = 8.0) -> None:
        self.expect = (int(expect[0]), int(expect[1]))  # (w, h)
        self.min_mean = float(min_mean)

    def validate(self, frame: np.ndarray) -> None:
        if frame is None:
            raise FrameInvalid("帧为空 (None) -> 捕获超时；请确认游戏窗口未被最小化")
        if frame.ndim != 3 or frame.shape[2] != 3:
            raise FrameInvalid(f"shape 期望 (h, w, 3) BGR, 实际 {getattr(frame, 'shape', None)}")
        h, w = frame.shape[0], frame.shape[1]
        if (w, h) != self.expect:
            raise FrameInvalid(
                f"shape mismatch: expected {self.expect[0]}x{self.expect[1]}, got {w}x{h}"
                " -> 客户区尺寸与 config 的 resolution 不一致；请用 1920x1080 窗口化，且不要缩放窗口"
            )
        mean = float(frame.mean())
        if mean < self.min_mean:
            raise FrameInvalid(
                f"black frame: mean={mean:.2f} < {self.min_mean}"
                " -> 窗口可能被最小化/锁屏/独占全屏，或当前捕获后端拿不到 flip-model 交换链"
            )


class FrameSource(Protocol):
    def grab(self) -> np.ndarray: ...


class DxcamSource:
    """DXGI Desktop Duplication。首帧或偶发 None 时沿用上一帧。"""

    def __init__(self, region_provider: Callable[[], Rect]) -> None:
        import dxcam

        self._region_provider = region_provider
        self._camera = dxcam.create(output_idx=0, output_color="RGB")
        if self._camera is None:
            raise RuntimeError("dxcam.create 返回 None（可能是虚拟机/远程桌面/无独立输出）")
        self._last: np.ndarray | None = None

    def grab(self) -> np.ndarray:
        region = self._region_provider()
        raw = self._camera.grab(region=(region.x, region.y, region.right, region.bottom))
        if raw is None:
            if self._last is None:
                raise FrameInvalid("dxcam 首帧为 None -> 请稍等一帧或在游戏画面变动后重试")
            return self._last
        self._last = to_bgr(raw, "dxcam")
        return self._last


class MssSource:
    """GDI 截屏。flip-model 窗口可能返回黑帧，由 FrameGate 兜底。"""

    def __init__(self, region_provider: Callable[[], Rect]) -> None:
        import mss

        self._region_provider = region_provider
        self._sct = mss.mss()

    def grab(self) -> np.ndarray:
        region = self._region_provider()
        shot = self._sct.grab(
            {"left": region.x, "top": region.y, "width": region.w, "height": region.h}
        )
        arr = np.frombuffer(shot.raw, dtype=np.uint8).reshape(shot.height, shot.width, 4)
        return to_bgr(arr, "mss")


def open_frame_source(
    region_provider: Callable[[], Rect], prefer: str = "dxcam"
) -> FrameSource:
    """按优先级尝试后端；全部失败抛 RuntimeError，消息里带每个后端的原因。"""
    order = ["dxcam", "mss"] if prefer == "dxcam" else ["mss", "dxcam"]
    errors: list[str] = []
    for name in order:
        try:
            source: FrameSource = (
                DxcamSource(region_provider) if name == "dxcam" else MssSource(region_provider)
            )
            log.info("[CAP] 捕获后端 = %s", name)
            return source
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            log.warning("[CAP] %s 初始化失败: %s -> 尝试下一个后端", name, exc)
    raise RuntimeError(
        "所有捕获后端初始化失败: " + "; ".join(errors) + " -> 请确认显示器已连接且未运行在远程桌面会话中"
    )


def probe(out_dir: Path) -> list[Path]:
    """M0 验证：dxcam 与 mss 各截一张存盘，返回成功写出的 png 路径。"""
    import cv2

    from . import win

    out_dir.mkdir(parents=True, exist_ok=True)
    hwnd = win.find_game_window()
    client = win.get_client_rect(hwnd)
    monitor = win.monitor_rect_for(hwnd)
    region = win.compute_capture_region(client, monitor)
    log.info("[M0] hwnd=0x%X client=%s monitor=%s capture=%s", hwnd, client, monitor, region)

    saved: list[Path] = []
    for name in ("dxcam", "mss"):
        try:
            source = DxcamSource(lambda: region) if name == "dxcam" else MssSource(lambda: region)
            frame = source.grab()
            path = out_dir / f"probe_{name}.png"
            cv2.imwrite(str(path), frame)
            mean = float(frame.mean())
            log.info(
                "[M0] %s: shape=%s mean=%.2f -> %s%s",
                name,
                frame.shape,
                mean,
                path,
                "" if mean >= 8.0 else "  <== 黑帧！该后端不可用",
            )
            saved.append(path)
        except Exception as exc:
            log.error("[ERR] capture: %s 截图失败: %s -> 换另一个后端或在游戏机重跑 tools/doctor.py", name, exc)
    return saved
