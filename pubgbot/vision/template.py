"""模板匹配锚点。

对纹理丰富的模板用 TM_CCOEFF_NORMED；对近乎纯色的模板（方差≈0，
CCOEFF 会因为除零而处处返回 1.0）自动改用 TM_SQDIFF_NORMED。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from ..config import AnchorSpec, Rect

log = logging.getLogger(__name__)

_FLAT_STD = 1.0


@dataclass(frozen=True)
class Hit:
    score: float
    hit: bool
    loc: tuple[int, int]


def _crop(frame: np.ndarray, roi: Rect) -> tuple[np.ndarray, int, int] | None:
    h, w = frame.shape[:2]
    clipped = roi.intersect(Rect(0, 0, w, h))
    if clipped is None:
        return None
    return frame[clipped.y:clipped.bottom, clipped.x:clipped.right], clipped.x, clipped.y


def _gray(img: np.ndarray) -> np.ndarray:
    return img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def match(frame: np.ndarray, roi: Rect, template: np.ndarray, threshold: float = 0.85) -> Hit:
    """在 roi 内找 template。越界/模板比 ROI 大一律按未命中处理。"""
    cropped = _crop(frame, roi)
    if cropped is None:
        return Hit(0.0, False, (0, 0))
    sub, ox, oy = cropped
    tpl_gray = _gray(template)
    th, tw = tpl_gray.shape[:2]
    if sub.shape[0] < th or sub.shape[1] < tw:
        return Hit(0.0, False, (0, 0))

    gray = _gray(sub)
    if float(tpl_gray.std()) < _FLAT_STD:
        result = cv2.matchTemplate(gray, tpl_gray, cv2.TM_SQDIFF_NORMED)
        min_val, _max_val, min_loc, _max_loc = cv2.minMaxLoc(result)
        score = 1.0 - float(min_val)
        loc = min_loc
    else:
        result = cv2.matchTemplate(gray, tpl_gray, cv2.TM_CCOEFF_NORMED)
        _min_val, max_val, _min_loc, max_loc = cv2.minMaxLoc(result)
        score = float(max_val)
        loc = max_loc
    return Hit(score=score, hit=score >= threshold, loc=(ox + loc[0], oy + loc[1]))


def match_anchor(frame: np.ndarray, spec: AnchorSpec, templates_dir: Path) -> Hit:
    """按配置里的模板文件匹配一个锚点。模板缺失时抛 FileNotFoundError。"""
    if spec.template is None:
        raise FileNotFoundError(f"锚点 {spec.name} 未配置 template -> 请先运行 tools/pick_roi.py 标定")
    path = Path(spec.template)
    path = path if path.is_absolute() else templates_dir / path
    if not path.exists():
        raise FileNotFoundError(
            f"模板不存在: {path} -> 请确认 templates/ 下存在该文件（pick_roi.py 会生成）"
        )
    template = cv2.imread(str(path))
    if template is None:
        raise FileNotFoundError(f"模板读取失败（文件损坏或不是图片）: {path}")
    return match(frame, spec.roi, template, spec.threshold)
