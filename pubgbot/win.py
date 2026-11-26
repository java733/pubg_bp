"""窗口定位与坐标。

只在真正运行时调用；导入本模块不会触碰任何窗口。
所有坐标均为「屏幕绝对坐标」（DPI 感知后的物理像素）。
"""

from __future__ import annotations

import ctypes
import logging
from pathlib import Path

import win32api
import win32con
import win32gui
import win32process

from .config import Rect

log = logging.getLogger(__name__)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class WindowNotFound(Exception):
    """找不到游戏窗口。"""


class CaptureRegionError(Exception):
    """客户区与显示器无交集，无法截屏。"""


def enable_dpi_awareness() -> None:
    """进程级 DPI 感知，必须在任何窗口坐标操作之前调用。

    不设置时，125%/150% 缩放下 GetClientRect 返回的是逻辑像素，
    截屏与 ROI 会整体错位。
    """
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        log.info("[DPI] SetProcessDpiAwareness(2) 成功")
    except Exception as exc:  # pragma: no cover - 取决于 Windows 版本
        log.warning("[DPI] SetProcessDpiAwareness 失败: %s -> 回退 SetProcessDPIAware", exc)
        try:
            ctypes.windll.user32.SetProcessDPIAware()
            log.info("[DPI] SetProcessDPIAware 成功")
        except Exception as exc2:
            log.error("[ERR] win: 无法启用 DPI 感知: %s -> 坐标系可能整体偏移，请手动设置显示器缩放为 100%%", exc2)


def is_admin() -> bool:
    """当前进程是否管理员。游戏以管理员运行时脚本必须同权限。"""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _process_name(pid: int) -> str | None:
    """取进程可执行文件名；被反作弊保护时可能返回 None。"""
    handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_ulong(1024)
        ok = ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
        if not ok:
            return None
        return Path(buf.value).name
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def list_visible_windows() -> list[tuple[int, str, str]]:
    """列出当前可见的顶层窗口 (hwnd, title, class)。用于找不到游戏时排查。"""
    result: list[tuple[int, str, str]] = []

    def _cb(hwnd: int, _extra) -> bool:
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title.strip():
                result.append((hwnd, title, win32gui.GetClassName(hwnd)))
        return True

    win32gui.EnumWindows(_cb, None)
    return result


def find_game_window(
    process_name: str = "TslGame.exe",
    class_name: str = "UnrealWindow",
) -> int:
    """按「进程名 + 窗口类名」查找游戏窗口，不依赖窗口标题。

    BattlEye 可能拒绝查询进程路径，此时仅按类名匹配并打 WARNING。
    """
    fallback: int | None = None
    found: int | None = None

    def _cb(hwnd: int, _extra) -> bool:
        nonlocal fallback, found
        if not win32gui.IsWindowVisible(hwnd):
            return True
        if win32gui.GetClassName(hwnd) != class_name:
            return True
        _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
        name = _process_name(pid)
        if name is None:
            fallback = hwnd
            return True
        if name.lower() == process_name.lower():
            found = hwnd
            return False  # 回调返回 False 即停止枚举
        return True

    win32gui.EnumWindows(_cb, None)
    if found:
        log.info("[WIN] 找到游戏窗口 hwnd=0x%X (%s / %s)", found, process_name, class_name)
        return found
    if fallback and win32gui.IsWindow(fallback):
        log.warning(
            "[WIN] 无法读取进程名（可能被反作弊保护），退化为仅按窗口类名匹配: hwnd=0x%X title=%r",
            fallback,
            win32gui.GetWindowText(fallback),
        )
        return fallback
    visible = list_visible_windows()
    hint = "\n".join(f"  hwnd=0x{h:X} class={c!r} title={t!r}" for h, t, c in visible[:20]) or "  (无可见窗口)"
    raise WindowNotFound(
        "找不到 PUBG 窗口 (TslGame.exe / UnrealWindow) -> 请确认游戏已启动并处于窗口化状态。"
        f"当前可见窗口:\n{hint}"
    )


def get_client_rect(hwnd: int) -> Rect:
    """客户区在屏幕上的绝对矩形（不含标题栏与边框）。"""
    if not win32gui.IsWindow(hwnd):
        raise WindowNotFound(f"窗口句柄已失效: 0x{hwnd:X} -> 游戏可能已关闭或最小化到托盘")
    _l, _t, w, h = win32gui.GetClientRect(hwnd)
    left, top = win32gui.ClientToScreen(hwnd, (0, 0))
    return Rect(left, top, w, h)


def monitor_rect_for(hwnd: int) -> Rect:
    """窗口所在显示器的物理矩形。"""
    monitor = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
    info = win32api.GetMonitorInfo(monitor)
    left, top, right, bottom = info["Monitor"]
    return Rect(left, top, right - left, bottom - top)


def compute_capture_region(client: Rect, monitor: Rect) -> Rect:
    """客户区与显示器求交。完全在屏幕外时抛 CaptureRegionError。"""
    clipped = client.intersect(monitor)
    if clipped is None:
        raise CaptureRegionError(
            f"客户区 {client} 与显示器 {monitor} 无交集 -> 请把游戏窗口移回主显示器内"
        )
    if clipped != client:
        log.warning("[WIN] 客户区部分在屏幕外，已裁剪: client=%s -> captured=%s", client, clipped)
    return clipped


def is_foreground(hwnd: int) -> bool:
    """游戏是否前台。不前台时一律不发送输入。"""
    fg = win32gui.GetForegroundWindow()
    return fg != 0 and fg == hwnd


def window_alive(hwnd: int) -> bool:
    return bool(win32gui.IsWindow(hwnd)) and bool(win32gui.IsWindowVisible(hwnd))
