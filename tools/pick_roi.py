"""ROI 标定工具（在游戏机上运行）。

用法：
    python tools/pick_roi.py --list                 # 查看所有锚点及标定状态
    python tools/pick_roi.py --name lobby_start     # 框选并保存该锚点的模板

流程：截一帧当前客户区 -> 弹出窗口让你框选 -> 保存到 templates/1920x1080/<name>.png
-> 回写 config/anchors.1920x1080.json 的 roi/template 字段。
全部锚点标完后，把该文件里的 calibrated 改成 true。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pubgbot.config import AppConfig, Rect, load_config  # noqa: E402

ANCHORS_JSON = ROOT / "config" / "anchors.1920x1080.json"
POLICY_JSON = ROOT / "config" / "policy.json"
TEMPLATES_DIR = ROOT / "templates" / "1920x1080"

log = logging.getLogger("pick_roi")


def crop_and_save(frame, roi: Rect, out_png: Path) -> Path:
    """裁剪 roi 并写盘，返回写出的路径。越界部分会被自动裁掉。"""
    h, w = frame.shape[:2]
    clipped = roi.intersect(Rect(0, 0, w, h))
    if clipped is None:
        raise ValueError(f"ROI {roi} 完全在画面之外 (画面 {w}x{h})")
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_png), frame[clipped.y:clipped.bottom, clipped.x:clipped.right])
    return out_png


def next_anchor_name(cfg: AppConfig) -> str:
    """第一个还没配模板的锚点名；都配好了就给个占位名。"""
    for name, spec in cfg.anchors.anchors.items():
        if spec.template is None:
            return name
    return f"anchor_{len(cfg.anchors.anchors) + 1}"


def anchor_status(cfg: AppConfig, templates_dir: Path) -> list[str]:
    lines = []
    for name, spec in cfg.anchors.anchors.items():
        if spec.template is None:
            lines.append(f"{name:<20} 未配置模板 (state={spec.state}) roi={spec.roi}")
            continue
        path = Path(spec.template)
        path = path if path.is_absolute() else templates_dir / path
        state = "OK" if path.exists() else "缺文件"
        lines.append(f"{name:<20} {state:<6} template={spec.template} roi={spec.roi}")
    return lines


def _grab_one_frame():
    from pubgbot import capture, win

    hwnd = win.find_game_window()
    client = win.get_client_rect(hwnd)
    region = win.compute_capture_region(client, win.monitor_rect_for(hwnd))
    source = capture.open_frame_source(lambda: region)
    return source.grab()


def _write_back(name: str, roi: Rect, png: Path) -> None:
    raw = json.loads(ANCHORS_JSON.read_text(encoding="utf-8"))
    entry = raw.setdefault("anchors", {}).setdefault(
        name, {"roi": "full", "template": None, "threshold": 0.85}
    )
    entry["template"] = png.name
    entry["roi"] = "full"
    raw.setdefault("rois", {})[f"roi_{name}"] = [roi.x, roi.y, roi.w, roi.h]
    entry["roi"] = f"roi_{name}"
    ANCHORS_JSON.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("[PICK] 已回写 %s: roi=%s template=%s", name, roi, png.name)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="PUBG 锚点 ROI 标定")
    parser.add_argument("--list", action="store_true", help="只打印标定状态")
    parser.add_argument("--name", default=None, help="要标定的锚点名（默认取第一个未标定的）")
    args = parser.parse_args()

    cfg = load_config(ANCHORS_JSON, POLICY_JSON)
    if args.list:
        print("\n".join(anchor_status(cfg, TEMPLATES_DIR)))
        return 0

    from pubgbot import win

    win.enable_dpi_awareness()
    try:
        frame = _grab_one_frame()
    except Exception as exc:
        print(f"[ERR] pick_roi: 截图失败: {exc} -> 先跑 python tools/doctor.py")
        return 1

    name = args.name or next_anchor_name(cfg)
    print(f"即将标定锚点: {name}")
    print("请在弹出窗口里框选该元素（Enter 确认，c 取消无选区）")
    x, y, w, h = cv2.selectROI(f"pick_roi: {name}", frame, showCrosshair=True)
    cv2.destroyAllWindows()
    if w == 0 or h == 0:
        print("[ERR] pick_roi: 没有选择区域 -> 已取消")
        return 1

    roi = Rect(int(x), int(y), int(w), int(h))
    png = crop_and_save(frame, roi, TEMPLATES_DIR / f"{name}.png")
    _write_back(name, roi, png)
    print(f"已保存模板: {png}  ROI={roi}")
    print("提示：所有锚点标完后，把 config/anchors.1920x1080.json 的 calibrated 改为 true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
