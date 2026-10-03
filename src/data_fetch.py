"""Fetch real U.S. Treasury par yields from FRED (the ONLY network module).

This is the single module in the project that touches the network. Everything
downstream (curve, bonds, liabilities, duration, SQL) reads only the cached CSV
snapshot this module writes to ``data/treasury_curve_YYYY-MM-DD.csv``.

Data provenance
---------------
* **Source:** Federal Reserve Economic Data (FRED), St. Louis Fed. We use the
  no-API-key CSV endpoint, one constant-maturity Treasury (CMT) series at a
  time: ``https://fred.stlouisfed.org/graph/fredgraph.csv?id=<SERIES>``.
* **Series:** DGS1MO, DGS3MO, DGS6MO, DGS1, DGS2, DGS3, DGS5, DGS7, DGS10,
  DGS20, DGS30 (par yields, percent).
* **Status:** REAL market data. (Bond holdings, credit spreads and liabilities
  elsewhere in the project are HYPOTHETICAL and labelled as such.)

Robustness
----------
* Per-request timeout and exponential-backoff retries.
* Every downloaded series must parse and must not be entirely missing
  (all-NaN) -> otherwise we raise.
* Only *isolated* missing days are forward-filled, up to
  ``FredConfig.max_ffill_gap_days`` consecutive days (weekends/holidays).
  Longer gaps are left missing and will drop the affected dates.
* We then select the most recent date on which *all* series have a value and
  snapshot that single cross-section as the curve.

Failure policy
--------------
If FRED is unreachable we DO NOT fabricate data and we DO NOT relabel the test
fixture as a real snapshot. ``main`` reports the failure and exits non-zero. A
clearly labelled fixture (``tests/fixtures/treasury_curve_fixture.csv``) exists
for offline testing only.
"""

from __future__ import annotations

import io
import sys
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd
import requests

from .config import Config, DEFAULT_CONFIG


class FredFetchError(RuntimeError):
    """Raised when real FRED data cannot be fetched or validated."""


@dataclass(frozen=True)
class CurveSnapshot:
    """A single-date cross-section of par yields ready to persist/bootstrap."""

    observation_date: str          # ISO date of the observations
    #: tenor_years -> par_yield (decimal, e.g. 0.0453)
    par_yields: dict[float, float]
    #: tenor_years -> FRED series id (provenance)
    series_by_tenor: dict[float, str]
    source: str = "FRED (real U.S. Treasury CMT par yields)"


# --------------------------------------------------------------------------- #
# Networking
# --------------------------------------------------------------------------- #

def _download_series(series_id: str, cfg: Config) -> pd.Series:
    """Download one FRED series as a date-indexed float Series (percent).

    Raises
    ------
    FredFetchError
        On network failure after retries, unparseable content, or an all-missing
        column.
    """
    url = cfg.fred.base_url
    params = {"id": series_id}
    last_err: Exception | None = None

    for attempt in range(cfg.fred.max_retries):
        try:
            resp = requests.get(
                url, params=params, timeout=cfg.fred.timeout_seconds
            )
            resp.raise_for_status()
            text = resp.text
            frame = pd.read_csv(io.StringIO(text))
            if frame.shape[1] < 2:
                raise FredFetchError(
                    f"{series_id}: unexpected CSV shape {frame.shape}"
                )
            # FRED's date column is 'observation_date' (new) or 'DATE' (old).
            date_col = frame.columns[0]
            value_col = frame.columns[1]
            frame[date_col] = pd.to_datetime(frame[date_col])
            # Missing observations are encoded as '.'; coerce to NaN floats.
            values = pd.to_numeric(frame[value_col], errors="coerce")
            series = pd.Series(
                values.to_numpy(), index=frame[date_col], name=series_id
            )
            if series.dropna().empty:
                raise FredFetchError(
                    f"{series_id}: column is entirely missing (all-NaN)"
                )
            return series
        except (requests.RequestException, FredFetchError, ValueError) as exc:
            last_err = exc
            if attempt < cfg.fred.max_retries - 1:
                wait = cfg.fred.backoff_seconds * (2 ** attempt)
                time.sleep(wait)
            continue

    raise FredFetchError(
        f"failed to fetch {series_id} after {cfg.fred.max_retries} attempts: "
        f"{last_err}"
    )


def fetch_curve(cfg: Config | None = None) -> tuple[CurveSnapshot, pd.DataFrame]:
    """Fetch all configured series and reduce to a single-date snapshot.

    Returns
    -------
    (CurveSnapshot, DataFrame)
        The chosen cross-section, plus the full aligned (and forward-filled)
        history frame for transparency.
    """
    cfg = cfg or DEFAULT_CONFIG
    tenors = cfg.fred.series_tenors

    columns: dict[str, pd.Series] = {}
    for series_id in tenors:
        columns[series_id] = _download_series(series_id, cfg)

    frame = pd.DataFrame(columns).sort_index()

    # Keep only the trailing window we care about.
    if cfg.fred.history_days > 0 and not frame.empty:
        cutoff = frame.index.max() - pd.Timedelta(days=cfg.fred.history_days)
        frame = frame.loc[frame.index >= cutoff]

    # Forward-fill ONLY isolated missing days (weekends/holidays), capped.
    filled = frame.ffill(limit=cfg.fred.max_ffill_gap_days)

    # Choose the most recent date where every series has a value.
    complete = filled.dropna(how="any")
    if complete.empty:
        raise FredFetchError(
            "no date has all series populated after limited forward-fill"
        )
    row = complete.iloc[-1]
    obs_date = complete.index[-1].date().isoformat()

    # Plausibility check on yields (percent).
    for series_id, pct in row.items():
        if not (cfg.fred.min_plausible_yield_pct
                <= pct
                <= cfg.fred.max_plausible_yield_pct):
            raise FredFetchError(
                f"{series_id}={pct} outside plausible range on {obs_date}"
            )

    par_yields = {
        tenors[s]: float(row[s]) / 100.0 for s in tenors
    }
    series_by_tenor = {tenors[s]: s for s in tenors}
    snapshot = CurveSnapshot(
        observation_date=obs_date,
        par_yields=par_yields,
        series_by_tenor=series_by_tenor,
    )
    return snapshot, filled


def write_snapshot_csv(snapshot: CurveSnapshot, cfg: Config | None = None) -> Path:
    """Persist a snapshot to ``data/treasury_curve_YYYY-MM-DD.csv``.

    The CSV is explicitly labelled as REAL FRED data in a ``source`` column.
    """
    cfg = cfg or DEFAULT_CONFIG
    cfg.paths.data.mkdir(parents=True, exist_ok=True)
    rows = []
    for tenor in sorted(snapshot.par_yields):
        rows.append(
            {
                "tenor_years": tenor,
                "fred_series": snapshot.series_by_tenor[tenor],
                "par_yield_pct": round(snapshot.par_yields[tenor] * 100.0, 6),
                "par_yield_decimal": snapshot.par_yields[tenor],
                "observation_date": snapshot.observation_date,
                "source": snapshot.source,
            }
        )
    df = pd.DataFrame(rows)
    out_path = cfg.paths.curve_snapshot(snapshot.observation_date)
    df.to_csv(out_path, index=False)
    return out_path


# --------------------------------------------------------------------------- #
# Reading cached snapshots (no network) - used by the rest of the pipeline
# --------------------------------------------------------------------------- #

def load_par_yields_from_csv(path: Path) -> tuple[dict[float, float], str]:
    """Read a snapshot CSV and return ``(tenor->par_yield_decimal, obs_date)``.

    This performs no network I/O; the downstream pipeline uses only this reader.
    """
    df = pd.read_csv(path)
    par_yields = {
        float(r.tenor_years): float(r.par_yield_decimal)
        for r in df.itertuples(index=False)
    }
    obs_date = str(df["observation_date"].iloc[0])
    return par_yields, obs_date


def latest_snapshot_path(cfg: Config | None = None) -> Path | None:
    """Return the newest ``treasury_curve_*.csv`` in the data dir, if any."""
    cfg = cfg or DEFAULT_CONFIG
    if not cfg.paths.data.exists():
        return None
    snaps = sorted(cfg.paths.data.glob("treasury_curve_*.csv"))
    return snaps[-1] if snaps else None


def load_curve_source(cfg: Config | None = None) -> tuple[dict[float, float], str, str]:
    """Load par yields for the pipeline from the latest real snapshot.

    Falls back to the clearly-labelled test fixture ONLY if no real snapshot is
    present, returning a ``source`` tag of 'FIXTURE' in that case so callers can
    surface the distinction.

    Returns
    -------
    (par_yields, observation_date, source_tag)
        ``source_tag`` is 'REAL' or 'FIXTURE'.
    """
    cfg = cfg or DEFAULT_CONFIG
    snap = latest_snapshot_path(cfg)
    if snap is not None:
        par_yields, obs_date = load_par_yields_from_csv(snap)
        return par_yields, obs_date, "REAL"
    if cfg.paths.fixture_curve_csv.exists():
        par_yields, obs_date = load_par_yields_from_csv(cfg.paths.fixture_curve_csv)
        return par_yields, obs_date, "FIXTURE"
    raise FredFetchError(
        "no real snapshot and no fixture available; run `python -m src.data_fetch`"
    )


# --------------------------------------------------------------------------- #
# CLI entry point
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    """Fetch real FRED data, validate, and write a dated snapshot.

    Prints a short summary. Returns 0 on success, non-zero on failure.
    """
    cfg = DEFAULT_CONFIG
    print("ALM Risk Simulator - fetching REAL U.S. Treasury par yields from FRED")
    print(f"endpoint: {cfg.fred.base_url}?id=<SERIES>")
    try:
        snapshot, _history = fetch_curve(cfg)
        out_path = write_snapshot_csv(snapshot, cfg)
    except FredFetchError as exc:
        print(f"\nERROR: could not fetch real FRED data: {exc}", file=sys.stderr)
        print(
            "FRED appears unreachable or returned bad data. No snapshot was "
            "written. A clearly-labelled fixture exists for offline tests at "
            f"{cfg.paths.fixture_curve_csv} but it is NOT real market data.",
            file=sys.stderr,
        )
        return 1

    print(f"\nSnapshot date : {snapshot.observation_date}")
    print(f"Saved to      : {out_path}")
    print("Par yields (%):")
    for tenor in sorted(snapshot.par_yields):
        series = snapshot.series_by_tenor[tenor]
        pct = snapshot.par_yields[tenor] * 100.0
        print(f"  {series:<7} {tenor:6.3f}y  {pct:7.3f}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
