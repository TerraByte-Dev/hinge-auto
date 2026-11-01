"""calibrate_filters slope math when a thumb can't move (already at an end)."""

import pytest

import calibrate_filters as cf


def test_both_thumbs_moved_use_their_own_slopes():
    assert cf.compute_slopes(300, 30, 137, 18, 559, 50, 941, 85) == (pytest.approx(13.583, abs=1e-3),
                                                                     pytest.approx(10.914, abs=1e-3))


def test_left_thumb_already_at_min_shares_right_slope():
    # S23, 2026-11-01: range 18-50, left thumb can't move left of 18.
    left, right = cf.compute_slopes(137, 18, 137, 18, 559, 50, 941, 85)

    assert left == right == pytest.approx(382 / 35)


def test_right_thumb_already_at_max_shares_left_slope():
    left, right = cf.compute_slopes(300, 30, 137, 18, 941, 85, 941, 85)

    assert left == right == pytest.approx(163 / 12)


def test_neither_thumb_moved_raises():
    with pytest.raises(ValueError, match="neither thumb moved"):
        cf.compute_slopes(137, 18, 137, 18, 941, 85, 941, 85)
