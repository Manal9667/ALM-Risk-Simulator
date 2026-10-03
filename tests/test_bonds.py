"""Tests for bonds.py: par pricing, curve/YTM consistency, portfolio aggregation."""

from __future__ import annotations

import numpy as np
import pytest

from src import bonds
from src import duration as dur
from .conftest import make_cashflows


def test_bond_prices_at_par_when_coupon_equals_yield():
    """P = 100 (per 100 face) when the coupon rate equals the YTM."""
    for y, T in [(0.05, 10.0), (0.03, 5.0), (0.07, 30.0)]:
        t, a = make_cashflows(face=100.0, coupon=y, maturity=T, freq=2)
        price = dur.price_at_yield(t, a, y, 2)
        assert price == pytest.approx(100.0, abs=1e-9)


def test_curve_price_and_ytm_price_are_consistent(fixture_curve):
    """Repricing a bond at its solved YTM reproduces the curve price."""
    book = bonds.generate_portfolio(fixture_curve)
    for b in book:
        cfs = b.cashflows()
        curve_price = bonds.price_on_curve(cfs, fixture_curve, b.spread)
        ytm_price = dur.price_at_yield(cfs.times, cfs.amounts, b.ytm, b.frequency)
        assert ytm_price == pytest.approx(curve_price, rel=1e-9)


def test_treasuries_have_zero_spread(fixture_curve):
    book = bonds.generate_portfolio(fixture_curve)
    for b in book:
        if b.rating == "UST":
            assert b.spread == pytest.approx(0.0)
        else:
            assert b.spread > 0.0


def test_portfolio_size_and_maturity_range(fixture_curve):
    book = bonds.generate_portfolio(fixture_curve)
    assert 12 <= len(book) <= 20
    mats = [b.maturity_years for b in book]
    assert min(mats) >= 1.0
    assert max(mats) <= 30.0


def test_portfolio_dv01_is_additive(fixture_curve):
    book = bonds.generate_portfolio(fixture_curve)
    pr = bonds.portfolio_risk(book, fixture_curve)
    manual = sum(
        dur.dv01_on_curve(fixture_curve, b.cashflows().times,
                          b.cashflows().amounts, spread=b.spread)
        for b in book
    )
    assert pr.dv01 == pytest.approx(manual, rel=1e-9)
    assert pr.market_value == pytest.approx(sum(b.market_value for b in book))


def test_higher_rating_has_lower_yield_same_maturity(fixture_curve):
    """At equal maturity, a wider credit spread implies a higher YTM."""
    vd = __import__("datetime").date(2024, 6, 28)
    aa = bonds.build_bond(
        bond_id="AA10", issuer="x", issuer_type="corp", rating="AA",
        face=100, coupon=0.05, maturity_years=10,
        spread=bonds.bps(60), curve=fixture_curve, valuation_date=vd,
    )
    bbb = bonds.build_bond(
        bond_id="BBB10", issuer="y", issuer_type="corp", rating="BBB",
        face=100, coupon=0.05, maturity_years=10,
        spread=bonds.bps(190), curve=fixture_curve, valuation_date=vd,
    )
    assert bbb.ytm > aa.ytm
