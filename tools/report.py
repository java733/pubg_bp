"""日志统计与抽样核对。

用法：
    python tools/report.py logs/run.jsonl --samples 20
人工核对抽样出来的帧号，估算识别准确率（设计文档第 3 节的验收方式）。
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def summarize(rows: list[dict]) -> dict:
    total = len(rows)
    counts: dict[str, int] = {}
    alerts = 0
    stops = 0
    for row in rows:
        state = row.get("state")
        if state:
            counts[state] = counts.get(state, 0) + 1
        event = row.get("event")
        if event == "alert":
            alerts += 1
        elif event == "stop":
            stops += 1
    unknown = counts.get("UNKNOWN", 0)
    return {
        "frames": total,
        "state_counts": counts,
        "unknown_ratio": round(unknown / total, 4) if total else 0.0,
        "alerts": alerts,
        "stops": stops,
    }


def sample_points(rows: list[dict], n: int, seed: int = 0) -> list[dict]:
    """随机抽 n 条记录（不超过总条数），便于人工核对识别是否正确。"""
    if n <= 0 or not rows:
        return []
    rnd = random.Random(seed)
    return rnd.sample(rows, min(n, len(rows)))


def load_rows(path: Path) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                print(f"[WARN] 第 {line_no} 行不是合法 JSON，已跳过")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="PUBG 挂机日志统计")
    parser.add_argument("log", help="JSONL 日志路径")
    parser.add_argument("--samples", type=int, default=0, help="额外抽多少条供人工核对")
    args = parser.parse_args()

    rows = load_rows(Path(args.log))
    if not rows:
        print(f"[ERR] report: {args.log} 里没有可用记录")
        return 1
    print(json.dumps(summarize(rows), ensure_ascii=False, indent=2))
    for row in sample_points(rows, args.samples):
        print(json.dumps(row, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
