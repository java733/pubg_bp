import json
from pathlib import Path
import numpy as np
import cv2
from pubgbot.config import load_config
from pubgbot.vision.state import StateRecognizer, VisualState


def build(tmp_path):
    a = {"version": 1, "resolution": [64, 48], "calibrated": True,
         "rois": {"full": [0, 0, 64, 48]},
         "anchors": {"lobby_start": {"roi": "full", "template": "lobby.png", "threshold": 0.85, "state": "LOBBY"},
                     "plane_prompt": {"roi": "full", "template": "plane.png", "threshold": 0.85, "state": "PLANE"},
                     "result_text": {"roi": "full", "template": "result.png", "threshold": 0.85, "state": "RESULT"}},
         "hsv": {}, "values": {}}
    p = {"values": {"vote_frames": 3, "min_dwell_ms": 500, "template_threshold": 0.85}}
    (tmp_path / "t").mkdir()
    for name, val in (("lobby", 60), ("plane", 130), ("result", 200)):
        cv2.imwrite(str(tmp_path / "t" / f"{name}.png"), np.full((20, 20, 3), val, np.uint8))
    (tmp_path / "a.json").write_text(json.dumps(a))
    (tmp_path / "p.json").write_text(json.dumps(p))
    return load_config(tmp_path / "a.json", tmp_path / "p.json"), tmp_path / "t"


def frame(val):
    f = np.full((48, 64, 3), 10, np.uint8)
    f[10:30, 10:30] = val
    return f


def labels(cfg, tpl):
    return StateRecognizer(cfg, tpl)


def test_switches_after_three_consistent_frames(tmp_path):
    cfg, tpl = build(tmp_path)
    r = labels(cfg, tpl)
    obs = [r.observe(frame(60), now) for now in (0, 100, 200, 300)]
    assert obs[0].state is VisualState.UNKNOWN
    assert obs[-1].state is VisualState.LOBBY


def test_single_outlier_frame_does_not_flip(tmp_path):
    cfg, tpl = build(tmp_path)
    r = labels(cfg, tpl)
    for now in (0, 100, 200):
        r.observe(frame(60), now)
    assert r.observe(frame(130), 300).state is VisualState.LOBBY


def test_dwell_blocks_fast_flip(tmp_path):
    cfg, tpl = build(tmp_path)
    r = labels(cfg, tpl)
    for now in (0, 100, 200):
        r.observe(frame(60), now)
    for now in (300, 400, 500):
        r.observe(frame(130), now)
    assert r.observe(frame(130), 600).state is VisualState.LOBBY  # 驻留未满 500ms 自切换点起算
    for now in (700, 800, 900, 1000):
        r.observe(frame(130), now)
    assert r.observe(frame(130), 1100).state is VisualState.PLANE


def test_nothing_matches_is_unknown(tmp_path):
    cfg, tpl = build(tmp_path)
    r = labels(cfg, tpl)
    obs = [r.observe(np.zeros((48, 64, 3), np.uint8), now) for now in (0, 100, 200)]
    assert all(o.state is VisualState.UNKNOWN for o in obs)
