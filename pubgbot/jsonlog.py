"""JSONL 日志：每帧一条，便于事后回放与人工核对。"""

from __future__ import annotations

import json
from pathlib import Path


class JsonlLogger:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a", encoding="utf-8")

    def write(self, record: dict) -> None:
        self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()

    def __enter__(self) -> "JsonlLogger":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
