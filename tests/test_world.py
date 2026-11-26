import json

import cv2
import numpy as np
import pytest

from pubgbot.config import load_config
from pubgbot.vision.state import VisualState
from pubgbot.world import HealthTracker, WorldBuilder


@pytest.fixture
def cfg(tmp_path):
    rois = {
        "health": [0, 380, 100, 20],
        "compass": [150, 0, 100, 10],
        "map": [0, 0, 400, 400],
        "minimap": [250, 250, 120, 120],
    }
    a = {
        "version": 1,
        "resolution": [400, 400],
        "calibrated": True,
        "rois": rois,
        "anchors": {},
        "hsv": {
            "health_white": [[0, 0, 200], [180, 30, 255]],
            "waypoint_yellow": [[18, 90, 120], [38, 255, 255]],
            "circle_white": [[0, 0, 200], [180, 40, 255]],
        },
        "values": {
            "circle_inner_factor": 0.9,
            "marker_min_pixels": 1,
            "map_circle_r_min": 40,
            "map_circle_r_max": 160,
            "minimap_circle_r_min": 8,
            "minimap_circle_r_max": 50,
        },
    }
    p = {
        "values": {
            "health_drop_window_ms": 3000,
            "health_drop_ratio": 0.05,
            "dist_stale_ms": 10000,
        }
    }
    (tmp_path / "a.json").write_text(json.dumps(a))
    (tmp_path / "p.json").write_text(json.dumps(p))
    return load_config(tmp_path / "a.json", tmp_path / "p.json")


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now


def blank():
    return np.zeros((400, 400, 3), np.uint8)


def map_frame():
    frame = blank()
    cv2.circle(frame, (260, 200), 80, (255, 255, 255), 3)
    return frame


def minimap_frame():
    frame = blank()
    # 小地图中心 (310,310)，圈心偏移 30px、半径 20 -> dist_ratio≈1.5
    cv2.circle(frame, (340, 310), 20, (255, 255, 255), 2)
    return frame


def builder(cfg, clock):
    return WorldBuilder(cfg, HealthTracker(cfg), clock=clock)


def test_map_circle_sets_scale_invariant_distance(cfg):
    clock = Clock()
    w = builder(cfg, clock).build(map_frame(), VisualState.MARKING)
    assert w.dist_ratio == pytest.approx(60 / 80, abs=0.08)
    assert w.mark_point is not None


def test_invisible_minimap_circle_keeps_map_distance(cfg):
    clock = Clock()
    b = builder(cfg, clock)
    first = b.build(map_frame(), VisualState.MARKING)
    assert first.dist_ratio is not None
    # 小地图看不到圈：地图来源的距离不能被抹掉
    clock.now = 500
    kept = b.build(blank(), VisualState.GROUNDED)
    assert kept.dist_ratio == pytest.approx(first.dist_ratio, abs=1e-6)


def test_visible_minimap_refines_distance(cfg):
    clock = Clock()
    b = builder(cfg, clock)
    b.build(map_frame(), VisualState.MARKING)
    clock.now = 500
    refined = b.build(minimap_frame(), VisualState.GROUNDED)
    assert refined.dist_ratio == pytest.approx(30 / 20, abs=0.2)


def test_minimap_detection_failure_invalidates_its_own_distance(cfg):
    clock = Clock()
    b = builder(cfg, clock)
    b.build(minimap_frame(), VisualState.GROUNDED)
    clock.now = 500
    assert b.build(blank(), VisualState.GROUNDED).dist_ratio is None


def test_map_distance_expires(cfg):
    clock = Clock()
    b = builder(cfg, clock)
    b.build(map_frame(), VisualState.MARKING)
    clock.now = 10001
    assert b.build(blank(), VisualState.GROUNDED).dist_ratio is None


def test_map_detection_failure_invalidates_mark(cfg):
    clock = Clock()
    b = builder(cfg, clock)
    assert b.build(map_frame(), VisualState.MARKING).mark_point is not None
    clock.now = 100
    failed = b.build(blank(), VisualState.MARKING)
    assert failed.mark_point is None
    assert failed.dist_ratio is None


def test_new_match_resets_mark_and_distance(cfg):
    clock = Clock()
    b = builder(cfg, clock)
    b.build(map_frame(), VisualState.MARKING)
    reset = b.build(blank(), VisualState.LOBBY)
    assert reset.dist_ratio is None
    assert reset.mark_point is None
