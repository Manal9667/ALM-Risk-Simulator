"""Streamlit dashboard for the ALM Risk Simulator (educational).

Every number on this page comes from ``src.scenarios``: liability cash flows are
frozen at the base case and re-discounted on each shocked curve, so both sides of
the balance sheet respond to rate moves.
"""

from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import streamlit as st

from src import scenarios as S
from src.config import DEFAULT_CONFIG, bps
from src.pipeline import build_base_case

BLUE, ORANGE, GREY = "#1f4e79", "#d1722b", "#8a8a8a"

st.set_page_config(page_title="ALM Risk Simulator", page_icon="📈", layout="wide")


@st.cache_resource(show_spinner="Building base case...")
def load():
    base = build_base_case(write=False)
    sheet = S.BalanceSheet.from_base_case(base)
    hedge = S.solve_key_rate_hedge(sheet, base.curve)
    return base, sheet, sheet.with_hedge(hedge), hedge


def money(x: float) -> str:
    r = round(x)
    return f"-${abs(r):,.0f}" if r < 0 else f"${r:,.0f}"


def mm(x: float) -> str:
    return f"{'-' if x < 0 else ''}${abs(x) / 1e6:,.1f}M"


def style(ax) -> None:
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", alpha=0.25)


base, sheet, hedged, hedge = load()
cfg = DEFAULT_CONFIG
curve = base.curve

# --------------------------------------------------------------------------- #
# Header
# --------------------------------------------------------------------------- #
st.title("ALM Risk Simulator")
st.caption(
    "Interest-rate risk for a hypothetical life insurer. Educational model: "
    "only the Treasury curve is real; bonds and liabilities are invented."
)
if base.source_tag == "REAL":
    st.success(f"Curve: real FRED Treasury par yields, observed {base.observation_date}.")
else:
    st.warning(
        "Curve: **offline fixture (synthetic, not real market data)**. "
        "Run `python -m src.data_fetch` to pull the latest FRED curve."
    )

# --------------------------------------------------------------------------- #
# Sidebar controls
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.header("Scenario")
    par_bp = st.slider("Parallel shift (bp)", -200, 200, -100, 5)
    twist = st.slider(
        "Steepen (+) / flatten (-)", -100, 100, 0, 5,
        help="30y moves by this many bp, 2y by -0.4x, pivoting at 10y.",
    )
    use_hedge = st.toggle("Apply key-rate hedge overlay", value=False)
    st.divider()
    st.caption(
        f"Overlay is zero-cost, built from zero-coupon Treasury exposures at "
        f"{', '.join(f'{k:.0f}y' for k in sheet.key_tenors)} "
        f"(gross {mm(hedge.gross_notional_mv)})."
    )

active = hedged if use_hedge else sheet


def shock_fn(t: float) -> float:
    return par_bp + twist * float(np.clip((t - 10.0) / 20.0, -0.4, 1.0))


shocked = curve.shift(shock_fn)

a0, l0 = active.asset_value(curve), active.liability_value(curve)
a1, l1 = active.asset_value(shocked), active.liability_value(shocked)
fr0, fr1 = a0 / l0, a1 / l1

# --------------------------------------------------------------------------- #
# Headline metrics
# --------------------------------------------------------------------------- #
c = st.columns(4)
c[0].metric("Assets", mm(a1), f"{mm(a1 - a0)}")
c[1].metric("Liabilities", mm(l1), f"{mm(l1 - l0)}", delta_color="inverse")
c[2].metric("Surplus", mm(a1 - l1), f"{mm((a1 - l1) - (a0 - l0))}")
c[3].metric("Funding ratio", f"{fr1:.3f}x", f"{fr1 - fr0:+.3f}x")

tab_fr, tab_krd, tab_tbl, tab_risk, tab_detail = st.tabs(
    ["Funding ratio", "Key-rate risk", "Scenario table", "Model risk", "Holdings & curve"]
)

# --------------------------------------------------------------------------- #
# Funding ratio vs shock
# --------------------------------------------------------------------------- #
with tab_fr:
    un = S.funding_ratio_curve(sheet, curve)
    he = S.funding_ratio_curve(hedged, curve)
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot(un.shock_bp, un.funding_ratio, color=ORANGE, lw=2.4, label="Unhedged")
    ax.plot(he.shock_bp, he.funding_ratio, color=BLUE, lw=2.4, label="With overlay")
    ax.axhline(1.0, color=GREY, ls="--", lw=1)
    if twist == 0:
        ax.scatter([par_bp], [fr1], color="black", zorder=5, label="Selected scenario")
    ax.set_xlabel("Parallel shock (bp)")
    ax.set_ylabel("Funding ratio")
    ax.legend(frameon=False)
    style(ax)
    fig.tight_layout()
    st.pyplot(fig)
    plt.close(fig)
    be = np.interp(1.0, un.funding_ratio.values, un.shock_bp.values)
    st.caption(
        f"Unhedged, the book falls below 100% funded at roughly **{be:+.0f}bp**: "
        f"liability duration ({base.liabilities.effective_duration:.1f}y) far exceeds "
        f"asset duration ({base.portfolio.effective_duration:.1f}y), so falling rates hurt."
    )

# --------------------------------------------------------------------------- #
# Key-rate risk
# --------------------------------------------------------------------------- #
with tab_krd:
    keys = list(sheet.key_tenors)
    a = active.asset_krd_dv01(curve)
    l = active.liability_krd_dv01(curve)
    x = np.arange(len(keys))
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(x - 0.2, [a[k] / 1e3 for k in keys], 0.4, color=BLUE, label="Assets")
    ax.bar(x + 0.2, [l[k] / 1e3 for k in keys], 0.4, color=ORANGE, label="Liabilities")
    ax.set_xticks(x, [f"{k:.0f}y" for k in keys])
    ax.set_ylabel("Key-rate DV01 ($ thousands per bp)")
    ax.legend(frameon=False)
    style(ax)
    fig.tight_layout()
    st.pyplot(fig)
    plt.close(fig)
    net = active.net_krd_dv01(curve)
    st.dataframe(
        pd.DataFrame({
            "Tenor": [f"{k:.0f}y" for k in keys],
            "Asset DV01": [money(a[k]) for k in keys],
            "Liability DV01": [money(l[k]) for k in keys],
            "Net": [money(net[k]) for k in keys],
            "Overlay MV": [money(hedge.strip_mv[k]) for k in keys],
        }),
        hide_index=True, width="stretch",
    )
    st.caption(
        "Assets are short and liabilities long, so the gap is concentrated at 10-30y. "
        "The overlay shorts 2-5y and goes long 10-30y until every net key-rate DV01 is zero."
    )

# --------------------------------------------------------------------------- #
# Scenario table
# --------------------------------------------------------------------------- #
with tab_tbl:
    un_t = S.run_scenarios(sheet, curve)
    he_t = S.run_scenarios(hedged, curve)
    table = pd.DataFrame({
        "Scenario": un_t.scenario,
        "FR unhedged": un_t.funding_ratio.map("{:.3f}".format),
        "FR hedged": he_t.funding_ratio.map("{:.3f}".format),
        "Surplus change, unhedged": un_t.d_surplus.map(mm),
        "Surplus change, hedged": he_t.d_surplus.map(mm),
    })
    st.dataframe(table, hide_index=True, width="stretch")
    worst_un = un_t.d_surplus.abs().max()
    worst_he = he_t.d_surplus.abs().max()
    st.caption(
        f"Worst-case surplus swing falls from {mm(worst_un)} to {mm(worst_he)} "
        f"({(1 - worst_he / worst_un) * 100:.0f}% reduction). The residual is convexity "
        "and risk between key tenors, which a DV01 overlay cannot remove."
    )

# --------------------------------------------------------------------------- #
# Model risk
# --------------------------------------------------------------------------- #
with tab_risk:
    st.subheader("Liability discount spread")
    sp = S.spread_sensitivity(curve, base.portfolio.market_value, cfg=cfg)
    st.dataframe(
        pd.DataFrame({
            "Spread (bp)": sp.liability_spread_bps.astype(int),
            "Liability duration (y)": sp.liability_duration.round(2),
            "Duration gap (y)": sp.duration_gap.round(2),
            "Net DV01": sp.net_dv01.map(money),
        }),
        hide_index=True, width="stretch",
    )
    st.subheader("Curve interpolation")
    ip = S.interpolation_sensitivity(sheet, curve)
    st.write(
        f"Liability PV differs by **{ip['difference_bps_of_pv']:.1f}bp** "
        f"({money(ip['log_linear_df_pv'] - ip['linear_zero_pv'])}) when the same "
        "pillars are interpolated log-linearly on discount factors instead of linearly on zeros."
    )

# --------------------------------------------------------------------------- #
# Holdings & curve
# --------------------------------------------------------------------------- #
with tab_detail:
    left, right = st.columns(2)
    with left:
        st.subheader("Zero curve")
        zc = pd.DataFrame(curve.pillars(), columns=["Tenor (y)", "Zero rate"])
        zs = pd.DataFrame(shocked.pillars(), columns=["Tenor (y)", "Shocked"])
        fig, ax = plt.subplots(figsize=(5, 3.2))
        ax.plot(zc["Tenor (y)"], zc["Zero rate"] * 100, color=BLUE, lw=2, label="Base")
        ax.plot(zs["Tenor (y)"], zs["Shocked"] * 100, color=ORANGE, lw=2, label="Scenario")
        ax.set_xlabel("Tenor (years)")
        ax.set_ylabel("Zero rate (%)")
        ax.legend(frameon=False)
        style(ax)
        fig.tight_layout()
        st.pyplot(fig)
        plt.close(fig)
    with right:
        st.subheader("Liability cash flows")
        lf = base.liabilities.to_frame()
        st.bar_chart(lf.set_index("year")["expected_cash_flow_hypothetical"], height=260)
    st.subheader("Bond holdings (hypothetical)")
    st.dataframe(
        pd.DataFrame([{
            "ID": b.id, "Issuer": b.issuer, "Rating": b.rating,
            "Maturity (y)": b.maturity_years, "Coupon": f"{b.coupon:.2%}",
            "YTM": f"{b.ytm:.2%}", "Market value": money(b.market_value),
        } for b in base.bonds]),
        hide_index=True, width="stretch",
    )
