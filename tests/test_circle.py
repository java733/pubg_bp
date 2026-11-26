import cv2
import numpy as np
from pubgbot.config import Rect
from pubgbot.vision.circle import Circle, clamp_to, detect_circle, nearest_edge_point

LO = np.array([0, 0, 200], np.uint8)
HI = np.array([180, 40, 255], np.uint8)


def ring_frame():
    f = np.zeros((200, 200, 3), np.uint8)
    cv2.circle(f, (100, 100), 60, (255, 255, 255), 3)
    return f


def test_detects_ring_center_and_radius():
    c = detect_circle(ring_frame(), Rect(0, 0, 200, 200), LO, HI, 20, 120)
    assert c is not None
    assert abs(c.cx - 100) <= 3 and abs(c.cy - 100) <= 3
    assert abs(c.r - 60) <= 4


def test_radius_out_of_bounds_returns_none():
    assert detect_circle(ring_frame(), Rect(0, 0, 200, 200), LO, HI, 80, 120) is None


def test_nearest_edge_point_is_inside_towards_player():
    p = nearest_edge_point((20.0, 100.0), Circle(100, 100, 60), 0.9)
    assert p == (46, 100)


def test_clamp_to_rect():
    assert clamp_to(Rect(0, 0, 200, 200), (-5, 250)) == (0, 199)
