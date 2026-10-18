"""Smoke test: the dashboard renders and responds to the sidebar controls."""

from __future__ import annotations

from pathlib import Path

import pytest

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
APP = str(Path(__file__).resolve().parents[1] / "app.py")


def _fr(at) -> float:
    return float(at.metric[3].value.rstrip("x"))


def test_dashboard_renders_and_shock_moves_funding_ratio():
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert not at.exception
    at.slider[0].set_value(0).run()
    base_fr = _fr(at)
    at.slider[0].set_value(-200).run()
    assert _fr(at) < base_fr - 0.05          # liabilities genuinely move


def test_hedge_toggle_flattens_response():
    at = AppTest.from_file(APP, default_timeout=120).run()
    at.slider[0].set_value(-200).run()
    unhedged = _fr(at)
    at.toggle[0].set_value(True).run()
    assert abs(_fr(at) - 1.075) < abs(unhedged - 1.075) / 5
