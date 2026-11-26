import json
import pytest
from pubgbot.config import load_config
from pubgbot.main import alerts_for, summarize_run


@pytest.fixture
def cfg(tmp_path):
    a = {"version": 1, "resolution": [64, 48], "calibrated": True, "rois": {}, "anchors": {}, "hsv": {}, "values": {}}
    p = {"values": {"unknown_alert_ms": 10000, "queue_alert_ms": 600000}}
    (tmp_path / "a.json").write_text(json.dumps(a))
    (tmp_path / "p.json").write_text(json.dumps(p))
    return load_config(tmp_path / "a.json", tmp_path / "p.json")


def test_alert_kinds(cfg):
    assert alerts_for("UNKNOWN", 10001, cfg) == ["unknown_too_long"]
    assert alerts_for("QUEUE", 600001, cfg) == ["queue_too_long"]
    assert alerts_for("QUEUE", 1000, cfg) == []


def test_summarize_run_reports_frames():
    s = summarize_run([{"state": "LOBBY"}] * 5)
    assert s["frames"] == 5
    assert s["states"]["LOBBY"] == 5
