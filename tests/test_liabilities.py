"""Tests for liabilities.py: PV equals discounted sum, calibration, durations."""

from __future__ import annotations

import numpy as np
import pytest

from src import liabilities as lib
from src import duration as dur


def test_liability_pv_equals_sum_of_discounted_flows(fixture_curve):
    res = lib.price_liabilities(fixture_curve, target_pv=1_000.0)
    manual = float(np.sum(res.cash_flows * res.discount_factors))
    assert res.total_pv == pytest.approx(manual, rel=1e-12)
    assert res.total_pv == pytest.approx(1_000.0, rel=1e-9)


def test_calibration_hits_funding_target(fixture_curve, cfg):
    """Scaling to an asset value lands the funding ratio at the mid target."""
    asset_value = 1_000_000_000.0
    res = lib.price_liabilities(fixture_curve, asset_value=asset_value)
    funding = asset_value / res.total_pv
    assert funding == pytest.approx(cfg.liabilities.funding_target_mid, rel=1e-9)
    assert cfg.liabilities.funding_target_low <= funding <= cfg.liabilities.funding_target_high


def test_modified_equals_macaulay_relationship(fixture_curve):
    res = lib.price_liabilities(fixture_curve, target_pv=1_000.0)
    assert res.modified_duration == pytest.approx(
        res.macaulay_duration / (1 + res.irr), rel=1e-9
    )


def test_effective_and_analytic_durations_are_close(fixture_curve):
    res = lib.price_liabilities(fixture_curve, target_pv=1_000.0)
    assert res.effective_duration == pytest.approx(res.modified_duration, rel=0.05)


def test_liability_duration_is_long(fixture_curve):
    """A 40y life book should be long-duration (sanity, not exact)."""
    res = lib.price_liabilities(fixture_curve, target_pv=1_000.0)
    assert res.effective_duration > 8.0


def test_schedule_has_full_horizon(fixture_curve, cfg):
    res = lib.price_liabilities(fixture_curve, target_pv=1_000.0)
    assert len(res.years) == cfg.liabilities.horizon_years
    assert np.all(res.cash_flows > 0)
