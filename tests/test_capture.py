import numpy as np
import pytest
from pubgbot.capture import FrameGate, FrameInvalid, to_bgr


def test_black_frame_rejected():
    gate = FrameGate((64, 48))
    with pytest.raises(FrameInvalid, match="black"):
        gate.validate(np.zeros((48, 64, 3), np.uint8))


def test_wrong_size_rejected():
    gate = FrameGate((64, 48))
    with pytest.raises(FrameInvalid, match="shape"):
        gate.validate(np.full((10, 10, 3), 200, np.uint8))


def test_valid_frame_passes():
    FrameGate((64, 48)).validate(np.full((48, 64, 3), 120, np.uint8))


def test_rgb_and_bgra_converted_to_bgr():
    rgb = np.zeros((2, 2, 3), np.uint8)
    rgb[..., 0] = 255  # R=255
    assert to_bgr(rgb, "dxcam")[0, 0].tolist() == [0, 0, 255]
    bgra = np.zeros((2, 2, 4), np.uint8)
    bgra[..., 0] = 255  # B=255
    assert to_bgr(bgra, "mss")[0, 0].tolist() == [255, 0, 0]
