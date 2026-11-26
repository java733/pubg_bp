"""M0：截屏可行性验证。

在游戏机上以管理员身份运行（仓库根目录）：
    python tools/probe_capture.py

产物：samples/probe_dxcam.png、samples/probe_mss.png
用图片查看器打开，确认是游戏大厅画面而不是纯黑、锁屏或桌面。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from pubgbot import capture, win

    win.enable_dpi_awareness()
    try:
        saved = capture.probe(ROOT / "samples")
    except Exception as exc:
        print(f"[ERR] probe_capture: {exc} -> 先跑 python tools/doctor.py 定位环境问题")
        return 1
    if not saved:
        print("[ERR] probe_capture: 两种后端都没有成功截图 -> 参见设计文档第 7 节风险表")
        return 1
    print("已保存:")
    for path in saved:
        print(f"  {path}")
    print("请打开图片确认：是游戏画面、无黑边、无桌面混入。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
