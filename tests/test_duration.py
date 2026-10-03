"""Tests for duration.py: analytic vs finite-difference, convexity, KRDs, ALM."""

from __future__ import annotations

import numpy as np
import pytest

from src import duration as dur
from .conftest import make_cashflows


def test_zero_coupon_price_formula():
    """A single cash flow reprices as 1/(1+y/m)^(mT)."""
    y, m, T = 0.05, 2, 7.0
    price = dur.price_at_yield(np.array([T]), np.array([100.0]), y, m)
    assert price == pytest.approx(100.0 * (1 + y / m) ** (-m * T))


def test_price_falls_when_yield_rises():
    t, a = make_cashflows(coupon=0.05, maturity=10)
    p_low = dur.price_at_yield(t, a, 0.03, 2)
    p_high = dur.price_at_yield(t, a, 0.07, 2)
    assert p_high < p_low


def test_macaulay_of_zero_equals_maturity():
    """Macaulay duration of a zero-coupon bond equals its maturity."""
    for T in (1.0, 5.0, 30.0):
        d = dur.macaulay_duration(np.array([T]), np.array([100.0]), 0.04, 2)
        assert d == pytest.approx(T)


def test_modified_equals_macaulay_over_one_plus_y_over_m():
    t, a = make_cashflows(coupon=0.06, maturity=12)
    y, m = 0.05, 2
    mac = dur.macaulay_duration(t, a, y, m)
    mod = dur.modified_duration(t, a, y, m)
    assert mod == pytest.approx(mac / (1 + y / m))


def test_analytic_duration_matches_finite_difference():
    """Modified duration equals the central finite-difference estimate."""
    t, a = make_cashflows(coupon=0.05, maturity=10)
    y, m, h = 0.045, 2, 1e-6
    p0 = dur.price_at_yield(t, a, y, m)
    p_up = dur.price_at_yield(t, a, y + h, m)
    p_dn = dur.price_at_yield(t, a, y - h, m)
    fd_mod = -(p_up - p_dn) / (2 * p0 * h)
    assert dur.modified_duration(t, a, y, m) == pytest.approx(fd_mod, rel=1e-5)


def test_analytic_convexity_matches_finite_difference():
    t, a = make_cashflows(coupon=0.05, maturity=10)
    y, m, h = 0.045, 2, 1e-4
    p0 = dur.price_at_yield(t, a, y, m)
    p_up = dur.price_at_yield(t, a, y + h, m)
    p_dn = dur.price_at_yield(t, a, y - h, m)
    fd_cvx = (p_up + p_dn - 2 * p0) / (p0 * h * h)
    assert dur.convexity_analytic(t, a, y, m) == pytest.approx(fd_cvx, rel=1e-4)


def test_convexity_improves_large_shock_approximation():
    """Adding the convexity term beats a duration-only estimate for a big move."""
    t, a = make_cashflows(coupon=0.05, maturity=20)
    y, m, dy = 0.045, 2, 0.02  # 200bp shock
    p0 = dur.price_at_yield(t, a, y, m)
    actual = dur.price_at_yield(t, a, y + dy, m)
    mod = dur.modified_duration(t, a, y, m)
    cvx = dur.convexity_analytic(t, a, y, m)
    dur_only = p0 - mod * p0 * dy
    dur_cvx = p0 + dur.taylor_price_change(p0, mod, cvx, dy)
    assert abs(dur_cvx - actual) < abs(dur_only - actual)


def test_dv01_matches_duration_definition():
    t, a = make_cashflows(coupon=0.05, maturity=10)
    y, m = 0.045, 2
    p = dur.price_at_yield(t, a, y, m)
    mod = dur.modified_duration(t, a, y, m)
    assert dur.dv01_analytic(t, a, y, m) == pytest.approx(mod * p * 1e-4)


def test_krds_sum_to_effective_duration(fixture_curve):
    """Tent-shaped key-rate durations sum to the effective (parallel) duration."""
    t, a = make_cashflows(coupon=0.04, maturity=30)
    krds = dur.key_rate_durations(fixture_curve, t, a)
    eff = dur.effective_duration(fixture_curve, t, a)
    assert sum(krds.values()) == pytest.approx(eff, rel=1e-3)


def test_effective_duration_matches_annual_modified_on_flat_curve(flat_curve):
    """On a flat annually-compounded curve, effective duration equals the
    modified (Fisher-Weil) duration computed with annual compounding at the same
    rate, because effective duration bumps that annual zero rate directly."""
    t, a = make_cashflows(coupon=0.05, maturity=10)
    eff = dur.effective_duration(flat_curve, t, a)
    mod_annual = dur.modified_duration(t, a, 0.04, freq=1)
    assert eff == pytest.approx(mod_annual, rel=1e-4)


def test_alm_metrics_definitions():
    m = dur.alm_metrics(
        asset_value=110.0, liability_value=100.0,
        asset_duration=6.0, liability_duration=12.0,
        asset_dv01=66.0, liability_dv01=120.0,
    )
    assert m.funding_ratio == pytest.approx(1.10)
    assert m.surplus == pytest.approx(10.0)
    assert m.duration_gap == pytest.approx(-6.0)
    assert m.leverage_adjusted_gap == pytest.approx(6.0 - (100.0 / 110.0) * 12.0)
    assert m.net_dv01 == pytest.approx(66.0 - 120.0)
