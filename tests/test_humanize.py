import random

from pubgbot.config import HumanizeConfig, Rect
from pubgbot.humanize import Humanizer


def make(enabled=True, seed=1):
    sleeps = []
    h = Humanizer(
        HumanizeConfig(enabled=enabled, seed=seed),
        rng=random.Random(seed),
        sleep=sleeps.append,
    )
    return h, sleeps


def test_disabled_keeps_old_behaviour():
    h, sleeps = make(enabled=False)
    assert h.turn_plan(-8) == [(-8, 0)]
    assert h.scan_steps(8) == [8, 8, 8]
    assert h.click_point(Rect(0, 0, 100, 40)) == (50, 20)
    assert h.mark_jitter(10, 20) == (10, 20)
    h.reaction()
    h.key_hold()
    h.inter_action()
    h.segment_pause()
    assert sleeps == []


def test_turn_plan_split_and_direction_preserved():
    h, _ = make()
    for _ in range(50):
        plan = h.turn_plan(-80)
        assert 3 <= len(plan) <= 8
        assert all(dx < 0 for dx, _ in plan)
        assert abs(abs(sum(dx for dx, _ in plan)) - 80) <= 3
        assert all(-1 <= dy <= 1 for _, dy in plan)


def test_turn_plan_eases_in_and_out():
    h, _ = make()
    plan = h.turn_plan(-200)
    steps = [abs(dx) for dx, _ in plan]
    assert steps[0] <= max(steps)
    assert steps[-1] <= max(steps)


def test_scan_steps_same_direction_and_jittered():
    h, _ = make()
    for _ in range(50):
        steps = h.scan_steps(10)
        assert 2 <= len(steps) <= 4
        assert len({s > 0 for s in steps}) == 1
        assert all(8 <= abs(s) <= 12 for s in steps)


def test_click_point_stays_inside_roi():
    h, _ = make()
    rect = Rect(100, 50, 40, 20)
    points = {h.click_point(rect) for _ in range(50)}
    assert all(100 <= x < 140 and 50 <= y < 70 for x, y in points)
    assert points != {(120, 60)}


def test_sleeps_follow_configured_ranges():
    h, sleeps = make()
    h.reaction()
    h.key_hold()
    h.inter_action()
    assert len(sleeps) == 3
    assert 0.030 <= sleeps[0] <= 0.120
    assert 0.055 <= sleeps[1] <= 0.145
    assert 0.020 <= sleeps[2] <= 0.090


def test_same_seed_is_reproducible():
    a, _ = make(seed=42)
    b, _ = make(seed=42)
    assert [a.turn_plan(-30) for _ in range(5)] == [b.turn_plan(-30) for _ in range(5)]
