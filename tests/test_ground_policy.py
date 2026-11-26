import json
from pathlib import Path
import pytest
from pubgbot.config import load_config
from pubgbot.fsm import BotFsm, WorldView
from pubgbot.vision.state import Observation, VisualState


@pytest.fixture
def cfg(tmp_path):
    a = {"version": 1, "resolution": [64, 48], "calibrated": True, "rois": {}, "anchors": {}, "hsv": {}, "values": {}}
    p = {"values": {"jump_delay_ms": 20000, "start_click_cooldown_ms": 5000,
                    "remark_interval_ms": 20000, "marking_timeout_ms": 1500,
                    "map_reopen_cooldown_ms": 2000,
                    "marker_lost_scan_ms": 2000, "scan_max_ms": 3000,
                    "stuck_detect_ms": 10000, "mark_fail_limit": 2, "run_fail_limit": 3,
                    "zone_margin_factor": 0.08, "dead_zone_px": 6, "steer_step_px": 8,
                    "downed_crawl_ms": 2000,
                    "freefall_timeout_ms": 180000}}
    (tmp_path / "a.json").write_text(json.dumps(a))
    (tmp_path / "p.json").write_text(json.dumps(p))
    return load_config(tmp_path / "a.json", tmp_path / "p.json")


W = WorldView(marker_x=None, compass_center_x=50, dist_ratio=None, health_drop_fast=False)


def test_landing_without_mark_forces_map_once(cfg):
    f = BotFsm(cfg)
    assert f.tick(Observation(VisualState.GROUNDED, {}, 1.0), W, 0).kind == "open_map"
    # 识别器没进 MARKING：超时后补关图，冷却期内不再按 M
    assert f.tick(Observation(VisualState.GROUNDED, {}, 1.0), W, 1500).kind == "close_map"
    for now in (1600, 2000, 3400):
        assert f.tick(Observation(VisualState.GROUNDED, {}, 1.0), W, now).kind == "idle"
    assert f.tick(Observation(VisualState.GROUNDED, {}, 1.0), W, 3500).kind == "open_map"


def test_freefall_presses_w(cfg):
    assert BotFsm(cfg).tick(Observation(VisualState.FREEFALL, {}, 1.0), W, 0).kind == "press_w"


def test_freefall_timeout_releases(cfg):
    f = BotFsm(cfg)
    assert f.tick(Observation(VisualState.FREEFALL, {}, 1.0), W, 0).kind == "press_w"
    assert f.tick(Observation(VisualState.FREEFALL, {}, 1.0), W, 180000).kind == "release_w"


def test_downed_crawls_then_prone_once(cfg):
    f = BotFsm(cfg)
    assert f.tick(Observation(VisualState.DOWNED, {}, 1.0), W, 0).kind == "press_w"
    assert f.tick(Observation(VisualState.DOWNED, {}, 1.0), W, 2001).kind == "prone"
    # Z 是 toggle：之后不能再重复按
    assert f.tick(Observation(VisualState.DOWNED, {}, 1.0), W, 2100).kind in ("idle", "release_w")


def test_result_clicks_return_with_cooldown(cfg):
    f = BotFsm(cfg)
    assert f.tick(Observation(VisualState.RESULT, {}, 1.0), W, 0).kind == "click_return"
    assert f.tick(Observation(VisualState.RESULT, {}, 1.0), W, 1000).kind == "idle"
    assert f.tick(Observation(VisualState.RESULT, {}, 1.0), W, 5000).kind == "click_return"
