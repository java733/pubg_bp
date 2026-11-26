import json
import pytest
from pubgbot.actions import ActionExecutor
from pubgbot.config import load_config
from pubgbot.fsm import Command


class Fake:
    def __init__(self):
        self.calls = []

    def key_down(self, k):
        self.calls.append(("down", k))

    def key_up(self, k):
        self.calls.append(("up", k))

    def move_rel(self, dx, dy):
        self.calls.append(("move", dx, dy))

    def click_right_at(self, x, y):
        self.calls.append(("right", x, y))

    def click_left_at(self, x, y):
        self.calls.append(("left", x, y))


@pytest.fixture
def cfg(tmp_path):
    a = {"version": 1, "resolution": [64, 48], "calibrated": True, "rois": {}, "anchors": {}, "hsv": {}, "values": {}}
    p = {"values": {"steer_step_px": 8}}
    (tmp_path / "a.json").write_text(json.dumps(a))
    (tmp_path / "p.json").write_text(json.dumps(p))
    return load_config(tmp_path / "a.json", tmp_path / "p.json")


def ex(cfg, fg=True):
    b = Fake()
    return ActionExecutor(b, cfg, lambda: fg), b


def test_press_w_then_release(cfg):
    e, b = ex(cfg)
    e.execute(Command("press_w"))
    e.execute(Command("release_w"))
    assert b.calls == [("down", "w"), ("up", "w")]


def test_release_all_on_exception(cfg):
    e, b = ex(cfg)
    e.execute(Command("press_w"))
    with pytest.raises(RuntimeError):
        with e.held():
            raise RuntimeError("boom")
    assert ("up", "w") in b.calls


def test_turn_moves_mouse_by_step(cfg):
    e, b = ex(cfg)
    e.execute(Command("turn", -8))
    assert b.calls == [("move", -8, 0)]


def test_no_input_when_foreground_lost(cfg):
    e, b = ex(cfg, fg=False)
    e.execute(Command("press_w"))
    e.execute(Command("mark", (10, 20)))
    assert b.calls == []


def test_prone_releases_w_first(cfg):
    e, b = ex(cfg)
    e.execute(Command("press_w"))
    e.execute(Command("prone"))
    assert b.calls[:4] == [("down", "w"), ("up", "w"), ("down", "c"), ("up", "c")]
