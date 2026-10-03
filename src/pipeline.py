"""End-to-end base-case pipeline for the ALM Risk Simulator (educational).

Reads only cached data (the real FRED snapshot if present, otherwise the clearly
labelled fixture), builds the curve, generates the hypothetical holdings and
liabilities, loads them into SQLite, runs validation and aggregations, and
prints the base-case ALM table and key-rate duration profiles.

Run with:  ``python -m src.pipeline``
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Config, DEFAULT_CONFIG
from . import bonds, data_fetch, db, duration as dur, liabilities, validation
from .curve import Curve, bootstrap_zero_curve


@dataclass
class BaseCase:
    """Everything the base-case run produces, for reuse by Phase 2."""

    curve: Curve
    source_tag: str
    observation_date: str
    bonds: list[bonds.Bond]
    portfolio: bonds.PortfolioRisk
    liabilities: liabilities.LiabilityResult
    metrics: dur.ALMMetrics


def build_base_case(cfg: Config | None = None, write: bool = True) -> BaseCase:
    """Build the full base case, optionally writing the data CSVs."""
    cfg = cfg or DEFAULT_CONFIG
    par_yields, obs_date, source_tag = data_fetch.load_curve_source(cfg)
    curve = bootstrap_zero_curve(par_yields, cfg.curve, as_of=obs_date)

    book = bonds.generate_portfolio(curve, cfg)
    if write:
        bonds.write_holdings_csv(book, cfg)
    portfolio = bonds.portfolio_risk(book, curve, cfg)

    liab = liabilities.price_liabilities(curve, asset_value=portfolio.market_value, cfg=cfg)
    if write:
        liabilities.write_liabilities_csv(liab, cfg)

    metrics = dur.alm_metrics(
        asset_value=portfolio.market_value,
        liability_value=liab.total_pv,
        asset_duration=portfolio.effective_duration,
        liability_duration=liab.effective_duration,
        asset_dv01=portfolio.dv01,
        liability_dv01=liab.dv01,
    )
    return BaseCase(
        curve=curve,
        source_tag=source_tag,
        observation_date=obs_date,
        bonds=book,
        portfolio=portfolio,
        liabilities=liab,
        metrics=metrics,
    )


def _fmt_money(x: float) -> str:
    return f"${x:,.0f}"


def print_report(base: BaseCase, cfg: Config | None = None) -> None:
    """Print the base-case ALM table, validation summary and KRD profiles."""
    cfg = cfg or DEFAULT_CONFIG
    m = base.metrics
    pf = base.portfolio
    lb = base.liabilities

    print("=" * 72)
    print("ALM RISK SIMULATOR - BASE CASE (EDUCATIONAL - NOT A PRODUCTION SYSTEM)")
    print("=" * 72)
    label = (
        "REAL FRED Treasury curve"
        if base.source_tag == "REAL"
        else "FIXTURE curve (synthetic, NOT real market data)"
    )
    print(f"Curve source      : {label}")
    print(f"Observation date  : {base.observation_date}")
    print("Holdings & liabilities are HYPOTHETICAL (labelled in data/README.md)")
    print()

    print("Balance sheet")
    print("-" * 72)
    print(f"  Asset market value       : {_fmt_money(m.asset_value)}")
    print(f"  Liability present value   : {_fmt_money(m.liability_value)}")
    print(f"  Surplus                   : {_fmt_money(m.surplus)}")
    print(f"  Funding ratio             : {m.funding_ratio:.4f}  "
          f"({m.funding_ratio*100:.2f}%)")
    print()

    print("Interest-rate risk (effective, Treasury-curve bump)")
    print("-" * 72)
    print(f"  Asset duration            : {m.asset_duration:8.4f} yrs")
    print(f"  Liability duration        : {m.liability_duration:8.4f} yrs")
    print(f"  Duration gap (D_A - D_L)  : {m.duration_gap:8.4f} yrs")
    print(f"  Leverage-adj. gap         : {m.leverage_adjusted_gap:8.4f} yrs")
    print(f"  Asset DV01                : {_fmt_money(m.asset_dv01)}")
    print(f"  Liability DV01            : {_fmt_money(m.liability_dv01)}")
    print(f"  Net DV01 (A - L)          : {_fmt_money(m.net_dv01)}")
    print(f"  Portfolio convexity       : {pf.convexity:8.3f}")
    print(f"  Liability convexity (eff) : {lb.effective_convexity:8.3f}")
    print()

    print("Key-rate duration profile (years)")
    print("-" * 72)
    print(f"  {'tenor':>6} | {'asset KRD':>12} | {'asset KRD DV01':>16} | "
          f"{'liab KRD DV01':>16}")
    liab_krdv = dur.key_rate_dv01s(
        base.curve, lb.years, lb.cash_flows, cfg.risk.key_rate_tenors,
        spread=lb.liability_spread,
    )
    for k in cfg.risk.key_rate_tenors:
        print(f"  {k:>5.0f}y | {pf.key_rate_durations[k]:>12.4f} | "
              f"{_fmt_money(pf.key_rate_dv01s[k]):>16} | "
              f"{_fmt_money(liab_krdv[k]):>16}")
    print(f"  sum of asset KRDs         : "
          f"{sum(pf.key_rate_durations.values()):.4f} yrs "
          f"(~ effective duration {pf.effective_duration:.4f})")
    print()


def run(cfg: Config | None = None) -> BaseCase:
    """Build the base case, persist to SQLite, validate, aggregate and report."""
    cfg = cfg or DEFAULT_CONFIG
    base = build_base_case(cfg, write=True)

    # Persist to SQLite and run data-quality checks + aggregations on real data.
    curve_csv = data_fetch.latest_snapshot_path(cfg) or cfg.paths.fixture_curve_csv
    conn = db.connect(cfg)
    try:
        db.load_all(conn, curve_csv, cfg.paths.holdings_csv,
                    cfg.paths.liabilities_csv, cfg)
        results = validation.run_checks(conn, cfg)
        warnings = validation.enforce(results)
        aggs = db.run_aggregations(conn, cfg)
    finally:
        conn.close()

    print_report(base, cfg)

    print("Data-quality validation (generated base-case data)")
    print("-" * 72)
    summary = validation.summarise(results)
    print(f"  checks: {len(results)}  passed: {summary['passed']}  "
          f"warnings: {summary['warnings']}  errors: {summary['errors']}")
    for w in warnings:
        print(f"  WARNING {w.name}: {w.rows_affected} row(s) - {w.description}")
    print()

    print("Portfolio aggregations (from SQL)")
    print("-" * 72)
    for name, frame in aggs.items():
        print(f"  [{name}]")
        print(frame.to_string(index=False).replace("\n", "\n  "))
        print()

    return base


if __name__ == "__main__":  # pragma: no cover
    run()
