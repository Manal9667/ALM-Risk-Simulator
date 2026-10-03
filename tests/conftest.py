"""Shared pytest fixtures for the ALM Risk Simulator tests.

Tests never touch the network. They use the clearly-labelled fixture curve and
small synthetic cash-flow sets.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.config import DEFAULT_CONFIG
from src.curve import bootstrap_zero_curve, curve_from_tenor_rate_map
from src.data_fetch import load_par_yields_from_csv


@pytest.fixture(scope="session")
def cfg():
    return DEFAULT_CONFIG


@pytest.fixture(scope="session")
def par_yields(cfg):
    """Par yields loaded from the offline fixture curve."""
    py, _obs = load_par_yields_from_csv(cfg.paths.fixture_curve_csv)
    return py


@pytest.fixture(scope="session")
def fixture_curve(par_yields, cfg):
    """Bootstrapped zero curve from the fixture par yields."""
    return bootstrap_zero_curve(par_yields, cfg.curve, as_of="2024-06-28")


@pytest.fixture
def flat_curve():
    """A flat 4% (annually compounded) zero curve across all tenors."""
    tenors = [0.5, 1, 2, 3, 5, 7, 10, 20, 30]
    return curve_from_tenor_rate_map({t: 0.04 for t in tenors})


def make_cashflows(face=100.0, coupon=0.05, maturity=10.0, freq=2):
    """Build (times, amounts) for a standard coupon bond."""
    n = int(round(maturity * freq))
    times = np.arange(1, n + 1, dtype=float) / freq
    amounts = np.full(n, coupon / freq * face, dtype=float)
    amounts[-1] += face
    return times, amounts
