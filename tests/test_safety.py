import json
import pytest
from pubgbot.config import load_config
from pubgbot.safety import Safety, SafetyStop


@pytest.fixture
def cfg(tmp_path):
    a = {"version": 1, "resolution": [64, 48], "calibrated": True, "rois": {}, "anchors": {}, "hsv": {}, "values": {}}
    p = {"values": {"watchdog_ms": 2000, "session_limit_ms": 28800000,
                    "unknown_alert_ms": 10000, "queue_alert_ms": 600000}}
    (tmp_path / "a.json").write_text(json.dumps(a))
    (tmp_path / "p.json").write_text(json.dumps(p))
    return load_config(tmp_path / "a.json", tmp_path / "p.json")


class Clock:
    def __init__(self):
        self.t = 0

    def __call__(self):
        return self.t


def build(cfg, clock, fg=True):
    released = []
    s = Safety(cfg, lambda: released.append(1), clock, lambda: fg)
    return s, released


def test_watchdog_triggers_release(cfg):
    c = Clock()
    s, rel = build(cfg, c)
    s.start()
    s.heartbeat()
    c.t = 2001
    with pytest.raises(SafetyStop, match="watchdog"):
        s.check()
    assert rel == [1]


def test_session_limit_stops(cfg):
    c = Clock()
    s, rel = build(cfg, c)
    s.start()
    c.t = 28800001
    with pytest.raises(SafetyStop, match="session"):
        s.check()


def test_foreground_lost_stops(cfg):
    c = Clock()
    s, rel = build(cfg, c, fg=False)
    s.start()
    with pytest.raises(SafetyStop, match="foreground"):
        s.check()


def test_hotkey_stop_releases_once(cfg):
    c = Clock()
    s, rel = build(cfg, c)
    s.start()
    s.heartbeat()
    s.request_stop("F12")
    with pytest.raises(SafetyStop):
        s.check()
    assert rel == [1]
    assert s.stop_requested() is True
