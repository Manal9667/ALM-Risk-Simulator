"""Hypothetical liability schedule for a life insurer (educational).

The entire liability schedule here is HYPOTHETICAL and labelled as such. It is a
stylised stand-in for the obligations of a life insurance company, not real
policy data.

Shape of the schedule (and why)
-------------------------------
Expected annual net outflows run from year 1 to ``horizon_years`` (~40) and are
the sum of three stylised components, each motivated by a real driver:

1. **Annuity payouts** - a near-level stream that slowly declines as annuitants
   die off:  ``annuity_level · (1 - annuity_decay_per_year)^(year-1)``.
2. **Death benefits** - a hump peaking in the medium term (policies written to a
   middle-aged book mature into claims):
   ``death_benefit_weight · exp(-½·((year - peak)/width)²)``.
3. **Long-dated tail** - a thin, rising stream in the back end for the youngest
   cohorts / whole-life obligations, beginning at ``tail_start_year``.

The raw shape is unit-free; it is then **scaled** so the liability present value
makes the starting funding ratio land near ``funding_target_mid`` (assets ~=
105-110% of liabilities). That calibration is an explicit modelling assumption,
stated in the output.

Discounting
-----------
Liabilities are discounted on the REAL Treasury zero curve plus a configurable
**liability spread** (default 25bps). Why this matters: the spread embeds an
illiquidity / own-credit view. A higher spread lowers liability PV and raises
the funding ratio, and it shortens measured liability duration slightly. Because
insurer liabilities are long, the choice of discount basis is one of the most
consequential assumptions in ALM, so it is isolated and configurable here.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import brentq

from .config import Config, DEFAULT_CONFIG, bps
from .curve import Curve
from . import duration as dur


@dataclass(frozen=True)
class LiabilityResult:
    """Priced liability schedule and its aggregate risk measures."""

    years: np.ndarray
    cash_flows: np.ndarray
    discount_factors: np.ndarray
    present_values: np.ndarray
    duration_contributions: np.ndarray   # t_i · PV_i / Σ PV_i (years)

    total_pv: float
    liability_spread: float
    scale_factor: float

    irr: float                      # single annual yield reproducing total_pv
    macaulay_duration: float        # analytic, at the IRR (annual compounding)
    modified_duration: float        # analytic, = Macaulay/(1+irr)
    convexity: float                # analytic, at the IRR
    effective_duration: float       # curve bump-and-reprice
    effective_convexity: float      # curve bump-and-reprice
    dv01: float                     # curve-based dollar DV01

    def to_frame(self) -> pd.DataFrame:
        """Per-cash-flow table (one row per year)."""
        return pd.DataFrame(
            {
                "year": self.years.astype(int),
                "expected_cash_flow_hypothetical": self.cash_flows.round(2),
                "discount_factor": self.discount_factors.round(8),
                "present_value": self.present_values.round(2),
                "duration_contribution_years": self.duration_contributions.round(8),
            }
        )


def raw_liability_shape(cfg: Config | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(years, raw_cash_flows)`` for the unit-scaled liability shape."""
    cfg = cfg or DEFAULT_CONFIG
    lc = cfg.liabilities
    years = np.arange(1, lc.horizon_years + 1, dtype=float)

    annuity = lc.annuity_level * (1.0 - lc.annuity_decay_per_year) ** (years - 1.0)
    death = lc.death_benefit_weight * np.exp(
        -0.5 * ((years - lc.death_benefit_peak_year) / lc.death_benefit_width) ** 2
    )
    tail = np.where(
        years >= lc.tail_start_year,
        lc.tail_weight
        * (years - lc.tail_start_year + 1.0)
        / (lc.horizon_years - lc.tail_start_year + 1.0),
        0.0,
    )
    raw = annuity + death + tail
    return years, raw


def price_liabilities(
    curve: Curve,
    target_pv: float | None = None,
    asset_value: float | None = None,
    cfg: Config | None = None,
) -> LiabilityResult:
    """Build, scale and price the hypothetical liability schedule.

    Exactly one calibration target should be supplied:

    * ``target_pv`` - scale so total PV equals this value, or
    * ``asset_value`` - scale so the funding ratio equals
      ``funding_target_mid`` (i.e. ``target_pv = asset_value / funding_mid``).

    If neither is given the raw (unit) shape is priced as-is.
    """
    cfg = cfg or DEFAULT_CONFIG
    spread = bps(cfg.liabilities.liability_spread_bps)
    years, raw = raw_liability_shape(cfg)

    # Discount factors on the Treasury curve shifted by the liability spread.
    disc_curve = curve.parallel_shift(spread)
    dfs = np.asarray(disc_curve.discount_factor(years), dtype=float)
    raw_pv = float(np.sum(raw * dfs))

    if target_pv is None and asset_value is not None:
        target_pv = asset_value / cfg.liabilities.funding_target_mid
    if target_pv is not None:
        scale = target_pv / raw_pv
    else:
        scale = 1.0

    cash_flows = raw * scale
    present_values = cash_flows * dfs
    total_pv = float(np.sum(present_values))
    duration_contrib = years * present_values / total_pv

    # Analytic measures at the single annual IRR reproducing total_pv.
    def pv_minus_target(y: float) -> float:
        return dur.price_at_yield(years, cash_flows, y, freq=1) - total_pv

    irr = float(brentq(pv_minus_target, cfg.risk.ytm_lower, cfg.risk.ytm_upper,
                       xtol=cfg.risk.ytm_tolerance, rtol=1e-14))
    mac = dur.macaulay_duration(years, cash_flows, irr, freq=1)
    mod = dur.modified_duration(years, cash_flows, irr, freq=1)
    cvx = dur.convexity_analytic(years, cash_flows, irr, freq=1)

    # Curve-based (effective) measures: bump the Treasury curve, spread constant.
    eff_dur = dur.effective_duration(curve, years, cash_flows, spread=spread)
    eff_cvx = dur.effective_convexity(curve, years, cash_flows, spread=spread)
    dv01 = dur.dv01_on_curve(curve, years, cash_flows, spread=spread)

    return LiabilityResult(
        years=years,
        cash_flows=cash_flows,
        discount_factors=dfs,
        present_values=present_values,
        duration_contributions=duration_contrib,
        total_pv=total_pv,
        liability_spread=spread,
        scale_factor=scale,
        irr=irr,
        macaulay_duration=mac,
        modified_duration=mod,
        convexity=cvx,
        effective_duration=eff_dur,
        effective_convexity=eff_cvx,
        dv01=dv01,
    )


def write_liabilities_csv(
    result: LiabilityResult, cfg: Config | None = None
) -> "object":
    """Write the per-year liability schedule to ``data/liabilities_hypothetical.csv``."""
    cfg = cfg or DEFAULT_CONFIG
    cfg.paths.data.mkdir(parents=True, exist_ok=True)
    result.to_frame().to_csv(cfg.paths.liabilities_csv, index=False)
    return cfg.paths.liabilities_csv
