import json
from pathlib import Path
import pytest
from pubgbot.config import ConfigError, Rect, load_config


def write(tmp_path, anchors=None, policy=None):
    a = {"version": 1, "resolution": [64, 48], "calibrated": False,
         "rois": {"compass": [8, 0, 48, 6], "minimap": [44, 32, 16, 16]},
         "anchors": {"lobby_start": {"roi": "compass", "template": None, "threshold": 0.85}},
         "hsv": {"waypoint_yellow": [[18, 90, 120], [38, 255, 255]]},
         "values": {"dead_zone_px": 6, "circle_inner_factor": 0.9}}
    p = {"values": {"frame_interval_ms": 80, "vote_frames": 3, "unused_float": 0.5}}
    (tmp_path / "a.json").write_text(json.dumps(anchors or a), encoding="utf-8")
    (tmp_path / "p.json").write_text(json.dumps(policy or p), encoding="utf-8")
    return load_config(tmp_path / "a.json", tmp_path / "p.json")


def test_loads_rect_and_values(tmp_path):
    cfg = write(tmp_path)
    assert cfg.anchors.resolution == (64, 48)
    assert cfg.anchors.roi("compass") == Rect(8, 0, 48, 6)
    assert cfg.anchors.value("dead_zone_px") == 6
    assert cfg.anchors.anchors["lobby_start"].threshold == 0.85
    assert cfg.policy.int_value("vote_frames") == 3


def test_hsv_range_returns_lower_upper(tmp_path):
    lower, upper = write(tmp_path).anchors.hsv_range("waypoint_yellow")
    assert lower.tolist() == [18, 90, 120]
    assert upper.tolist() == [38, 255, 255]


def test_missing_value_key_raises(tmp_path):
    cfg = write(tmp_path)
    with pytest.raises(ConfigError, match="no_such_key"):
        cfg.anchors.value("no_such_key")


def test_rect_intersect_clips(tmp_path):
    assert Rect(-10, -10, 30, 30).intersect(Rect(0, 0, 64, 48)) == Rect(0, 0, 20, 20)
    assert Rect(100, 100, 10, 10).intersect(Rect(0, 0, 64, 48)) is None


def test_unknown_anchor_roi_reference_raises(tmp_path):
    bad = {"version": 1, "resolution": [64, 48], "calibrated": False, "rois": {},
           "anchors": {"x": {"roi": "missing_roi", "template": None, "threshold": 0.85}},
           "hsv": {}, "values": {}}
    with pytest.raises(ConfigError, match="missing_roi"):
        write(tmp_path, anchors=bad)
