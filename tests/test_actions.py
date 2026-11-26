import json
import random

import pytest

from pubgbot.actions import ActionExecutor
from pubgbot.config import HumanizeConfig, load_config
from pubgbot.fsm import Command
from pubgbot.humanize import Humanizer


class Fake:
    def __init__(self, cursor=(0, 0)):
        self.calls = []
        self._cursor = cursor

    def key_down(self, k):
        self.calls.append(("down", k))

    def key_up(self, k):
        self.calls.append(("up", k))

    def move_rel(self, dx, dy):
        self.calls.append(("move", dx, dy))

    def cursor_pos(self):
        return self._cursor

    def mouse_down(self, button):
        self.calls.append(("mdown", button))

    def mouse_up(self, button):
        self.calls.append(("mup", button))


@pytest.fixture
def cfg(tmp_path):
    a = {
        "version": 1,
        "resolution": [64, 48],
        "calibrated": True,
        "rois": {"lobby_button": [10, 10, 20, 10], "return_button": [40, 10, 20, 10]},
        "anchors": {},
        "hsv": {},
        "values": {"scan_step_px": 8},
    }
    p = {"values": {"steer_step_px": 8}, "humanize": {"enabled": False}}
    (tmp_path / "a.json").write_text(json.dumps(a))
    (tmp_path / "p.json").write_text(json.dumps(p))
    return load_config(tmp_path / "a.json", tmp_path / "p.json")


def ex(cfg, fg=True, humanizer=None):
    b = Fake()
    return ActionExecutor(b, cfg, lambda: fg, humanizer), b


def humanizer(seed=7):
    """记录 sleep 的人类化实例，测试里不真的睡。"""
    sleeps = []
    h = Humanizer(HumanizeConfig(seed=seed), rng=random.Random(seed), sleep=sleeps.append)
    return h, sleeps


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


def test_turn_split_when_humanized(cfg):
    h, sleeps = humanizer()
    e, b = ex(cfg, humanizer=h)
    e.execute(Command("turn", -8))
    moves = [c for c in b.calls if c[0] == "move"]
    assert 3 <= len(moves) <= 8
    assert abs(abs(sum(m[1] for m in moves)) - 8) <= 1
    assert all(m[2] in (-1, 0, 1) for m in moves)
    assert len(sleeps) == len(moves)


def test_tap_holds_key_before_release(cfg):
    h, sleeps = humanizer()
    e, b = ex(cfg, humanizer=h)
    e.execute(Command("jump"))
    assert b.calls == [("down", "f"), ("up", "f")]
    # reaction + key_hold + inter_action
    assert len(sleeps) == 3


def test_click_start_moves_in_two_segments(cfg):
    h, sleeps = humanizer()
    e, b = ex(cfg, humanizer=h)
    e.execute(Command("click_start"))
    moves = [c for c in b.calls if c[0] == "move"]
    assert len(moves) == 2
    assert b.calls[-2:] == [("mdown", "left"), ("mup", "left")]
    assert sleeps


def test_mark_uses_right_button(cfg):
    h, _ = humanizer()
    e, b = ex(cfg, humanizer=h)
    e.execute(Command("mark", (30, 20)))
    assert ("mdown", "right") in b.calls
    assert ("mup", "right") in b.calls


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
