"""Central configuration for the ALM Risk Simulator (educational).

All tunable constants live here as frozen dataclasses so the rest of the code
base contains no "magic numbers". Every value a reader might question (a spread,
a tolerance, a calibration target, a URL) is named and documented here.

Nothing in this module touches the network or the filesystem; it only declares
values. Instances are created with sensible defaults and can be overridden by
callers (Phase 2 scenario code is expected to build modified copies).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Mapping

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #

#: Project root (the directory that contains ``src/``, ``data/`` ...).
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Paths:
    """Filesystem layout. Paths are resolved relative to the project root."""

    root: Path = PROJECT_ROOT
    data: Path = PROJECT_ROOT / "data"
    sql: Path = PROJECT_ROOT / "sql"
    tests: Path = PROJECT_ROOT / "tests"
    fixtures: Path = PROJECT_ROOT / "tests" / "fixtures"

    # Named data artefacts.
    holdings_csv: Path = PROJECT_ROOT / "data" / "holdings_hypothetical.csv"
    liabilities_csv: Path = PROJECT_ROOT / "data" / "liabilities_hypothetical.csv"
    corrupted_holdings_csv: Path = (
        PROJECT_ROOT / "data" / "holdings_corrupted_sample.csv"
    )
    fixture_curve_csv: Path = (
        PROJECT_ROOT / "tests" / "fixtures" / "treasury_curve_fixture.csv"
    )
    sqlite_db: Path = PROJECT_ROOT / "data" / "alm.sqlite"

    def curve_snapshot(self, iso_date: str) -> Path:
        """Return the dated snapshot path ``data/treasury_curve_YYYY-MM-DD.csv``."""
        return self.data / f"treasury_curve_{iso_date}.csv"


# --------------------------------------------------------------------------- #
# FRED data source
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class FredConfig:
    """Settings for fetching U.S. Treasury par yields from FRED.

    The CSV endpoint requires no API key. We request one series at a time:
    ``https://fred.stlouisfed.org/graph/fredgraph.csv?id=<SERIES>``.
    """

    base_url: str = "https://fred.stlouisfed.org/graph/fredgraph.csv"

    #: Mapping of FRED series id -> tenor in years. These are the constant
    #: maturity Treasury (CMT) par-yield series. The short end (<= 1y) are
    #: money-market style quotes; see curve.py for how they are treated.
    series_tenors: Mapping[str, float] = field(
        default_factory=lambda: {
            "DGS1MO": 1.0 / 12.0,
            "DGS3MO": 3.0 / 12.0,
            "DGS6MO": 6.0 / 12.0,
            "DGS1": 1.0,
            "DGS2": 2.0,
            "DGS3": 3.0,
            "DGS5": 5.0,
            "DGS7": 7.0,
            "DGS10": 10.0,
            "DGS20": 20.0,
            "DGS30": 30.0,
        }
    )

    #: Tenors (years) that FRED quotes as coupon-bearing par bonds. The short
    #: end below this is treated as zero-coupon money-market quotes.
    par_bond_min_tenor_years: float = 2.0

    timeout_seconds: float = 20.0
    max_retries: int = 4
    #: Base backoff; retry n waits ``backoff_seconds * 2**n`` seconds.
    backoff_seconds: float = 1.5

    #: How many trailing calendar days of history to download. We only need the
    #: most recent observation but request a window so a holiday/weekend at the
    #: end of the window still yields data to forward-fill from.
    history_days: int = 30

    #: Maximum run of consecutive missing days we are willing to forward-fill.
    #: Longer gaps indicate a genuine data problem and are left as NaN.
    max_ffill_gap_days: int = 3

    #: A plausible-range sanity check on yields, in percent.
    min_plausible_yield_pct: float = -5.0
    max_plausible_yield_pct: float = 25.0


# --------------------------------------------------------------------------- #
# Curve construction
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class CurveConfig:
    """Conventions for bootstrapping and interpolating the zero curve."""

    #: Coupon frequency (per year) assumed for the par Treasury instruments
    #: when bootstrapping zero rates. Treasuries pay semiannually.
    bootstrap_frequency: int = 2

    #: Compounding frequency per year for the zero rates we store. We store and
    #: interpolate annually-compounded zero rates (see curve.py docstring).
    zero_compounding: int = 1

    #: Day-count is handled as simple year fractions (ACT/365-ish, idealised)
    #: because this is an educational model working on round tenors.
    #: Documented limitation, not a production day-count convention.
    extrapolate_flat: bool = True


# --------------------------------------------------------------------------- #
# Bonds (hypothetical holdings)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class BondConfig:
    """Parameters for generating the hypothetical bond portfolio."""

    random_seed: int = 20240101
    coupon_frequency: int = 2  # semiannual

    #: Hypothetical credit spreads (basis points) added to the Treasury zero
    #: rate, by rating tier. These are illustrative, NOT market quotes.
    credit_spreads_bps: Mapping[str, float] = field(
        default_factory=lambda: {
            "UST": 0.0,   # Treasuries: no credit spread
            "AA": 60.0,
            "A": 110.0,
            "BBB": 190.0,
        }
    )

    #: Total portfolio face target (hypothetical, USD). Used only for scale.
    target_total_face: float = 1_000_000_000.0

    settlement_offset_days: int = 2  # T+2 settlement, idealised


# --------------------------------------------------------------------------- #
# Liabilities (hypothetical)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class LiabilityConfig:
    """Parameters for the hypothetical liability schedule."""

    horizon_years: int = 40

    #: Spread (bps) added to the Treasury zero curve to discount liabilities.
    #: Represents an illiquidity / own-credit adjustment. Documented in
    #: liabilities.py; the default is deliberately modest.
    liability_spread_bps: float = 25.0

    #: Shape parameters for the three liability components. See liabilities.py
    #: for the rationale behind the chosen shape.
    annuity_level: float = 1.0          # flat annuity payout weight
    annuity_decay_per_year: float = 0.03  # annuitants die off over time
    death_benefit_peak_year: int = 15   # hump of expected death benefits
    death_benefit_width: float = 8.0    # std dev (years) of the death-benefit hump
    death_benefit_weight: float = 0.9
    tail_start_year: int = 25           # long-dated tail begins
    tail_weight: float = 0.35

    #: Calibration target: assets / liabilities funding ratio at base case.
    #: We scale liabilities so this lands in [funding_target_low, _high].
    funding_target_low: float = 1.05
    funding_target_high: float = 1.10
    funding_target_mid: float = 1.075


# --------------------------------------------------------------------------- #
# Duration / risk measurement
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class RiskConfig:
    """Settings for duration, convexity and key-rate calculations."""

    #: Bump size (in decimal, i.e. 1e-4 = 1bp) for finite-difference / effective
    #: duration and convexity.
    effective_bump: float = 1e-4  # 1 basis point

    #: Size of a DV01 bump (1 basis point, in decimal).
    dv01_bump: float = 1e-4

    #: Key-rate tenors (years) at which key-rate durations are computed.
    key_rate_tenors: tuple[float, ...] = (2.0, 5.0, 10.0, 20.0, 30.0)

    #: Half-width (years) of the triangular key-rate bump around each tenor.
    key_rate_half_width: float = 3.0

    #: YTM solver tolerance and bounds.
    ytm_tolerance: float = 1e-10
    ytm_lower: float = -0.5
    ytm_upper: float = 2.0


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ValidationConfig:
    """Plausible ranges used by the SQL data-quality checks."""

    min_yield_pct: float = -5.0
    max_yield_pct: float = 25.0
    min_coupon: float = 0.0
    min_face: float = 0.0


# --------------------------------------------------------------------------- #
# Top-level aggregate
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Config:
    """Aggregate configuration object passed around the pipeline."""

    paths: Paths = field(default_factory=Paths)
    fred: FredConfig = field(default_factory=FredConfig)
    curve: CurveConfig = field(default_factory=CurveConfig)
    bonds: BondConfig = field(default_factory=BondConfig)
    liabilities: LiabilityConfig = field(default_factory=LiabilityConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    validation: ValidationConfig = field(default_factory=ValidationConfig)

    def with_overrides(self, **changes: object) -> "Config":
        """Return a copy with top-level fields replaced (helper for Phase 2)."""
        return replace(self, **changes)


#: A ready-to-use default configuration instance.
DEFAULT_CONFIG = Config()


def bps(value_bps: float) -> float:
    """Convert basis points to a decimal rate. ``bps(100) == 0.01``."""
    return value_bps / 10_000.0
