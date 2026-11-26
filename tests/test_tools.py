import cv2
import numpy as np
from pubgbot.config import Rect
from tools.pick_roi import crop_and_save


def test_crop_and_save_writes_exact_patch(tmp_path):
    f = np.zeros((20, 20, 3), np.uint8)
    f[5:10, 5:10] = 255
    out = crop_and_save(f, Rect(5, 5, 5, 5), tmp_path / "t.png")
    img = cv2.imread(str(out))
    assert img.shape[:2] == (5, 5)
    assert int(img.mean()) == 255
