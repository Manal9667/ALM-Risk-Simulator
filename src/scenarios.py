"""Rate-shock scenarios, key-rate hedging and model-risk sensitivities (educational).

This module answers the question the base case cannot: *what happens to the
balance sheet when the curve moves?*

Design rule: **liability cash flows are frozen at their base-case values** and
re-discounted on each shocked curve. The base case scales the raw liability
shape so the funding ratio starts at ``funding_target_mid``; that calibration is
a one-off. Re-calibrating on a shocked curve would silently pin the funding ratio
and hide all of the interest-rate risk, so it is never done here.

Contents
--------
* :class:`BalanceSheet`  - frozen assets + liabilities that can be revalued on any curve
* :func:`standard_scenarios`, :func:`run_scenarios` - parallel and non-parallel shocks
* :func:`solve_key_rate_hedge` - zero-cost Treasury-strip overlay that zeroes net key-rate DV01
* :func:`spread_sensitivity`, :func:`interpolation_sensitivity` - model-risk checks
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Mapping

import numpy as np
import pandas as pd

from . import bonds as bonds_mod
from . import duration as dur
from . import liabilities as liab_mod
from .config import Config, DEFAULT_CONFIG, bps
from .curve import Curve

ShockFn = Callable[[float], float]  # tenor (yrs) -> shift in basis points


# --------------------------------------------------------------------------- #
# Hedge overlay
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Hedge:
    """Zero-cost key-rate overlay built from zero-coupon Treasury exposures.

    Each position is a long (or short) zero-coupon Treasury at a key tenor,
    financed by borrowing (or lending) its market value at a rate-insensitive
    short-term funding rate. The overlay therefore has **zero value on day one**
    (funding ratio unchanged) and its P&L is ``amount * (DF_shocked - DF_base)``.
    This is how insurers hedge in practice: receive-fixed / pay-fixed swaps and
    Treasury futures, with a Treasury strip as the clean educational proxy.

    ``strip_mv`` is the base-curve market value of each position (negative =
    short); ``strip_amount`` is the corresponding face amount.
    """

    strip_mv: Mapping[float, float]
    strip_amount: Mapping[float, float]

    @property
    def gross_notional_mv(self) -> float:
        return float(sum(abs(v) for v in self.strip_mv.values()))


# --------------------------------------------------------------------------- #
# Balance sheet
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class BalanceSheet:
    """Assets and liabilities that can be revalued on any zero curve.

    Liability cash flows are fixed; only the discount curve changes.
    """

    book: tuple                       # tuple[bonds.Bond, ...]
    liab_years: np.ndarray
    liab_cash_flows: np.ndarray
    liab_spread: float
    key_tenors: tuple[float, ...]
    hedge: Hedge | None = None

    # -- construction ------------------------------------------------------- #
    @classmethod
    def from_base_case(cls, base, cfg: Config | None = None) -> "BalanceSheet":
        cfg = cfg or DEFAULT_CONFIG
        return cls(
            book=tuple(base.bonds),
            liab_years=np.asarray(base.liabilities.years, dtype=float),
            liab_cash_flows=np.asarray(base.liabilities.cash_flows, dtype=float),
            liab_spread=float(base.liabilities.liability_spread),
            key_tenors=tuple(cfg.risk.key_rate_tenors),
        )

    def with_hedge(self, hedge: Hedge | None) -> "BalanceSheet":
        return replace(self, hedge=hedge)

    # -- valuation ----------------------------------------------------------- #
    def _book_value(self, curve: Curve) -> float:
        return float(sum(
            bonds_mod.price_on_curve(b.cashflows(), curve, b.spread)
            for b in self.book
        ))

    def asset_value(self, curve: Curve) -> float:
        pv = self._book_value(curve)
        if self.hedge is None:
            return pv
        # Overlay P&L: strip value on this curve less the (borrowed) base value.
        overlay = sum(
            amt * float(curve.discount_factor(t)) - self.hedge.strip_mv[t]
            for t, amt in self.hedge.strip_amount.items()
        )
        return pv + float(overlay)

    def liability_value(self, curve: Curve) -> float:
        return dur.present_value_on_curve(
            curve, self.liab_years, self.liab_cash_flows, spread=self.liab_spread
        )

    def surplus(self, curve: Curve) -> float:
        return self.asset_value(curve) - self.liability_value(curve)

    def funding_ratio(self, curve: Curve) -> float:
        return self.asset_value(curve) / self.liability_value(curve)

    # -- risk ------------------------------------------------------------------ #
    def asset_krd_dv01(self, curve: Curve) -> dict[float, float]:
        total = {k: 0.0 for k in self.key_tenors}
        for b in self.book:
            cf = b.cashflows()
            d = dur.key_rate_dv01s(
                curve, cf.times, cf.amounts, self.key_tenors, spread=b.spread
            )
            for k, v in d.items():
                total[k] += v
        out = dict(total)
        if self.hedge:
            for t, amt in self.hedge.strip_amount.items():
                d = dur.key_rate_dv01s(
                    curve, np.array([t]), np.array([amt]), self.key_tenors
                )
                for k, v in d.items():
                    out[k] += v
        return out

    def liability_krd_dv01(self, curve: Curve) -> dict[float, float]:
        return dur.key_rate_dv01s(
            curve, self.liab_years, self.liab_cash_flows, self.key_tenors,
            spread=self.liab_spread,
        )

    def net_krd_dv01(self, curve: Curve) -> dict[float, float]:
        a, l = self.asset_krd_dv01(curve), self.liability_krd_dv01(curve)
        return {k: a[k] - l[k] for k in self.key_tenors}

    def net_dv01(self, curve: Curve) -> float:
        """Parallel DV01 of surplus (positive = surplus gains when rates fall)."""
        dy = DEFAULT_CONFIG.risk.dv01_bump
        up, dn = curve.parallel_shift(dy), curve.parallel_shift(-dy)
        return (self.surplus(dn) - self.surplus(up)) / 2.0


# --------------------------------------------------------------------------- #
# Scenarios
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Scenario:
    name: str
    shift_bps: ShockFn
    parallel_bps: float | None = None   # set for parallel shocks (enables Taylor)


def parallel(bp: float) -> Scenario:
    sign = "+" if bp >= 0 else "-"
    return Scenario(f"Parallel {sign}{abs(bp):.0f}bp", lambda t, b=bp: b, bp)


def steepener(slope_bp_per_yr: float = 2.5, pivot: float = 10.0,
              cap: float = 50.0) -> Scenario:
    """Short end down, long end up around ``pivot`` (capped at +/-``cap`` bp)."""
    def f(t: float) -> float:
        return float(np.clip(slope_bp_per_yr * (t - pivot), -cap, cap))
    return Scenario("Steepener (2y -20bp / 30y +50bp)", f)


def flattener(slope_bp_per_yr: float = 2.5, pivot: float = 10.0,
              cap: float = 50.0) -> Scenario:
    s = steepener(slope_bp_per_yr, pivot, cap)
    return Scenario("Flattener (2y +20bp / 30y -50bp)", lambda t: -s.shift_bps(t))


def long_end_rally(bp: float = -100.0) -> Scenario:
    """Only the 20y+ sector moves: the textbook liability-driven shock."""
    def f(t: float) -> float:
        return bp * float(np.clip((t - 10.0) / 10.0, 0.0, 1.0))
    return Scenario(f"Long-end rally ({bp:+.0f}bp at 20y+)", f)


def standard_scenarios() -> list[Scenario]:
    return [
        parallel(-200), parallel(-100), parallel(-50),
        parallel(50), parallel(100), parallel(200),
        steepener(), flattener(), long_end_rally(-100.0),
    ]


def apply_scenario(curve: Curve, sc: Scenario) -> Curve:
    return curve.shift(sc.shift_bps)


def run_scenarios(
    sheet: BalanceSheet, curve: Curve, scenarios: list[Scenario] | None = None,
) -> pd.DataFrame:
    """Full-revaluation results for each scenario.

    ``taylor_d_surplus`` (parallel shocks only) is the second-order estimate
    built from the surplus's own dollar duration and convexity, so the gap to
    ``d_surplus`` is the higher-order residual.
    """
    scenarios = scenarios or standard_scenarios()
    a0, l0 = sheet.asset_value(curve), sheet.liability_value(curve)
    s0 = a0 - l0
    # Dollar duration / convexity of surplus from a modest symmetric bump.
    h = 10.0  # bp
    s_up = sheet.surplus(curve.parallel_shift(bps(h)))
    s_dn = sheet.surplus(curve.parallel_shift(-bps(h)))
    dollar_dur = (s_dn - s_up) / (2.0 * bps(h))             # -dS/dy
    dollar_cvx = (s_up + s_dn - 2.0 * s0) / bps(h) ** 2     # d2S/dy2

    rows = []
    for sc in scenarios:
        c = apply_scenario(curve, sc)
        a, l = sheet.asset_value(c), sheet.liability_value(c)
        row = {
            "scenario": sc.name,
            "assets": a,
            "liabilities": l,
            "surplus": a - l,
            "funding_ratio": a / l,
            "d_surplus": (a - l) - s0,
            "taylor_d_surplus": np.nan,
        }
        if sc.parallel_bps is not None:
            dy = bps(sc.parallel_bps)
            row["taylor_d_surplus"] = -dollar_dur * dy + 0.5 * dollar_cvx * dy * dy
        rows.append(row)
    return pd.DataFrame(rows)


def funding_ratio_curve(
    sheet: BalanceSheet, curve: Curve, shocks_bp: np.ndarray | None = None,
) -> pd.DataFrame:
    """Funding ratio across a grid of parallel shocks (for charts)."""
    grid = np.arange(-200, 201, 25) if shocks_bp is None else np.asarray(shocks_bp)
    return pd.DataFrame({
        "shock_bp": grid,
        "funding_ratio": [
            sheet.funding_ratio(curve.parallel_shift(bps(float(b)))) for b in grid
        ],
    })


# --------------------------------------------------------------------------- #
# Key-rate hedge
# --------------------------------------------------------------------------- #

def solve_key_rate_hedge(sheet: BalanceSheet, curve: Curve) -> Hedge:
    """Zero-cost overlay that sets net key-rate DV01 to zero at every key tenor.

    Unknowns ``w_j`` are base market values held in a zero-coupon Treasury at key
    tenor ``j`` (negative = short). Matching each key-rate DV01 gives

        Σ_j w_j · d_jk  =  L_k − A_k            for each key tenor k

    where ``A_k`` / ``L_k`` are asset / liability key-rate DV01s and ``d_jk`` is
    the key-rate DV01 per dollar of strip ``j``. Because strips sit exactly on the
    key tenors the matrix is diagonal, so the solve is exact and well conditioned.

    Matching every key-rate DV01 also matches parallel DV01 (their sum). What the
    overlay cannot remove is **convexity** and risk *between* key tenors; the
    scenario table shows that residual honestly.
    """
    if sheet.hedge is not None:
        raise ValueError("sheet already carries a hedge; solve on the unhedged sheet")
    keys = list(sheet.key_tenors)
    a = sheet.asset_krd_dv01(curve)
    l = sheet.liability_krd_dv01(curve)

    n = len(keys)
    d = np.zeros((n, n))
    for j, tj in enumerate(keys):
        amt_per_dollar = 1.0 / float(curve.discount_factor(tj))
        kd = dur.key_rate_dv01s(
            curve, np.array([tj]), np.array([amt_per_dollar]), keys
        )
        for k_idx, k in enumerate(keys):
            d[j, k_idx] = kd[k]

    target = np.array([l[k] - a[k] for k in keys])
    w = np.linalg.solve(d.T, target)

    return Hedge(
        strip_mv={float(k): float(w[i]) for i, k in enumerate(keys)},
        strip_amount={
            float(k): float(w[i] / curve.discount_factor(k)) for i, k in enumerate(keys)
        },
    )


# --------------------------------------------------------------------------- #
# Model-risk sensitivities
# --------------------------------------------------------------------------- #

def spread_sensitivity(
    curve: Curve, asset_value: float, spreads_bps: tuple[float, ...] = (0, 25, 50, 100),
    cfg: Config | None = None,
) -> pd.DataFrame:
    """How the liability discount spread changes duration and net DV01.

    Liabilities are re-scaled to the target funding ratio for each spread, so the
    base funding ratio is held constant and the comparison isolates the *risk*
    effect of the discount-basis assumption.
    """
    cfg = cfg or DEFAULT_CONFIG
    rows = []
    # Asset risk is spread-independent; take it from a fresh portfolio on the curve.
    book = bonds_mod.generate_portfolio(curve, cfg)
    pf = bonds_mod.portfolio_risk(book, curve, cfg)
    for s in spreads_bps:
        c = replace(cfg, liabilities=replace(cfg.liabilities, liability_spread_bps=float(s)))
        lb = liab_mod.price_liabilities(curve, asset_value=pf.market_value, cfg=c)
        rows.append({
            "liability_spread_bps": s,
            "liability_duration": lb.effective_duration,
            "duration_gap": pf.effective_duration - lb.effective_duration,
            "net_dv01": pf.dv01 - lb.dv01,
        })
    return pd.DataFrame(rows)


def _log_df_interp(curve: Curve, t: np.ndarray) -> np.ndarray:
    """Discount factors by log-linear interpolation of the pillar DFs.

    Flat continuously-compounded zero rate is used outside the pillar range.
    """
    ten = np.asarray(curve.tenors, dtype=float)
    ln_df = np.log(np.asarray(curve.discount_factor(ten), dtype=float))
    out = np.interp(t, ten, ln_df)
    lo, hi = t < ten[0], t > ten[-1]
    out = np.where(lo, ln_df[0] * t / ten[0], out)
    out = np.where(hi, ln_df[-1] * t / ten[-1], out)
    return np.exp(out)


def interpolation_sensitivity(sheet: BalanceSheet, curve: Curve) -> dict[str, float]:
    """Compare liability PV under linear-on-zeros vs log-linear-on-DF interpolation.

    Both use the same pillar zero rates. The gap is pure interpolation (model)
    risk in the base-case valuation.
    """
    base_pv = sheet.liability_value(curve)
    t = sheet.liab_years
    # Apply the liability spread as a parallel zero shift, then compare DFs.
    shifted = curve.parallel_shift(sheet.liab_spread)
    alt_pv = float(np.sum(sheet.liab_cash_flows * _log_df_interp(shifted, t)))
    return {
        "linear_zero_pv": base_pv,
        "log_linear_df_pv": alt_pv,
        "difference_pct": (alt_pv / base_pv - 1.0) * 100.0,
        "difference_bps_of_pv": (alt_pv / base_pv - 1.0) * 1e4,
    }
