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
    holder = fg if isinstance(fg, dict) else {"ok": fg}
    s = Safety(cfg, lambda: released.append(1), clock, lambda: holder["ok"])
    return s, released, holder


def test_watchdog_triggers_release(cfg):
    c = Clock()
    s, rel, _ = build(cfg, c)
    s.start()
    s.heartbeat()
    c.t = 2001
    with pytest.raises(SafetyStop, match="watchdog"):
        s.check()
    assert rel == [1]


def test_session_limit_stops(cfg):
    c = Clock()
    s, rel, _ = build(cfg, c)
    s.start()
    c.t = 28800001
    with pytest.raises(SafetyStop, match="session"):
        s.check()


def test_foreground_lost_pauses_instead_of_stopping(cfg):
    c = Clock()
    s, rel, fg = build(cfg, c, fg=False)
    s.start()
    s.check()  # 切出游戏不应抛 SafetyStop
    assert rel == [1]
    s.check()  # 持续切出只释放一次
    assert rel == [1]
    assert s.stop_requested() is False
    fg["ok"] = True
    s.check()  # 回到游戏后继续运行
    assert s.stop_requested() is False


def test_hotkey_stop_releases_once(cfg):
    c = Clock()
    s, rel, _ = build(cfg, c)
    s.start()
    s.heartbeat()
    s.request_stop("F12")
    with pytest.raises(SafetyStop):
        s.check()
    assert rel == [1]
    assert s.stop_requested() is True
