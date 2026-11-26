"""环境自检。

开发机（无游戏）也能跑：依赖与配置应 PASS，窗口查找 FAIL 并给出明确建议。
退出码恒为 0 —— 这是诊断工具，不应该因为环境不满足就报错退出。

用法（管理员 PowerShell，仓库根目录）：
    python tools/doctor.py
"""

from __future__ import annotations

import logging
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

log = logging.getLogger("doctor")

REQUIRED_MODULES = ["numpy", "cv2", "dxcam", "mss", "win32gui", "keyboard", "pydirectinput"]


def render_report(checks: list[dict]) -> str:
    """把检查项渲染成可读文本。每项含 name/ok/detail/advice。"""
    lines: list[str] = []
    for check in checks:
        tag = "PASS" if check.get("ok") else "FAIL"
        line = f"[{tag}] {check.get('name', '?')}: {check.get('detail', '')}"
        if not check.get("ok") and check.get("advice"):
            line += f" -> 建议: {check['advice']}"
        lines.append(line)
    passed = sum(1 for c in checks if c.get("ok"))
    lines.append(f"--- {passed}/{len(checks)} PASS ---")
    return "\n".join(lines)


def _check_python() -> dict:
    ver = platform.python_version()
    bits = platform.architecture()[0]
    ok = sys.version_info[:2] >= (3, 12) and bits == "64bit"
    return {
        "name": "Python",
        "ok": ok,
        "detail": f"{ver} {bits} ({sys.executable})",
        "advice": "需要 Python 3.12 64 位；32 位解释器无法加载 dxcam/pywin32 的 64 位模块",
    }


def _check_admin() -> dict:
    from pubgbot.win import is_admin

    ok = is_admin()
    return {
        "name": "管理员权限",
        "ok": ok,
        "detail": "是" if ok else "否",
        "advice": "请用「以管理员身份运行」打开 PowerShell，否则全局热键与 SendInput 会被 UIPI 静默拦截",
    }


def _check_dpi() -> dict:
    from pubgbot.win import enable_dpi_awareness

    try:
        enable_dpi_awareness()
        return {"name": "DPI 感知", "ok": True, "detail": "已启用 per-monitor v2 (或回退)"}
    except Exception as exc:
        return {
            "name": "DPI 感知",
            "ok": False,
            "detail": str(exc),
            "advice": "把 Windows 显示缩放设为 100%，或手动确认进程 DPI 感知",
        }


def _check_monitors() -> dict:
    try:
        import win32api

        monitors = win32api.EnumDisplayMonitors()
        items = []
        for i, (handle, _hdc, info) in enumerate(monitors):
            l, t, r, b = info["Monitor"]
            items.append(f"{i}: {r - l}x{b - t} @ ({l},{t}){'(主)' if info['Flags'] == 1 else ''}")
        return {"name": "显示器", "ok": bool(items), "detail": "; ".join(items) or "无"}
    except Exception as exc:
        return {
            "name": "显示器",
            "ok": False,
            "detail": str(exc),
            "advice": "确认显卡驱动正常、使用本地会话（远程桌面下 DXGI 捕获通常不可用）",
        }


def _check_deps() -> dict:
    missing: list[str] = []
    for name in REQUIRED_MODULES:
        try:
            __import__(name)
        except Exception as exc:
            missing.append(f"{name}({exc.__class__.__name__})")
    return {
        "name": "依赖包",
        "ok": not missing,
        "detail": "全部可导入" if not missing else "缺失: " + ", ".join(missing),
        "advice": "在仓库根目录执行 pip install -e .",
    }


def _check_config() -> dict:
    from pubgbot.config import load_config

    cfg_dir = ROOT / "config"
    try:
        cfg = load_config(cfg_dir / "anchors.1920x1080.json", cfg_dir / "policy.json")
    except Exception as exc:
        return {
            "name": "配置加载",
            "ok": False,
            "detail": str(exc),
            "advice": "检查 config/*.json 是否存在且为合法 JSON",
        }
    detail = (
        f"resolution={cfg.anchors.resolution[0]}x{cfg.anchors.resolution[1]} "
        f"rois={len(cfg.anchors.rois)} anchors={len(cfg.anchors.anchors)} "
        f"calibrated={cfg.anchors.calibrated}"
    )
    if not cfg.anchors.calibrated:
        detail += "  <== 尚未标定，识别会持续告警"
    return {
        "name": "配置加载",
        "ok": True,
        "detail": detail,
        "advice": "用 python tools/pick_roi.py 完成标定后把 calibrated 改为 true",
    }


def _check_window() -> dict:
    from pubgbot import win

    try:
        hwnd = win.find_game_window()
    except Exception as exc:
        return {
            "name": "游戏窗口",
            "ok": False,
            "detail": str(exc),
            "advice": "请确认 PUBG 已启动、处于窗口化、且未被最小化",
        }
    try:
        rect = win.get_client_rect(hwnd)
        fg = win.is_foreground(hwnd)
    except Exception as exc:
        return {"name": "游戏窗口", "ok": False, "detail": str(exc), "advice": "重启游戏或检查窗口是否被隐藏"}
    return {
        "name": "游戏窗口",
        "ok": True,
        "detail": f"hwnd=0x{hwnd:X} 客户区={rect.w}x{rect.h} @({rect.x},{rect.y}) 前台={fg}",
        "advice": "",
    }


def _check_capture() -> dict:
    from pubgbot import capture, win

    try:
        hwnd = win.find_game_window()
        client = win.get_client_rect(hwnd)
        region = win.compute_capture_region(client, win.monitor_rect_for(hwnd))
    except Exception as exc:
        return {
            "name": "试截一帧",
            "ok": False,
            "detail": f"无法定位窗口: {exc}",
            "advice": "先让「游戏窗口」一项 PASS",
        }

    parts: list[str] = []
    any_ok = False
    for name in ("dxcam", "mss"):
        try:
            source = (
                capture.DxcamSource(lambda: region)
                if name == "dxcam"
                else capture.MssSource(lambda: region)
            )
            frame = source.grab()
            mean = float(frame.mean())
            ok = mean >= 8.0
            any_ok = any_ok or ok
            h, w = frame.shape[:2]
            parts.append(f"{name}: {w}x{h} mean={mean:.1f}{'' if ok else '(黑帧)'}")
        except Exception as exc:
            parts.append(f"{name}: 失败({exc})")
    return {
        "name": "试截一帧",
        "ok": any_ok,
        "detail": "; ".join(parts),
        "advice": "两个后端都黑帧时，先退出全屏独占/关闭 HDR，再重试；仍失败请把本行输出反馈",
    }


def collect_checks() -> list[dict]:
    checks: list[dict] = []
    for fn in (
        _check_python,
        _check_admin,
        _check_dpi,
        _check_monitors,
        _check_deps,
        _check_config,
        _check_window,
        _check_capture,
    ):
        try:
            checks.append(fn())
        except Exception as exc:  # 单项崩溃不影响其它项
            checks.append(
                {
                    "name": fn.__name__,
                    "ok": False,
                    "detail": f"{exc.__class__.__name__}: {exc}",
                    "advice": "该项检查自身异常，请把完整输出反馈",
                }
            )
    return checks


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    checks = collect_checks()
    print(render_report(checks))
    logging.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
