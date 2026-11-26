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
                    "map_reopen_cooldown_ms": 2000,
                    "marker_lost_scan_ms": 2000, "scan_max_ms": 3000,
                    "stuck_detect_ms": 10000, "mark_fail_limit": 2, "run_fail_limit": 3,
                    "zone_margin_factor": 0.08, "dead_zone_px": 6, "steer_step_px": 8}}
    (tmp_path / "a.json").write_text(json.dumps(a))
    (tmp_path / "p.json").write_text(json.dumps(p))
    return load_config(tmp_path / "a.json", tmp_path / "p.json")


def obs(state, now=0, **kw):
    return Observation(state, {}, 1.0)


W = WorldView(marker_x=50, compass_center_x=50, dist_ratio=1.5, health_drop_fast=False)
MARKED = WorldView(marker_x=50, compass_center_x=50, dist_ratio=1.5,
                   health_drop_fast=False, mark_point=(120, 340))
INSIDE = WorldView(marker_x=None, compass_center_x=50, dist_ratio=0.5, health_drop_fast=False)


def start_match(f, world=W, base=0):
    """落地 -> 开图 -> 标点 -> 关图 -> 回到地面（进入 running）。"""
    assert f.tick(obs(VisualState.GROUNDED, base), world, base).kind == "open_map"
    assert f.tick(obs(VisualState.MARKING, base + 100), MARKED, base + 100).kind == "mark"
    assert f.tick(obs(VisualState.MARKING, base + 200), MARKED, base + 200).kind == "close_map"
    assert f.tick(obs(VisualState.GROUNDED, base + 300), world, base + 300).kind in (
        "press_w",
        "turn",
        "idle",
    )


def test_command_momentary_classification():
    assert Command("prone").momentary
    assert Command("close_map").momentary
    assert not Command("press_w").momentary
    assert not Command("turn").momentary


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


def test_landing_always_marks_even_inside_zone(cfg):
    # 没标点时即使小地图显示已在圈内，也要开图标一次
    f = BotFsm(cfg)
    assert f.tick(obs(VisualState.GROUNDED, 0), INSIDE, 0).kind == "open_map"


def test_grounded_inside_zone_prones_after_mark(cfg):
    f = BotFsm(cfg)
    start_match(f)
    assert f.tick(obs(VisualState.GROUNDED, 0), INSIDE, 400).kind == "prone"
    assert f.posture == "prone"
    # 姿态键是 toggle：趴下后不能每帧重复按
    for now in (500, 1000, 19000):
        assert f.tick(obs(VisualState.GROUNDED, 0), INSIDE, now).kind in ("idle", "release_w")


def test_marking_marks_then_closes_once(cfg):
    f = BotFsm(cfg)
    f.tick(obs(VisualState.GROUNDED, 0), W, 0)
    cmd = f.tick(obs(VisualState.MARKING, 0), MARKED, 100)
    assert cmd.kind == "mark" and cmd.arg == (120, 340)
    assert f.tick(obs(VisualState.MARKING, 0), MARKED, 200).kind == "close_map"
    # 识别器还没切走时只能等待，绝不能重按 M
    assert f.tick(obs(VisualState.MARKING, 0), MARKED, 300).kind == "idle"
    assert f.tick(obs(VisualState.MARKING, 0), MARKED, 1500).kind == "idle"


def test_running_steers_toward_marker(cfg):
    f = BotFsm(cfg)
    start_match(f)
    left = WorldView(marker_x=20, compass_center_x=50, dist_ratio=1.2, health_drop_fast=False)
    assert f.tick(obs(VisualState.GROUNDED, 0), left, 400).kind == "turn"
    assert f.tick(obs(VisualState.GROUNDED, 0), left, 500).kind == "turn"
    centered = WorldView(marker_x=52, compass_center_x=50, dist_ratio=1.2, health_drop_fast=False)
    assert f.tick(obs(VisualState.GROUNDED, 0), centered, 600).kind == "press_w"


def test_running_stands_up_after_prone(cfg):
    f = BotFsm(cfg)
    start_match(f)
    assert f.tick(obs(VisualState.GROUNDED, 0), INSIDE, 400).kind == "prone"
    # 20s 后重新标点，再回到地面时角色仍是趴姿 -> 必须先起身
    assert f.tick(obs(VisualState.GROUNDED, 0), INSIDE, 20400).kind == "open_map"
    assert f.tick(obs(VisualState.MARKING, 0), MARKED, 20500).kind == "mark"
    assert f.tick(obs(VisualState.MARKING, 0), MARKED, 20600).kind == "close_map"
    assert f.tick(obs(VisualState.GROUNDED, 0), W, 20700).kind == "stand"
    assert f.posture == "standing"


def test_marker_lost_triggers_scan(cfg):
    f = BotFsm(cfg)
    start_match(f)
    lost = WorldView(marker_x=None, compass_center_x=50, dist_ratio=1.2, health_drop_fast=False)
    f.tick(obs(VisualState.GROUNDED, 0), lost, 300)
    assert f.tick(obs(VisualState.GROUNDED, 0), lost, 2400).kind == "scan"


def test_health_drop_prones_once(cfg):
    f = BotFsm(cfg)
    hurt = WorldView(marker_x=50, compass_center_x=50, dist_ratio=1.2, health_drop_fast=True)
    assert f.tick(obs(VisualState.GROUNDED, 0), hurt, 0).kind == "prone"
    assert f.tick(obs(VisualState.GROUNDED, 0), hurt, 100).kind == "release_w"
    assert f.posture == "prone"


def test_unknown_after_open_map_still_closes_on_timeout(cfg):
    f = BotFsm(cfg)
    assert f.tick(obs(VisualState.GROUNDED, 0), W, 0).kind == "open_map"
    assert f.tick(obs(VisualState.UNKNOWN, 0), W, 100).kind == "idle"
    # 识别器一直 UNKNOWN 也要按超时把关图补齐
    assert f.tick(obs(VisualState.UNKNOWN, 0), W, 1500).kind == "close_map"
    assert f.tick(obs(VisualState.UNKNOWN, 0), W, 1600).kind == "idle"


def test_map_state_not_recognized_closes_and_retries(cfg):
    f = BotFsm(cfg)
    assert f.tick(obs(VisualState.GROUNDED, 0), W, 0).kind == "open_map"
    for now in range(100, 1400, 100):
        assert f.tick(obs(VisualState.GROUNDED, 0), W, now).kind == "idle"
    # 超时：无条件补发 close_map，避免地图永久停住
    assert f.tick(obs(VisualState.GROUNDED, 0), W, 1500).kind == "close_map"
    # 关图冷却期内不再按 M
    for now in range(1600, 3400, 100):
        assert f.tick(obs(VisualState.GROUNDED, 0), W, now).kind == "idle"
    # 冷却结束 -> 重试
    assert f.tick(obs(VisualState.GROUNDED, 0), W, 3500).kind == "open_map"
    assert f.tick(obs(VisualState.GROUNDED, 0), W, 3600).kind == "idle"
    assert f.tick(obs(VisualState.GROUNDED, 0), W, 5100).kind == "close_map"
    # 连续失败达上限 -> 趴下（且只按一次姿态键）
    assert f.tick(obs(VisualState.GROUNDED, 0), W, 5200).kind == "prone"
    assert f.mode == "prone"
    assert f.tick(obs(VisualState.GROUNDED, 0), W, 5300).kind in ("idle", "release_w")


def test_new_match_resets_posture_and_mark(cfg):
    f = BotFsm(cfg)
    start_match(f)
    assert f.tick(obs(VisualState.GROUNDED, 0), INSIDE, 400).kind == "prone"
    f.tick(obs(VisualState.LOBBY, 0), INSIDE, 500)
    assert f.posture == "unknown"
    # 新一局没有标点 -> 再次强制开图
    assert f.tick(obs(VisualState.GROUNDED, 0), INSIDE, 600).kind == "open_map"
