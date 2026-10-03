"""Hypothetical bond portfolio priced on the REAL Treasury curve (educational).

IMPORTANT: the bond holdings, issuers, ratings and corporate credit spreads in
this module are entirely HYPOTHETICAL and are labelled as such in the data and
docs. Only the Treasury zero curve they are priced on is real (from FRED).

Pricing model
-------------
* Each bond pays semiannual coupons of ``coupon/freq · face`` at times
  ``1/freq, 2/freq, ..., maturity`` and repays ``face`` at maturity. We treat
  settlement as falling on a coupon date (an educational idealisation), so clean
  and dirty prices coincide and the schedule is exact.
* **Curve pricing:** each cash flow is discounted on the Treasury zero curve
  shifted up by the bond's hypothetical credit spread, i.e. the discount rate at
  time ``t`` is ``z(t) + spread``. Treasuries use ``spread = 0``.
      MV = Σ CF_i · (1 + z(t_i) + spread)^(−t_i)
* **Yield to maturity:** the single flat yield (semiannual) that reproduces the
  curve price. Because YTM is solved from the curve price, repricing at the YTM
  returns the curve price (consistency is asserted in tests and reported).

Portfolio aggregates: market value, value-weighted modified/effective duration
and convexity, DV01 (additive), and value-weighted key-rate durations.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, timedelta

import numpy as np
import pandas as pd
from scipy.optimize import brentq

from .config import Config, DEFAULT_CONFIG, bps
from .curve import Curve
from . import duration as dur


ISSUER_TYPE_TREASURY = "Treasury (REAL curve)"
ISSUER_TYPE_CORPORATE = "Corporate (HYPOTHETICAL)"


@dataclass(frozen=True)
class Bond:
    """A single bond holding. All corporate attributes are hypothetical."""

    id: str
    issuer: str
    issuer_type: str
    rating: str
    face: float
    coupon: float            # annual coupon rate (decimal)
    frequency: int           # coupons per year
    issue_date: str          # ISO
    settlement_date: str     # ISO
    maturity_date: str       # ISO
    maturity_years: float    # year fraction settlement -> maturity (idealised)
    spread: float            # hypothetical credit spread (decimal); 0 for UST
    ytm: float               # solved yield to maturity (decimal)
    price: float             # clean price per 100 face
    market_value: float      # price/100 * face

    def cashflows(self) -> dur.CashFlows:
        """Semiannual coupon + principal cash flows (times in years)."""
        n = int(round(self.maturity_years * self.frequency))
        times = np.arange(1, n + 1, dtype=float) / self.frequency
        coupon_amt = self.coupon / self.frequency * self.face
        amounts = np.full(n, coupon_amt, dtype=float)
        amounts[-1] += self.face
        return dur.CashFlows(times=times, amounts=amounts)


# --------------------------------------------------------------------------- #
# Pricing
# --------------------------------------------------------------------------- #

def price_on_curve(
    cashflows: dur.CashFlows, curve: Curve, spread: float
) -> float:
    """Dirty present value of cash flows on ``curve`` shifted by ``spread``."""
    return dur.present_value_on_curve(
        curve, cashflows.times, cashflows.amounts, spread=spread
    )


def solve_ytm(
    cashflows: dur.CashFlows, target_price: float, freq: int,
    cfg: Config | None = None,
) -> float:
    """Solve the flat (semiannual) yield reproducing ``target_price``."""
    cfg = cfg or DEFAULT_CONFIG
    t, a = cashflows.times, cashflows.amounts

    def f(y: float) -> float:
        return dur.price_at_yield(t, a, y, freq) - target_price

    return float(
        brentq(
            f, cfg.risk.ytm_lower, cfg.risk.ytm_upper,
            xtol=cfg.risk.ytm_tolerance, rtol=1e-14,
        )
    )


def build_bond(
    *,
    bond_id: str,
    issuer: str,
    issuer_type: str,
    rating: str,
    face: float,
    coupon: float,
    maturity_years: float,
    spread: float,
    curve: Curve,
    valuation_date: date,
    cfg: Config | None = None,
) -> Bond:
    """Construct a fully-priced :class:`Bond` from its economic terms."""
    cfg = cfg or DEFAULT_CONFIG
    freq = cfg.bonds.coupon_frequency
    settlement = valuation_date + timedelta(days=cfg.bonds.settlement_offset_days)
    maturity = settlement + timedelta(days=int(round(maturity_years * 365.25)))
    # Issue a few years before settlement (purely cosmetic / for validation).
    issue = settlement - timedelta(days=int(round(min(maturity_years, 5) * 365.25)))

    # Temporary bond to generate the schedule, then price it.
    n = int(round(maturity_years * freq))
    times = np.arange(1, n + 1, dtype=float) / freq
    coupon_amt = coupon / freq * face
    amounts = np.full(n, coupon_amt, dtype=float)
    amounts[-1] += face
    cfs = dur.CashFlows(times=times, amounts=amounts)

    mv = price_on_curve(cfs, curve, spread)
    price_per_100 = mv / face * 100.0
    ytm = solve_ytm(cfs, mv, freq, cfg)

    return Bond(
        id=bond_id,
        issuer=issuer,
        issuer_type=issuer_type,
        rating=rating,
        face=face,
        coupon=coupon,
        frequency=freq,
        issue_date=issue.isoformat(),
        settlement_date=settlement.isoformat(),
        maturity_date=maturity.isoformat(),
        maturity_years=maturity_years,
        spread=spread,
        ytm=ytm,
        price=price_per_100,
        market_value=mv,
    )


# --------------------------------------------------------------------------- #
# Portfolio aggregation
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class PortfolioRisk:
    """Aggregate risk of a bond portfolio priced on a curve."""

    market_value: float
    modified_duration: float   # value-weighted
    effective_duration: float  # value-weighted
    convexity: float           # value-weighted (effective)
    dv01: float                # additive dollar DV01
    key_rate_durations: dict[float, float]
    key_rate_dv01s: dict[float, float]


def portfolio_risk(
    bonds: list[Bond], curve: Curve, cfg: Config | None = None
) -> PortfolioRisk:
    """Aggregate a list of bonds into portfolio-level risk measures.

    Interest-rate risk bumps the Treasury curve while holding each bond's credit
    spread constant (spread is passed through as a flat add-on). Durations and
    convexity are value-weighted; DV01 and key-rate DV01s are additive.
    """
    cfg = cfg or DEFAULT_CONFIG
    keys = list(cfg.risk.key_rate_tenors)

    total_mv = 0.0
    w_mod = 0.0
    w_eff = 0.0
    w_cvx = 0.0
    total_dv01 = 0.0
    krd_dollar: dict[float, float] = {k: 0.0 for k in keys}
    krdv01: dict[float, float] = {k: 0.0 for k in keys}

    for b in bonds:
        cfs = b.cashflows()
        mv = b.market_value
        total_mv += mv
        w_mod += mv * dur.modified_duration(cfs.times, cfs.amounts, b.ytm, b.frequency)
        eff = dur.effective_duration(curve, cfs.times, cfs.amounts, spread=b.spread)
        w_eff += mv * eff
        cvx = dur.effective_convexity(curve, cfs.times, cfs.amounts, spread=b.spread)
        w_cvx += mv * cvx
        total_dv01 += dur.dv01_on_curve(curve, cfs.times, cfs.amounts, spread=b.spread)
        bk = dur.key_rate_durations(curve, cfs.times, cfs.amounts, keys, spread=b.spread)
        bk01 = dur.key_rate_dv01s(curve, cfs.times, cfs.amounts, keys, spread=b.spread)
        for k in keys:
            krd_dollar[k] += mv * bk[k]
            krdv01[k] += bk01[k]

    return PortfolioRisk(
        market_value=total_mv,
        modified_duration=w_mod / total_mv,
        effective_duration=w_eff / total_mv,
        convexity=w_cvx / total_mv,
        dv01=total_dv01,
        key_rate_durations={k: krd_dollar[k] / total_mv for k in keys},
        key_rate_dv01s=krdv01,
    )


# --------------------------------------------------------------------------- #
# Seeded hypothetical portfolio generation
# --------------------------------------------------------------------------- #

#: Hypothetical issuers by type/rating. Names are invented for teaching.
_CORP_ISSUERS = {
    "AA": ["Aurora Mutual Holdings", "Northwind Capital"],
    "A": ["Keystone Industrials", "Meridian Power Co", "Granite Retail"],
    "BBB": ["Cascade Telecom", "Harbor Freight Lines", "Summit Resources"],
}


def generate_portfolio(
    curve: Curve, cfg: Config | None = None
) -> list[Bond]:
    """Generate a deterministic, seeded portfolio of 12-20 hypothetical bonds.

    The mix spans Treasuries and AA/A/BBB corporates, maturities 1-30y and mixed
    coupons. Coupons are drawn around the prevailing curve level so prices sit
    near par but not exactly at par.
    """
    cfg = cfg or DEFAULT_CONFIG
    rng = np.random.default_rng(cfg.bonds.random_seed)
    valuation_date = (
        date.fromisoformat(curve.as_of) if curve.as_of else date.today()
    )

    # (rating, maturity_years) blueprint spanning the curve. 16 bonds.
    blueprint: list[tuple[str, float]] = [
        ("UST", 1.0), ("UST", 2.0), ("UST", 5.0), ("UST", 10.0), ("UST", 30.0),
        ("AA", 3.0), ("AA", 7.0), ("AA", 20.0),
        ("A", 2.0), ("A", 5.0), ("A", 10.0), ("A", 15.0),
        ("BBB", 3.0), ("BBB", 7.0), ("BBB", 12.0), ("BBB", 25.0),
    ]

    n = len(blueprint)
    face_each = cfg.bonds.target_total_face / n
    bonds: list[Bond] = []
    corp_counters = {k: 0 for k in _CORP_ISSUERS}

    for i, (rating, mat) in enumerate(blueprint):
        spread = bps(cfg.bonds.credit_spreads_bps[rating])
        base_rate = float(curve.zero_rate(mat)) + spread
        # Coupon near the all-in yield, jittered +/-75bp, rounded to the
        # nearest 1/8 of one percent (0.00125 in decimal), floored at 0.5%.
        jitter = float(rng.uniform(-0.0075, 0.0075))
        eighth_pct = 0.00125
        coupon = max(0.005, round((base_rate + jitter) / eighth_pct) * eighth_pct)
        if rating == "UST":
            issuer = "U.S. Treasury (REAL)"
            itype = ISSUER_TYPE_TREASURY
        else:
            lst = _CORP_ISSUERS[rating]
            issuer = lst[corp_counters[rating] % len(lst)]
            corp_counters[rating] += 1
            itype = ISSUER_TYPE_CORPORATE
        bond = build_bond(
            bond_id=f"BND{i+1:03d}",
            issuer=issuer,
            issuer_type=itype,
            rating=rating,
            face=face_each,
            coupon=coupon,
            maturity_years=mat,
            spread=spread,
            curve=curve,
            valuation_date=valuation_date,
            cfg=cfg,
        )
        bonds.append(bond)
    return bonds


def portfolio_to_frame(bonds: list[Bond]) -> pd.DataFrame:
    """Serialise bonds to a DataFrame (one row per holding)."""
    rows = [asdict(b) for b in bonds]
    df = pd.DataFrame(rows)
    # Round economic figures for a tidy CSV while keeping full precision cols.
    df["price"] = df["price"].round(6)
    df["market_value"] = df["market_value"].round(2)
    df["ytm"] = df["ytm"].round(8)
    return df


def write_holdings_csv(
    bonds: list[Bond], cfg: Config | None = None
) -> "object":
    """Write the holdings to ``data/holdings_hypothetical.csv``."""
    cfg = cfg or DEFAULT_CONFIG
    cfg.paths.data.mkdir(parents=True, exist_ok=True)
    df = portfolio_to_frame(bonds)
    df.to_csv(cfg.paths.holdings_csv, index=False)
    return cfg.paths.holdings_csv


def load_holdings(cfg: Config | None = None) -> list[Bond]:
    """Load holdings back from the hypothetical CSV into :class:`Bond` objects."""
    cfg = cfg or DEFAULT_CONFIG
    df = pd.read_csv(cfg.paths.holdings_csv)
    return [Bond(**{k: row[k] for k in Bond.__dataclass_fields__}) for _, row in df.iterrows()]
