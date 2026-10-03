"""Tests for the SQL validation layer: each check catches its corrupted input."""

from __future__ import annotations

import pandas as pd
import pytest

from src import db, validation


@pytest.fixture
def corrupt_conn(cfg):
    """In-memory DB loaded with the corrupted holdings sample + a bad liability."""
    conn = db.connect(in_memory=True)
    db.init_schema(conn, cfg)
    holdings = pd.read_csv(cfg.paths.corrupted_holdings_csv)
    holdings.to_sql("holdings", conn, if_exists="append", index=False)
    # Minimal liabilities table with one wrong-signed (negative) outflow.
    liab = pd.DataFrame(
        {
            "year": [1, 2, 3],
            "expected_cash_flow_hypothetical": [100.0, -50.0, 75.0],
            "discount_factor": [0.98, 0.95, 0.92],
            "present_value": [98.0, -47.5, 69.0],
            "duration_contribution_years": [0.1, -0.1, 0.2],
        }
    )
    liab.to_sql("liabilities", conn, if_exists="append", index=False)
    yield conn
    conn.close()


def test_every_check_catches_its_corrupted_input(corrupt_conn, cfg):
    results = {r.name: r for r in validation.run_checks(corrupt_conn, cfg)}
    expected = {
        "missing_values",
        "duplicate_ids",
        "invalid_maturity",
        "issue_after_maturity",
        "settlement_before_issue",
        "negative_coupon_or_face",
        "out_of_range_yield",
        "wrong_sign_liability",
    }
    assert expected.issubset(results.keys())
    for name in expected:
        assert results[name].rows_affected >= 1, f"{name} failed to catch its defect"


def test_errors_fail_loudly_warnings_do_not(corrupt_conn, cfg):
    results = validation.run_checks(corrupt_conn, cfg)
    with pytest.raises(validation.ValidationError):
        validation.enforce(results)
    # The out-of-range yield is a warning, not an error.
    by_name = {r.name: r for r in results}
    assert by_name["out_of_range_yield"].severity == "warning"
    assert by_name["missing_values"].severity == "error"


def test_clean_data_passes(fixture_curve, cfg, tmp_path):
    """Generated base-case data passes every check with no errors or warnings."""
    from src import bonds, liabilities

    book = bonds.generate_portfolio(fixture_curve, cfg)
    liab = liabilities.price_liabilities(
        fixture_curve, asset_value=sum(b.market_value for b in book), cfg=cfg
    )
    conn = db.connect(in_memory=True)
    db.init_schema(conn, cfg)
    bonds.portfolio_to_frame(book).to_sql("holdings", conn, if_exists="append", index=False)
    liab.to_frame().to_sql("liabilities", conn, if_exists="append", index=False)

    results = validation.run_checks(conn, cfg)
    summary = validation.summarise(results)
    conn.close()
    assert summary["errors"] == 0
    assert summary["warnings"] == 0


def test_aggregations_run(fixture_curve, cfg):
    from src import bonds

    book = bonds.generate_portfolio(fixture_curve, cfg)
    conn = db.connect(in_memory=True)
    db.init_schema(conn, cfg)
    bonds.portfolio_to_frame(book).to_sql("holdings", conn, if_exists="append", index=False)
    aggs = db.run_aggregations(conn, cfg)
    conn.close()
    assert "mv_by_maturity_bucket" in aggs
    assert "mv_by_issuer_type" in aggs
    assert aggs["mv_by_issuer_type"]["market_value"].sum() == pytest.approx(
        sum(b.market_value for b in book), rel=1e-6
    )
