import numpy as np
from pubgbot.config import Rect
from pubgbot.vision.compass import find_marker_x, steer_pixels

LO = np.array([18, 90, 120], np.uint8)
HI = np.array([38, 255, 255], np.uint8)


def frame_with_marker(x0, w=4):
    f = np.zeros((10, 100, 3), np.uint8)
    f[2:8, x0:x0 + w] = (0, 255, 255)
    return f


def test_finds_marker_center():
    assert find_marker_x(frame_with_marker(70), Rect(0, 0, 100, 10), LO, HI) == 71


def test_no_marker_returns_none():
    assert find_marker_x(np.zeros((10, 100, 3), np.uint8), Rect(0, 0, 100, 10), LO, HI) is None


def test_steer_right_when_marker_right():
    assert steer_pixels(71, 50, 6, 8) == 8


def test_steer_left_clamped():
    assert steer_pixels(20, 50, 6, 8) == -8


def test_dead_zone_returns_zero():
    assert steer_pixels(53, 50, 6, 8) == 0


def test_missing_marker_returns_zero():
    assert steer_pixels(None, 50, 6, 8) == 0
