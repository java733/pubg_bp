"""离线回放：对保存下来的帧目录重新跑识别，不碰游戏、不发输入。

用法：
    python tools/replay.py samples/frames
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pubgbot.config import load_config  # noqa: E402
from pubgbot.vision.state import StateRecognizer  # noqa: E402


def replay(frames_dir: Path, recognizer: StateRecognizer, step_ms: int = 100) -> list[dict]:
    """按文件名顺序回放 png，返回每帧的观测记录。"""
    rows: list[dict] = []
    files = sorted(Path(frames_dir).glob("*.png"))
    for index, path in enumerate(files):
        frame = cv2.imread(str(path))
        if frame is None:
            print(f"[WARN] 读取失败，跳过: {path}")
            continue
        obs = recognizer.observe(frame, index * step_ms)
        rows.append(
            {
                "file": path.name,
                "state": obs.state.value,
                "confidence": round(obs.confidence, 4),
                "scores": obs.scores,
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="离线回放识别")
    parser.add_argument("frames_dir", help="帧目录（*.png）")
    args = parser.parse_args()

    cfg = load_config(ROOT / "config" / "anchors.1920x1080.json", ROOT / "config" / "policy.json")
    recognizer = StateRecognizer(cfg, ROOT / "templates" / "1920x1080")
    rows = replay(Path(args.frames_dir), recognizer)
    if not rows:
        print("[ERR] replay: 目录里没有可读的 png")
        return 1
    for row in rows:
        print(json.dumps(row, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
