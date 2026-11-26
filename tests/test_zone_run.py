import json
import numpy as np
import cv2
import pytest
from pubgbot.config import load_config
from pubgbot.fsm import Command, WorldView
from pubgbot.vision.circle import Circle, nearest_edge_point
from pubgbot.vision.state import Observation, VisualState
from pubgbot.world import HealthTracker
from pubgbot.fsm import BotFsm


@pytest.fixture
def cfg(tmp_path):
    rois = {"health": [0, 0, 500, 20], "compass": [150, 0, 100, 10], "map": [0, 0, 200, 200]}
    a = {"version": 1, "resolution": [64, 48], "calibrated": True, "rois": rois, "anchors": {},
         "hsv": {"health_white": [[0, 0, 200], [180, 30, 255]], "waypoint_yellow": [[18, 90, 120], [38, 255, 255]],
                 "circle_white": [[0, 0, 200], [180, 40, 255]]},
         "values": {"circle_inner_factor": 0.9}}
    p = {"values": {"jump_delay_ms": 20000, "start_click_cooldown_ms": 5000,
                    "remark_interval_ms": 20000, "marking_timeout_ms": 1500,
                    "marker_lost_scan_ms": 2000, "scan_max_ms": 3000,
                    "stuck_detect_ms": 10000, "mark_fail_limit": 2, "run_fail_limit": 3,
                    "zone_margin_factor": 0.08, "dead_zone_px": 6, "steer_step_px": 8,
                    "health_drop_window_ms": 3000, "health_drop_ratio": 0.05}}
    (tmp_path / "a.json").write_text(json.dumps(a))
    (tmp_path / "p.json").write_text(json.dumps(p))
    return load_config(tmp_path / "a.json", tmp_path / "p.json")


def health_frame():
    f = np.zeros((20, 500, 3), np.uint8)
    f[:, :100] = (255, 255, 255)
    return f


def drop_frame():
    f = np.zeros((20, 500, 3), np.uint8)
    f[:, :50] = (255, 255, 255)
    return f


def obs(state=VisualState.GROUNDED):
    return Observation(state, {}, 1.0)


def test_health_drop_detected_once(cfg):
    h = HealthTracker(cfg)
    h.update(health_frame(), 0)
    h.update(health_frame(), 1000)
    assert h.update(drop_frame(), 1500) is True
    h.update(drop_frame(), 1600)
    assert h.update(health_frame(), 1700) is False


def test_mark_failures_lead_to_prone(cfg):
    f = BotFsm(cfg)
    outside = WorldView(marker_x=None, compass_center_x=50, dist_ratio=1.5, health_drop_fast=False)
    for base in (0, 5000):
        assert f.tick(obs(), outside, base).kind == "open_map"
        assert f.tick(obs(VisualState.MARKING), outside, base + 100).kind == "idle"
        assert f.tick(obs(VisualState.MARKING), outside, base + 1700).kind == "close_map"
    assert f.tick(obs(), outside, 10000).kind == "prone"
    assert f.mode == "prone"


def test_remark_after_interval(cfg):
    f = BotFsm(cfg)
    outside = WorldView(marker_x=None, compass_center_x=50, dist_ratio=1.5, health_drop_fast=False)
    assert f.tick(obs(), outside, 0).kind == "open_map"


def test_zone_run_turns_and_stops(cfg):
    f = BotFsm(cfg)
    outside = WorldView(marker_x=None, compass_center_x=50, dist_ratio=1.2, health_drop_fast=False)
    f.tick(obs(), outside, 0)
    marked = WorldView(marker_x=50, compass_center_x=50, dist_ratio=1.2, health_drop_fast=False,
                       mark_point=(10, 10))
    f.tick(obs(VisualState.MARKING), marked, 100)
    f.tick(obs(VisualState.MARKING), marked, 200)
    f.tick(obs(), outside, 300)
    # 黄标持续对准罗盘中心、但距离始终不变 -> 应被判定卡住并最终趴下，且绝不重开地图
    for now in range(300, 60000, 100):
        cmd = f.tick(obs(), marked, now)
        assert cmd.kind not in ("open_map", "close_map", "mark", "jump", "click_start", "click_return")
    assert f.mode == "prone"


def test_nearest_edge_uses_configured_factor(cfg):
    assert nearest_edge_point((20, 100), Circle(100, 100, 60), cfg.anchors.value("circle_inner_factor")) == (46, 100)
