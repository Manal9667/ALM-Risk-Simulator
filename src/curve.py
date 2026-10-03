"""Zero-coupon yield curve construction and manipulation (educational).

This module turns a set of U.S. Treasury *par* yields into a *zero* (spot) curve
by bootstrapping, then exposes discounting, interpolation and shock operations
used by the rest of the simulator.

Conventions and method (read before trusting the numbers)
---------------------------------------------------------
* **Compounding.** Zero rates are stored and interpolated as *annually
  compounded* rates ``z``. The discount factor for time ``t`` (in years) is

      DF(t) = (1 + z(t)) ** (-t).

* **Interpolation.** We interpolate **linearly on the zero rates** against time.
  This is a deliberate, documented choice: it is simple, monotone-friendly and
  adequate for an educational model. (Linear-on-zeros is not arbitrage-free in
  the strict forward sense; a production system might interpolate on
  log-discount factors or use a monotone-convex scheme.) Outside the pillar
  range we extrapolate flat (hold the nearest pillar's zero rate).

* **Bootstrap.** Par yields are converted to zero rates one pillar at a time in
  increasing maturity:

    - **Short end** (tenor < ``CurveConfig.par_bond_min_tenor_years``, default
      2y): the FRED series DGS1MO ... DGS1 are treated as *zero-coupon
      money-market quotes*. We take the quoted yield directly as the annually
      compounded zero rate at that tenor. This conflates the quoted
      (bond-equivalent / money-market) convention with our annual compounding;
      it is an acceptable simplification at the short end and is the documented
      limitation referred to in the spec.

    - **Coupon end** (tenor >= 2y): each series is treated as a par bond priced
      at 100 paying semiannual coupons equal to the par yield. We solve for the
      zero rate at the pillar maturity such that the bond reprices to par, using
      already-bootstrapped zeros (linearly interpolated) for the intermediate
      coupon dates. By construction the bootstrap reprices the input par
      instruments to par (see tests).

Limits: idealised year fractions (no real day-count/holiday calendar), flat
extrapolation, and the short-end simplification above. This is an educational
tool, not a production curve.

All shock operations (`parallel_shift`, `key_rate_bump`, `shift`) return **new**
``Curve`` objects. The underlying arrays are marked read-only so a curve can be
shared safely.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence

import numpy as np
from scipy.optimize import brentq

from .config import CurveConfig, DEFAULT_CONFIG


def _readonly(arr: np.ndarray) -> np.ndarray:
    """Return a float array copy that cannot be mutated in place."""
    out = np.array(arr, dtype=float).copy()
    out.flags.writeable = False
    return out


@dataclass(frozen=True)
class Curve:
    """An immutable zero-coupon curve defined by pillar tenors and zero rates.

    Parameters
    ----------
    tenors:
        Strictly increasing pillar maturities in years.
    zeros:
        Annually compounded zero rates (decimal) at each pillar tenor.
    config:
        Curve conventions (compounding, extrapolation).
    as_of:
        Optional ISO date string describing the curve's observation date.
    """

    tenors: np.ndarray
    zeros: np.ndarray
    config: CurveConfig = field(default_factory=lambda: DEFAULT_CONFIG.curve)
    as_of: str | None = None

    def __post_init__(self) -> None:
        tenors = _readonly(self.tenors)
        zeros = _readonly(self.zeros)
        if tenors.ndim != 1 or zeros.ndim != 1:
            raise ValueError("tenors and zeros must be 1-D")
        if tenors.shape != zeros.shape:
            raise ValueError("tenors and zeros must have the same length")
        if np.any(np.diff(tenors) <= 0):
            raise ValueError("tenors must be strictly increasing")
        if np.any(tenors <= 0):
            raise ValueError("tenors must be positive")
        # Bypass frozen to store the normalised read-only arrays.
        object.__setattr__(self, "tenors", tenors)
        object.__setattr__(self, "zeros", zeros)

    # ------------------------------------------------------------------ #
    # Core lookups
    # ------------------------------------------------------------------ #
    def zero_rate(self, t: float | np.ndarray) -> float | np.ndarray:
        """Annually compounded zero rate at time ``t`` (years).

        Linear interpolation on zero rates between pillars; flat extrapolation
        beyond the first/last pillar when ``config.extrapolate_flat`` is set.
        """
        t_arr = np.asarray(t, dtype=float)
        if self.config.extrapolate_flat:
            z = np.interp(t_arr, self.tenors, self.zeros)
        else:
            z = np.interp(
                t_arr, self.tenors, self.zeros,
                left=np.nan, right=np.nan,
            )
        return float(z) if np.isscalar(t) or t_arr.ndim == 0 else z

    def discount_factor(self, t: float | np.ndarray) -> float | np.ndarray:
        """Discount factor ``DF(t) = (1 + z(t)) ** (-t)`` (annual compounding)."""
        t_arr = np.asarray(t, dtype=float)
        z = np.asarray(self.zero_rate(t_arr), dtype=float)
        df = (1.0 + z) ** (-t_arr)
        # DF(0) == 1 by definition.
        df = np.where(t_arr == 0.0, 1.0, df)
        return float(df) if np.isscalar(t) or t_arr.ndim == 0 else df

    def pillars(self) -> list[tuple[float, float]]:
        """Return ``[(tenor, zero_rate), ...]`` for inspection/serialisation."""
        return [(float(t), float(z)) for t, z in zip(self.tenors, self.zeros)]

    # ------------------------------------------------------------------ #
    # Shock operations (all return NEW curves; never mutate self)
    # ------------------------------------------------------------------ #
    def shift(self, f: Callable[[float], float]) -> "Curve":
        """General shock. ``f(t)`` returns a shift in **basis points** at tenor
        ``t``; the shift is applied to the zero rate at each pillar.

        Phase 2 uses this for non-parallel scenarios (steepeners, twists, ...).
        The shift is sampled at the existing pillars and interpolated linearly
        between them, consistent with the curve's interpolation scheme.
        """
        shifts_decimal = np.array(
            [f(float(t)) for t in self.tenors], dtype=float
        ) / 10_000.0
        return Curve(
            tenors=self.tenors,
            zeros=self.zeros + shifts_decimal,
            config=self.config,
            as_of=self.as_of,
        )

    def parallel_shift(self, dy: float) -> "Curve":
        """Shift every zero rate by ``dy`` (decimal, e.g. 0.0001 == 1bp)."""
        return Curve(
            tenors=self.tenors,
            zeros=self.zeros + float(dy),
            config=self.config,
            as_of=self.as_of,
        )

    def key_rate_bump(
        self,
        key_tenor: float,
        bump_bps: float,
        half_width: float | None = None,
    ) -> "Curve":
        """Apply a triangular bump of ``bump_bps`` centred at ``key_tenor``.

        The bump weight is ``max(0, 1 - |t - key_tenor| / half_width)`` so it is
        full at the key tenor and decays linearly to zero ``half_width`` years
        either side. Key-rate durations (duration.py) use these bumps.
        """
        hw = (
            half_width
            if half_width is not None
            else DEFAULT_CONFIG.risk.key_rate_half_width
        )

        def triangular(t: float) -> float:
            w = max(0.0, 1.0 - abs(t - key_tenor) / hw)
            return bump_bps * w

        return self.shift(triangular)


# --------------------------------------------------------------------------- #
# Bootstrapping par yields -> zero curve
# --------------------------------------------------------------------------- #

def _par_bond_price(
    coupon_rate: float,
    maturity: float,
    frequency: int,
    df_at: Callable[[float], float],
) -> float:
    """Price (per 100 face) of a par-structured coupon bond on given DFs.

    Coupons of ``coupon_rate/frequency * 100`` are paid at ``1/frequency,
    2/frequency, ..., maturity``; principal of 100 is repaid at maturity.
    """
    n = int(round(maturity * frequency))
    coupon = coupon_rate / frequency * 100.0
    price = 0.0
    for i in range(1, n + 1):
        t_i = i / frequency
        price += coupon * df_at(t_i)
    price += 100.0 * df_at(maturity)
    return price


def bootstrap_zero_curve(
    par_yields: Mapping[float, float],
    config: CurveConfig | None = None,
    as_of: str | None = None,
) -> Curve:
    """Bootstrap an annually compounded zero curve from par yields.

    Parameters
    ----------
    par_yields:
        Mapping of ``tenor_years -> par_yield`` (decimal, e.g. 0.045 for 4.5%).
    config:
        Curve conventions. Defaults to :data:`DEFAULT_CONFIG.curve`.
    as_of:
        Optional observation date (ISO string) stored on the curve.

    Returns
    -------
    Curve
        A curve whose pillars are exactly the input tenors.

    Notes
    -----
    See the module docstring for the full method. Short-end tenors (below
    ``config.par_bond_min_tenor_years``) are taken as zero-coupon quotes; longer
    tenors are bootstrapped so that each par bond reprices to 100.
    """
    cfg = config or DEFAULT_CONFIG.curve
    freq = cfg.bootstrap_frequency
    short_cutoff = DEFAULT_CONFIG.fred.par_bond_min_tenor_years

    tenors = sorted(par_yields)
    boot_tenors: list[float] = []
    boot_zeros: list[float] = []

    def interp_zero(t: float) -> float:
        """Interpolate the zero rate using the pillars bootstrapped so far."""
        if not boot_tenors:
            raise RuntimeError("no pillars bootstrapped yet")
        return float(np.interp(t, boot_tenors, boot_zeros))

    def df_from(t: float, trial_tenor: float, trial_zero: float) -> float:
        """DF at ``t`` using bootstrapped pillars plus a trial (tenor, zero)."""
        xs = boot_tenors + [trial_tenor]
        ys = boot_zeros + [trial_zero]
        z = float(np.interp(t, xs, ys))
        return (1.0 + z) ** (-t)

    for tenor in tenors:
        par = par_yields[tenor]
        if tenor < short_cutoff:
            # Zero-coupon money-market treatment: zero rate == quoted yield.
            boot_tenors.append(tenor)
            boot_zeros.append(par)
            continue

        # Coupon end: solve for z(tenor) so the par bond reprices to 100.
        def price_minus_par(z_trial: float, _tenor: float = tenor,
                            _par: float = par) -> float:
            def df_at(t: float) -> float:
                if t < _tenor - 1e-12:
                    return (1.0 + interp_zero(t)) ** (-t)
                return df_from(t, _tenor, z_trial)
            return _par_bond_price(_par, _tenor, freq, df_at) - 100.0

        # A par bond's zero rate is near its par yield; bracket generously.
        lo, hi = -0.5, 2.0
        z_solution = brentq(price_minus_par, lo, hi, xtol=1e-12, rtol=1e-14)
        boot_tenors.append(tenor)
        boot_zeros.append(z_solution)

    return Curve(
        tenors=np.array(boot_tenors, dtype=float),
        zeros=np.array(boot_zeros, dtype=float),
        config=cfg,
        as_of=as_of,
    )


def curve_from_tenor_rate_map(
    zero_map: Mapping[float, float],
    config: CurveConfig | None = None,
    as_of: str | None = None,
) -> Curve:
    """Build a curve directly from a ``tenor -> zero_rate`` map (no bootstrap).

    Useful for tests that want to specify zeros explicitly.
    """
    cfg = config or DEFAULT_CONFIG.curve
    tenors = sorted(zero_map)
    return Curve(
        tenors=np.array(tenors, dtype=float),
        zeros=np.array([zero_map[t] for t in tenors], dtype=float),
        config=cfg,
        as_of=as_of,
    )


# A convenience: module-level general shift mirroring ``Curve.shift`` so callers
# can write ``shift(curve, f)`` as described in the Phase 1 spec.
def shift(curve: Curve, f: Callable[[float], float]) -> Curve:
    """Return a new curve with ``f(t)`` (basis points) added to each pillar."""
    return curve.shift(f)
