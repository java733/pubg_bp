import numpy as np
from pubgbot.config import AnchorSpec, Rect
from pubgbot.vision.template import match, match_anchor
from pathlib import Path
import cv2


def make_frame():
    f = np.full((48, 64, 3), 30, np.uint8)
    f[10:20, 20:32] = 200  # 12x10 亮块
    return f


def test_match_finds_template():
    tmpl = np.full((10, 12, 3), 200, np.uint8)
    hit = match(make_frame(), Rect(0, 0, 64, 48), tmpl, 0.85)
    assert hit.hit is True
    assert abs(hit.loc[0] - 20) <= 1 and abs(hit.loc[1] - 10) <= 1


def test_match_misses_when_absent():
    tmpl = np.full((10, 12, 3), 200, np.uint8)
    hit = match(make_frame(), Rect(40, 30, 24, 18), tmpl, 0.85)
    assert hit.hit is False
    assert hit.score < 0.85


def test_match_anchor_reads_template_file(tmp_path):
    cv2.imwrite(str(tmp_path / "t.png"), np.full((10, 12, 3), 200, np.uint8))
    spec = AnchorSpec("t", Rect(0, 0, 64, 48), Path("t.png"), 0.85)
    assert match_anchor(make_frame(), spec, tmp_path).hit is True


def test_roi_out_of_frame_returns_miss():
    tmpl = np.full((10, 12, 3), 200, np.uint8)
    assert match(make_frame(), Rect(60, 44, 20, 10), tmpl, 0.85).hit is False
