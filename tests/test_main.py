import json
import pytest
from pubgbot.capture import FrameInvalid
from pubgbot.config import Rect, load_config
from pubgbot.main import ResolutionMismatch, run, validate_resolution
from pubgbot.safety import SafetyStop
from pubgbot.vision.state import Observation, VisualState


@pytest.fixture
def cfg(tmp_path):
    a = {"version": 1, "resolution": [64, 48], "calibrated": True, "rois": {}, "anchors": {}, "hsv": {}, "values": {}}
    p = {"values": {"frame_interval_ms": 80, "unknown_alert_ms": 10000,
                    "queue_alert_ms": 600000, "max_invalid_frames": 5}}
    (tmp_path / "a.json").write_text(json.dumps(a))
    (tmp_path / "p.json").write_text(json.dumps(p))
    return load_config(tmp_path / "a.json", tmp_path / "p.json")


def test_resolution_mismatch_raises(cfg):
    with pytest.raises(ResolutionMismatch, match=r"expected 64x48, got 1920x1080"):
        validate_resolution(Rect(0, 0, 1920, 1080), cfg)


def test_resolution_ok(cfg):
    validate_resolution(Rect(0, 0, 64, 48), cfg)


class FakeLogger:
    def __init__(self):
        self.rows = []

    def write(self, row):
        self.rows.append(row)


class FakeSafety:
    def __init__(self):
        self.stops = 0

    def check(self):
        return None

    def heartbeat(self):
        return None


def test_run_continues_and_releases_on_stop(cfg):
    state = {"n": 0}
    clock = lambda: state["n"]

    def source():
        state["n"] += 1
        return "FRAME"

    class Rec:
        def observe(self, frame, now):
            return Observation(VisualState.QUEUE, {"a": 0.1}, 0.9)

    class Fsm:
        def tick(self, obs, world, now):
            if now >= 2:
                raise SafetyStop("boom")
            return type("C", (), {"kind": "idle", "arg": None, "reason": ""})()

    released = []
    executor = type("E", (), {"execute": lambda self, c: None, "release_all": lambda self: released.append(1)})()
    result = run(source, lambda f: None, Rec(), Fsm(), executor, FakeSafety(), FakeLogger(), clock)
    assert released == [1]
    assert result["frames"] == 1


class _NoopExecutor:
    def execute(self, cmd):
        return None

    def release_all(self):
        return None


class _NoopFsm:
    def tick(self, obs, world, now):
        return type("C", (), {"kind": "idle", "arg": None, "reason": ""})()


class _NoopRec:
    def observe(self, frame, now):
        return Observation(VisualState.QUEUE, {}, 1.0)


def test_invalid_frame_streak_stops(cfg):
    def gate(frame):
        raise FrameInvalid("bad frame")

    logger = FakeLogger()
    result = run(
        lambda: "FRAME",
        gate,
        _NoopRec(),
        _NoopFsm(),
        _NoopExecutor(),
        FakeSafety(),
        logger,
        lambda: 0,
        cfg=cfg,
    )
    assert result["frames"] == 0
    assert result["stops"] and "连续 5 帧无效" in result["stops"][0]
    assert any(row.get("event") == "stop" for row in logger.rows)


def test_unexpected_exception_is_recorded(cfg):
    class BoomFsm:
        def tick(self, obs, world, now):
            raise ValueError("bad state")

    logger = FakeLogger()
    result = run(
        lambda: "FRAME",
        lambda f: None,
        _NoopRec(),
        BoomFsm(),
        _NoopExecutor(),
        FakeSafety(),
        logger,
        lambda: 0,
        cfg=cfg,
    )
    assert result["stops"] == ["ValueError: bad state"]
    assert any(row.get("event") == "stop" for row in logger.rows)
