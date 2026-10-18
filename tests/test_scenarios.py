"""Tests for the scenario engine, key-rate hedge and model-risk helpers."""

from __future__ import annotations

import numpy as np
import pytest

from src import scenarios as S
from src.config import bps
from src.pipeline import build_base_case


@pytest.fixture(scope="module")
def base():
    return build_base_case(write=False)


@pytest.fixture(scope="module")
def sheet(base):
    return S.BalanceSheet.from_base_case(base)


def test_base_funding_ratio_matches_calibration(sheet, base, cfg):
    assert sheet.funding_ratio(base.curve) == pytest.approx(
        cfg.liabilities.funding_target_mid, rel=1e-9
    )


def test_liabilities_move_under_shock(sheet, base):
    """Regression: liabilities must be re-discounted, never re-calibrated."""
    l0 = sheet.liability_value(base.curve)
    l_dn = sheet.liability_value(base.curve.parallel_shift(bps(-100)))
    l_up = sheet.liability_value(base.curve.parallel_shift(bps(100)))
    assert l_dn > l0 > l_up
    # ~12y duration => roughly 12% move per 100bp
    assert (l_dn / l0 - 1.0) == pytest.approx(0.125, abs=0.03)


def test_unhedged_book_is_hurt_by_falling_rates(sheet, base):
    """Liability duration > asset duration: rate falls erode the funding ratio."""
    fr0 = sheet.funding_ratio(base.curve)
    assert sheet.funding_ratio(base.curve.parallel_shift(bps(-100))) < fr0
    assert sheet.funding_ratio(base.curve.parallel_shift(bps(100))) > fr0


def test_net_dv01_matches_base_case_metrics(sheet, base):
    assert sheet.net_dv01(base.curve) == pytest.approx(base.metrics.net_dv01, rel=2e-3)


def test_taylor_estimate_tracks_full_reprice(sheet, base):
    df = S.run_scenarios(sheet, base.curve, [S.parallel(-100), S.parallel(100)])
    err = (df["taylor_d_surplus"] - df["d_surplus"]).abs() / df["d_surplus"].abs()
    assert (err < 0.03).all()


def test_hedge_zeroes_key_rate_dv01s(sheet, base):
    h = S.solve_key_rate_hedge(sheet, base.curve)
    net = sheet.with_hedge(h).net_krd_dv01(base.curve)
    assert max(abs(v) for v in net.values()) < 1.0   # < $1 on ~$1bn


def test_hedge_is_zero_cost(sheet, base):
    hedged = sheet.with_hedge(S.solve_key_rate_hedge(sheet, base.curve))
    assert hedged.asset_value(base.curve) == pytest.approx(
        sheet.asset_value(base.curve), abs=1e-4
    )
    assert hedged.funding_ratio(base.curve) == pytest.approx(
        sheet.funding_ratio(base.curve), rel=1e-12
    )


def test_hedge_cuts_surplus_volatility_by_over_95pct(sheet, base):
    hedged = sheet.with_hedge(S.solve_key_rate_hedge(sheet, base.curve))
    un = S.run_scenarios(sheet, base.curve)["d_surplus"].abs()
    he = S.run_scenarios(hedged, base.curve)["d_surplus"].abs()
    assert (he < 0.05 * un).all()


def test_hedge_cannot_be_applied_twice(sheet, base):
    h = S.solve_key_rate_hedge(sheet, base.curve)
    with pytest.raises(ValueError):
        S.solve_key_rate_hedge(sheet.with_hedge(h), base.curve)


def test_steepener_and_flattener_are_mirror_images():
    st, fl = S.steepener(), S.flattener()
    for t in (1, 2, 5, 10, 20, 30):
        assert st.shift_bps(t) == pytest.approx(-fl.shift_bps(t))


def test_scenario_table_shape(sheet, base):
    df = S.run_scenarios(sheet, base.curve)
    assert len(df) == len(S.standard_scenarios())
    assert df["funding_ratio"].between(0.5, 2.0).all()


def test_higher_liability_spread_shortens_duration(base, cfg):
    df = S.spread_sensitivity(base.curve, base.portfolio.market_value, cfg=cfg)
    assert df["liability_duration"].is_monotonic_decreasing


def test_interpolation_sensitivity_is_small_but_nonzero(sheet, base):
    out = S.interpolation_sensitivity(sheet, base.curve)
    assert out["difference_pct"] != 0.0
    assert abs(out["difference_pct"]) < 1.0
