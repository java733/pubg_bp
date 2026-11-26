import json
import pytest
from pubgbot.config import load_config
from pubgbot.fsm import BotFsm, Command, WorldView
from pubgbot.vision.state import Observation, VisualState


@pytest.fixture
def cfg(tmp_path):
    a = {"version": 1, "resolution": [64, 48], "calibrated": True, "rois": {}, "anchors": {}, "hsv": {}, "values": {}}
    p = {"values": {"jump_delay_ms": 20000, "start_click_cooldown_ms": 5000,
                    "remark_interval_ms": 20000, "marking_timeout_ms": 1500,
                    "marker_lost_scan_ms": 2000, "scan_max_ms": 3000,
                    "stuck_detect_ms": 10000, "mark_fail_limit": 2, "run_fail_limit": 3,
                    "zone_margin_factor": 0.08, "dead_zone_px": 6, "steer_step_px": 8}}
    (tmp_path / "a.json").write_text(json.dumps(a))
    (tmp_path / "p.json").write_text(json.dumps(p))
    return load_config(tmp_path / "a.json", tmp_path / "p.json")


def obs(state, now, **kw):
    return Observation(state, {}, 1.0)


W = WorldView(marker_x=50, compass_center_x=50, dist_ratio=1.5, health_drop_fast=False)


def test_unknown_emits_idle(cfg):
    assert BotFsm(cfg).tick(obs(VisualState.UNKNOWN, 0), W, 0).kind == "idle"


def test_lobby_clicks_start_with_cooldown(cfg):
    f = BotFsm(cfg)
    assert f.tick(obs(VisualState.LOBBY, 0), W, 0).kind == "click_start"
    assert f.tick(obs(VisualState.LOBBY, 0), W, 1000).kind == "idle"
    assert f.tick(obs(VisualState.LOBBY, 0), W, 5000).kind == "click_start"


def test_plane_jumps_after_delay(cfg):
    f = BotFsm(cfg)
    f.tick(obs(VisualState.PLANE, 0), W, 0)
    assert f.tick(obs(VisualState.PLANE, 0), W, 19999).kind == "idle"
    assert f.tick(obs(VisualState.PLANE, 0), W, 20000).kind == "jump"


def test_grounded_outside_zone_starts_marking(cfg):
    f = BotFsm(cfg)
    assert f.tick(obs(VisualState.GROUNDED, 0), W, 0).kind == "open_map"
    assert f.mode == "marking"


def test_grounded_inside_zone_prones(cfg):
    w = WorldView(marker_x=None, compass_center_x=50, dist_ratio=0.5, health_drop_fast=False)
    assert BotFsm(cfg).tick(obs(VisualState.GROUNDED, 0), w, 0).kind == "prone"


def test_marking_marks_then_closes(cfg):
    f = BotFsm(cfg)
    f.tick(obs(VisualState.GROUNDED, 0), W, 0)
    marked = WorldView(marker_x=50, compass_center_x=50, dist_ratio=1.5,
                       health_drop_fast=False, mark_point=(120, 340))
    cmd = f.tick(obs(VisualState.MARKING, 0), marked, 100)
    assert cmd.kind == "mark" and cmd.arg == (120, 340)
    assert f.tick(obs(VisualState.MARKING, 0), marked, 200).kind == "close_map"


def test_running_steers_toward_marker(cfg):
    f = BotFsm(cfg)
    f.tick(obs(VisualState.GROUNDED, 0), W, 0)
    f.tick(obs(VisualState.MARKING, 0), W, 100)
    f.tick(obs(VisualState.MARKING, 0), W, 200)
    f.tick(obs(VisualState.GROUNDED, 0), W, 300)          # 关图后回地面
    left = WorldView(marker_x=20, compass_center_x=50, dist_ratio=1.2, health_drop_fast=False)
    assert f.tick(obs(VisualState.GROUNDED, 0), left, 400).kind == "turn"
    assert f.tick(obs(VisualState.GROUNDED, 0), left, 500).kind == "turn"
    centered = WorldView(marker_x=52, compass_center_x=50, dist_ratio=1.2, health_drop_fast=False)
    assert f.tick(obs(VisualState.GROUNDED, 0), centered, 600).kind == "press_w"


def test_marker_lost_triggers_scan(cfg):
    f = BotFsm(cfg)
    f.tick(obs(VisualState.GROUNDED, 0), W, 0)
    f.tick(obs(VisualState.MARKING, 0), W, 100)
    f.tick(obs(VisualState.MARKING, 0), W, 200)
    lost = WorldView(marker_x=None, compass_center_x=50, dist_ratio=1.2, health_drop_fast=False)
    f.tick(obs(VisualState.GROUNDED, 0), lost, 300)
    assert f.tick(obs(VisualState.GROUNDED, 0), lost, 2400).kind == "scan"


def test_health_drop_releases_and_prones(cfg):
    f = BotFsm(cfg)
    hurt = WorldView(marker_x=50, compass_center_x=50, dist_ratio=1.2, health_drop_fast=True)
    assert f.tick(obs(VisualState.GROUNDED, 0), hurt, 0).kind == "release_w"


def test_map_state_not_recognized_does_not_spam_m(cfg):
    f = BotFsm(cfg)
    assert f.tick(obs(VisualState.GROUNDED, 0), W, 0).kind == "open_map"
    for now in range(100, 1400, 100):
        assert f.tick(obs(VisualState.GROUNDED, 0), W, now).kind == "idle"
    assert f.tick(obs(VisualState.GROUNDED, 0), W, 1500).kind == "idle"
    assert f.tick(obs(VisualState.GROUNDED, 0), W, 1600).kind == "open_map"
