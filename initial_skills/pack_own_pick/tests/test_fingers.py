import glob
import os

import cv2
import pytest

from helpers.fingers import finger_feats, calib_shift

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_finger_frames():
    """Run 015 (L3 1079): the right finger blob stops short of the image edge; it must still be found."""
    files = sorted(glob.glob(os.path.join(HERE, "practice/runs/015/00_L3_1079/observations/00[0-2]_L_wrist_rgb.png")))
    if not files:
        pytest.skip("no saved frames")
    for f in files:
        rgb = cv2.cvtColor(cv2.imread(f), cv2.COLOR_BGR2RGB)
        ff = finger_feats(rgb)
        assert ff is not None, f
        assert 325 < ff["rx"] - ff["lx"] < 355, ff
        du, dv = calib_shift(ff)
        assert abs(du) < 30 and abs(dv) < 30
