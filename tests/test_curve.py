"""Tests for curve.py: bootstrap reprices par instruments, shifts, immutability."""

from __future__ import annotations

import numpy as np
import pytest

from src.curve import Curve, bootstrap_zero_curve, shift as shift_fn
from src.duration import price_at_yield


def test_discount_factor_formula(flat_curve):
    """DF(t) = (1+z)^(-t) with annual compounding."""
    for t in (0.5, 1.0, 7.3, 30.0):
        assert flat_curve.discount_factor(t) == pytest.approx((1.04) ** (-t))
    assert flat_curve.discount_factor(0.0) == pytest.approx(1.0)


def test_bootstrap_reprices_par_instruments(par_yields, cfg):
    """Each par bond (coupon = par yield) reprices to 100 on the zero curve."""
    curve = bootstrap_zero_curve(par_yields, cfg.curve)
    freq = cfg.curve.bootstrap_frequency
    short_cutoff = cfg.fred.par_bond_min_tenor_years
    for tenor, par in par_yields.items():
        if tenor < short_cutoff:
            # Short end treated as zero-coupon: zero rate equals the quote.
            assert curve.zero_rate(tenor) == pytest.approx(par, abs=1e-9)
            continue
        n = int(round(tenor * freq))
        times = np.arange(1, n + 1, dtype=float) / freq
        amounts = np.full(n, par / freq * 100.0)
        amounts[-1] += 100.0
        dfs = np.array([curve.discount_factor(t) for t in times])
        price = float(np.sum(amounts * dfs))
        assert price == pytest.approx(100.0, abs=1e-6)


def test_zero_shift_returns_base_case(fixture_curve):
    """A zero parallel shift and a zero general shift reproduce the base curve."""
    base = fixture_curve
    assert np.allclose(base.parallel_shift(0.0).zeros, base.zeros)
    assert np.allclose(shift_fn(base, lambda t: 0.0).zeros, base.zeros)


def test_shift_does_not_mutate_original(fixture_curve):
    """Shocks return new objects; the original curve is unchanged and read-only."""
    base = fixture_curve
    original = base.zeros.copy()
    bumped = base.parallel_shift(0.01)
    assert np.allclose(base.zeros, original)          # unchanged
    assert not np.allclose(bumped.zeros, base.zeros)  # genuinely different
    assert bumped is not base
    # Underlying array is immutable.
    with pytest.raises(ValueError):
        base.zeros[0] = 0.123


def test_parallel_shift_moves_every_pillar(fixture_curve):
    bumped = fixture_curve.parallel_shift(0.0025)
    assert np.allclose(bumped.zeros - fixture_curve.zeros, 0.0025)


def test_key_rate_bump_is_local(fixture_curve):
    """A triangular key-rate bump moves the target tenor and decays to zero."""
    bumped = fixture_curve.key_rate_bump(10.0, bump_bps=50.0, half_width=3.0)
    diff = bumped.zeros - fixture_curve.zeros
    tenors = fixture_curve.tenors
    at_10 = diff[np.argmin(np.abs(tenors - 10.0))]
    at_2 = diff[np.argmin(np.abs(tenors - 2.0))]
    assert at_10 == pytest.approx(0.0050, abs=1e-9)   # 50bp at the key tenor
    assert at_2 == pytest.approx(0.0, abs=1e-12)       # far away: untouched


def test_curve_rejects_bad_inputs():
    with pytest.raises(ValueError):
        Curve(tenors=np.array([2.0, 1.0]), zeros=np.array([0.01, 0.02]))
    with pytest.raises(ValueError):
        Curve(tenors=np.array([1.0, 2.0]), zeros=np.array([0.01]))
