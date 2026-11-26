import numpy as np
from pubgbot.config import Rect
from pubgbot.vision.color import color_ratio

LO = np.array([18, 90, 120], np.uint8)
HI = np.array([38, 255, 255], np.uint8)


def test_ratio_of_half_yellow():
    f = np.zeros((20, 20, 3), np.uint8)
    f[:, :10] = (0, 255, 255)  # BGR 黄
    assert color_ratio(f, Rect(0, 0, 20, 20), LO, HI) == 0.5


def test_ratio_zero_when_none():
    assert color_ratio(np.zeros((20, 20, 3), np.uint8), Rect(0, 0, 20, 20), LO, HI) == 0.0
