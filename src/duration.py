"""Duration, convexity, DV01, key-rate durations and ALM metrics (educational).

Everything here operates on a generic set of cash flows represented as parallel
arrays of ``times`` (years from settlement) and ``amounts`` (cash, same sign
convention as the caller). Bonds and liabilities both reduce to such cash flows,
so the same risk machinery serves both sides of the balance sheet.

Two families of measures are provided:

* **Single-yield (analytic)** measures take a yield ``y`` compounded ``m`` times
  per year. These have closed forms and are what the unit tests check against
  finite differences.
* **Curve-based (effective)** measures reprice the cash flows on a shocked
  :class:`~src.curve.Curve` ("bump and reprice"). These capture the actual term
  structure and are used for portfolio and liability risk and for ALM.

Formulae (all stated in the relevant docstrings):

* Price at yield:        P = Σ CF_i / (1 + y/m)^(m·t_i)
* Macaulay duration:     D_mac = Σ t_i·PV_i / Σ PV_i
* Modified duration:     D_mod = D_mac / (1 + y/m)
* Analytic convexity:    C = (1 / (P·m²)) · Σ CF_i·n_i·(n_i+1)·(1+y/m)^(−n_i−2),
                         with n_i = m·t_i
* DV01:                  DV01 = D_mod · P · 1e-4   (dollar value of 1 basis point)
* Effective duration:    D_eff = (P(−Δy) − P(+Δy)) / (2·P₀·Δy)
* Effective convexity:   C_eff = (P(+Δy) + P(−Δy) − 2·P₀) / (P₀·Δy²)
* Taylor approximation:  ΔV ≈ −D_mod·V·Δy + ½·C·V·Δy²

Key-rate durations use tent-shaped (piecewise-linear partition-of-unity) bumps
anchored at the key tenors, so they sum back to the effective (parallel)
duration up to interpolation error.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np

from .config import DEFAULT_CONFIG, RiskConfig
from .curve import Curve


@dataclass(frozen=True)
class CashFlows:
    """A set of dated cash flows."""

    times: np.ndarray     # years from settlement, strictly positive
    amounts: np.ndarray   # cash amounts (caller's sign convention)

    def __post_init__(self) -> None:
        t = np.asarray(self.times, dtype=float)
        a = np.asarray(self.amounts, dtype=float)
        if t.shape != a.shape:
            raise ValueError("times and amounts must have the same shape")
        object.__setattr__(self, "times", t)
        object.__setattr__(self, "amounts", a)


# --------------------------------------------------------------------------- #
# Single-yield (analytic) measures
# --------------------------------------------------------------------------- #

def price_at_yield(
    times: np.ndarray, amounts: np.ndarray, ytm: float, freq: int
) -> float:
    """Present value of cash flows discounted at a flat yield.

    ``P = Σ CF_i / (1 + ytm/freq) ** (freq · t_i)``.
    """
    t = np.asarray(times, dtype=float)
    cf = np.asarray(amounts, dtype=float)
    k = 1.0 + ytm / freq
    return float(np.sum(cf * k ** (-freq * t)))


def macaulay_duration(
    times: np.ndarray, amounts: np.ndarray, ytm: float, freq: int
) -> float:
    """Macaulay duration (years): ``Σ t_i·PV_i / Σ PV_i`` at flat yield ``ytm``."""
    t = np.asarray(times, dtype=float)
    cf = np.asarray(amounts, dtype=float)
    k = 1.0 + ytm / freq
    pv = cf * k ** (-freq * t)
    total = np.sum(pv)
    if total == 0:
        raise ZeroDivisionError("zero present value; duration undefined")
    return float(np.sum(t * pv) / total)


def modified_duration(
    times: np.ndarray, amounts: np.ndarray, ytm: float, freq: int
) -> float:
    """Modified duration: ``D_mac / (1 + ytm/freq)``."""
    return macaulay_duration(times, amounts, ytm, freq) / (1.0 + ytm / freq)


def convexity_analytic(
    times: np.ndarray, amounts: np.ndarray, ytm: float, freq: int
) -> float:
    """Analytic convexity (per unit yield^2).

    ``C = (1/(P·m²)) · Σ CF_i·n_i·(n_i+1)·(1+y/m)^(−n_i−2)`` with ``n_i = m·t_i``.
    """
    t = np.asarray(times, dtype=float)
    cf = np.asarray(amounts, dtype=float)
    m = freq
    k = 1.0 + ytm / m
    n = m * t
    price = price_at_yield(t, cf, ytm, m)
    terms = cf * n * (n + 1.0) * k ** (-n - 2.0)
    return float(np.sum(terms) / (price * m * m))


def dv01_analytic(
    times: np.ndarray, amounts: np.ndarray, ytm: float, freq: int,
    bump: float | None = None,
) -> float:
    """DV01 = ``D_mod · P · 1e-4`` (dollar price change for a 1bp yield move)."""
    bp = bump if bump is not None else DEFAULT_CONFIG.risk.dv01_bump
    p = price_at_yield(times, amounts, ytm, freq)
    d_mod = modified_duration(times, amounts, ytm, freq)
    return float(d_mod * p * bp)


def taylor_price_change(
    value: float, modified_dur: float, convexity: float, dy: float
) -> float:
    """Second-order Taylor estimate of a price change.

    ``ΔV ≈ −D_mod·V·Δy + ½·C·V·Δy²``.
    """
    return -modified_dur * value * dy + 0.5 * convexity * value * dy * dy


# --------------------------------------------------------------------------- #
# Curve-based (effective) measures: "bump and reprice"
# --------------------------------------------------------------------------- #

def present_value_on_curve(
    curve: Curve, times: np.ndarray, amounts: np.ndarray,
    spread: float = 0.0,
) -> float:
    """PV of cash flows on a zero curve, optionally plus a flat ``spread``.

    ``PV = Σ CF_i · DF(t_i)`` where ``DF`` is taken from a spread-shifted copy of
    the curve (the original curve is never mutated).
    """
    disc_curve = curve.parallel_shift(spread) if spread else curve
    t = np.asarray(times, dtype=float)
    cf = np.asarray(amounts, dtype=float)
    df = np.asarray(disc_curve.discount_factor(t), dtype=float)
    return float(np.sum(cf * df))


def effective_duration(
    curve: Curve, times: np.ndarray, amounts: np.ndarray,
    bump: float | None = None, spread: float = 0.0,
) -> float:
    """Effective duration via parallel bump-and-reprice.

    ``D_eff = (P(−Δy) − P(+Δy)) / (2·P₀·Δy)``.
    """
    dy = bump if bump is not None else DEFAULT_CONFIG.risk.effective_bump
    p0 = present_value_on_curve(curve, times, amounts, spread)
    p_up = present_value_on_curve(curve.parallel_shift(dy), times, amounts, spread)
    p_dn = present_value_on_curve(curve.parallel_shift(-dy), times, amounts, spread)
    return float((p_dn - p_up) / (2.0 * p0 * dy))


def effective_convexity(
    curve: Curve, times: np.ndarray, amounts: np.ndarray,
    bump: float | None = None, spread: float = 0.0,
) -> float:
    """Effective convexity via parallel bump-and-reprice.

    ``C_eff = (P(+Δy) + P(−Δy) − 2·P₀) / (P₀·Δy²)``.
    """
    dy = bump if bump is not None else DEFAULT_CONFIG.risk.effective_bump
    p0 = present_value_on_curve(curve, times, amounts, spread)
    p_up = present_value_on_curve(curve.parallel_shift(dy), times, amounts, spread)
    p_dn = present_value_on_curve(curve.parallel_shift(-dy), times, amounts, spread)
    return float((p_up + p_dn - 2.0 * p0) / (p0 * dy * dy))


def dv01_on_curve(
    curve: Curve, times: np.ndarray, amounts: np.ndarray,
    bump: float | None = None, spread: float = 0.0,
) -> float:
    """DV01 from the curve: half the symmetric reprice difference for a 1bp move.

    ``DV01 = (P(−Δy) − P(+Δy)) / 2`` with ``Δy`` = 1 basis point. Positive for a
    long position (price rises as yields fall).
    """
    dy = bump if bump is not None else DEFAULT_CONFIG.risk.dv01_bump
    p_up = present_value_on_curve(curve.parallel_shift(dy), times, amounts, spread)
    p_dn = present_value_on_curve(curve.parallel_shift(-dy), times, amounts, spread)
    return float((p_dn - p_up) / 2.0)


# --------------------------------------------------------------------------- #
# Key-rate durations (tent / partition-of-unity bumps)
# --------------------------------------------------------------------------- #

def _tent_shift(key_tenors: Sequence[float], j: int) -> "callable":
    """Return ``f(t) -> bps`` for a unit (1bp) tent anchored at ``key_tenors[j]``.

    The tent is 1 at ``key_tenors[j]`` and falls linearly to 0 at the adjacent
    key tenors. For the first/last key the tent is held flat (=1) below/above
    that key. Across all j these tents sum to 1 for every ``t`` in the key range,
    i.e. a partition of unity, so the key-rate durations sum to the parallel
    (effective) duration up to interpolation error.
    """
    keys = list(key_tenors)
    k = keys[j]
    left = keys[j - 1] if j > 0 else None
    right = keys[j + 1] if j < len(keys) - 1 else None

    def f(t: float) -> float:
        if t <= k:
            if left is None:
                return 1.0            # flat below first key
            if t <= left:
                return 0.0
            return (t - left) / (k - left)
        else:
            if right is None:
                return 1.0            # flat above last key
            if t >= right:
                return 0.0
            return (right - t) / (right - k)

    return f


def key_rate_durations(
    curve: Curve, times: np.ndarray, amounts: np.ndarray,
    key_tenors: Sequence[float] | None = None,
    bump: float | None = None, spread: float = 0.0,
) -> dict[float, float]:
    """Key-rate durations at each key tenor via tent bump-and-reprice.

    For each key ``k``: ``KRD_k = (P(−tent_k) − P(+tent_k)) / (2·P₀·Δy)`` where
    ``tent_k`` is a 1bp tent (see :func:`_tent_shift`) and ``Δy`` is 1bp.
    """
    keys = list(key_tenors) if key_tenors is not None else list(
        DEFAULT_CONFIG.risk.key_rate_tenors
    )
    dy = bump if bump is not None else DEFAULT_CONFIG.risk.effective_bump
    bump_bps = dy * 10_000.0
    p0 = present_value_on_curve(curve, times, amounts, spread)

    out: dict[float, float] = {}
    for j, k in enumerate(keys):
        base = _tent_shift(keys, j)

        def up(t: float, _b=base) -> float:
            return bump_bps * _b(t)

        def dn(t: float, _b=base) -> float:
            return -bump_bps * _b(t)

        p_up = present_value_on_curve(curve.shift(up), times, amounts, spread)
        p_dn = present_value_on_curve(curve.shift(dn), times, amounts, spread)
        out[float(k)] = float((p_dn - p_up) / (2.0 * p0 * dy))
    return out


def key_rate_dv01s(
    curve: Curve, times: np.ndarray, amounts: np.ndarray,
    key_tenors: Sequence[float] | None = None,
    bump: float | None = None, spread: float = 0.0,
) -> dict[float, float]:
    """Key-rate DV01s: dollar sensitivity to a 1bp tent bump at each key tenor."""
    keys = list(key_tenors) if key_tenors is not None else list(
        DEFAULT_CONFIG.risk.key_rate_tenors
    )
    dy = bump if bump is not None else DEFAULT_CONFIG.risk.dv01_bump
    bump_bps = dy * 10_000.0
    out: dict[float, float] = {}
    for j, k in enumerate(keys):
        base = _tent_shift(keys, j)

        def up(t: float, _b=base) -> float:
            return bump_bps * _b(t)

        def dn(t: float, _b=base) -> float:
            return -bump_bps * _b(t)

        p_up = present_value_on_curve(curve.shift(up), times, amounts, spread)
        p_dn = present_value_on_curve(curve.shift(dn), times, amounts, spread)
        out[float(k)] = float((p_dn - p_up) / 2.0)
    return out


# --------------------------------------------------------------------------- #
# ALM balance-sheet metrics
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ALMMetrics:
    """Container for base-case asset-liability management metrics."""

    asset_value: float
    liability_value: float
    surplus: float
    funding_ratio: float
    asset_duration: float
    liability_duration: float
    duration_gap: float
    leverage_adjusted_gap: float
    asset_dv01: float | None = None
    liability_dv01: float | None = None
    net_dv01: float | None = None

    def as_dict(self) -> dict[str, float | None]:
        return self.__dict__.copy()


def alm_metrics(
    asset_value: float,
    liability_value: float,
    asset_duration: float,
    liability_duration: float,
    asset_dv01: float | None = None,
    liability_dv01: float | None = None,
) -> ALMMetrics:
    """Compute core ALM metrics so Phase 2 scenario code can reuse them.

    Definitions
    -----------
    * funding_ratio          = A / L
    * surplus                = A − L
    * duration_gap           = D_A − D_L
    * leverage_adjusted_gap  = D_A − (L/A)·D_L

    The leverage-adjusted gap measures surplus sensitivity: it weights the
    liability duration by the liability-to-asset ratio, so it reflects how the
    *net* position (not just raw durations) responds to a parallel rate move.
    """
    if asset_value == 0:
        raise ZeroDivisionError("asset_value is zero; ratios undefined")
    funding_ratio = asset_value / liability_value if liability_value else float("inf")
    surplus = asset_value - liability_value
    duration_gap = asset_duration - liability_duration
    lev_adj_gap = asset_duration - (liability_value / asset_value) * liability_duration
    net_dv01 = None
    if asset_dv01 is not None and liability_dv01 is not None:
        net_dv01 = asset_dv01 - liability_dv01
    return ALMMetrics(
        asset_value=asset_value,
        liability_value=liability_value,
        surplus=surplus,
        funding_ratio=funding_ratio,
        asset_duration=asset_duration,
        liability_duration=liability_duration,
        duration_gap=duration_gap,
        leverage_adjusted_gap=lev_adj_gap,
        asset_dv01=asset_dv01,
        liability_dv01=liability_dv01,
        net_dv01=net_dv01,
    )
