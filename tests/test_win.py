import pytest
from pubgbot.config import Rect
from pubgbot.win import CaptureRegionError, compute_capture_region


def test_fully_inside_monitor_unchanged():
    assert compute_capture_region(Rect(10, 10, 100, 80), Rect(0, 0, 1920, 1080)) == Rect(10, 10, 100, 80)


def test_partially_offscreen_clipped():
    assert compute_capture_region(Rect(-50, 20, 200, 100), Rect(0, 0, 1920, 1080)) == Rect(0, 20, 150, 100)


def test_entirely_offscreen_raises():
    with pytest.raises(CaptureRegionError):
        compute_capture_region(Rect(2000, 0, 100, 100), Rect(0, 0, 1920, 1080))
