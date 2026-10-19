"""Streamlit dashboard for the ALM Risk Simulator (educational).

Every number comes from ``src.scenarios``: liability cash flows are frozen at the
base case and re-discounted on each shocked curve.
"""

from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import streamlit as st

from src import scenarios as S
from src.config import DEFAULT_CONFIG
from src.pipeline import build_base_case

TAUPE, CLAY, SAGE, ROSE = "#6b5d4f", "#c99a7c", "#8fa08a", "#b9786b"
PAPER, INK, MUTED = "#f5f0e8", "#3a342d", "#9a9183"

st.set_page_config(page_title="ALM Risk Simulator", layout="wide")

st.markdown(
    f"""
    <style>
    #MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"]
        {{ display: none !important; }}
    [data-testid="stHeader"] {{ background: transparent; height: 2.2rem; }}
    .app-footer {{ margin-top: 3rem; padding-top: .8rem; border-top: 1px solid #e2d9c8;
                   text-align: center; font-size: .68rem; color: {MUTED};
                   letter-spacing: .06em; }}
    .block-container {{ padding-top: 2rem; max-width: 1180px; }}

    .hero {{ padding: 0 0 1.1rem; margin-bottom: 1.3rem; border-bottom: 1px solid #e2d9c8; }}
    .hero .kicker {{ font-size: .7rem; letter-spacing: .18em; color: {MUTED};
                     text-transform: uppercase; }}
    .hero h1 {{ font-family: Georgia, 'Times New Roman', serif; font-weight: 400;
                font-size: 2.3rem; margin: .2rem 0 .4rem; color: {INK}; padding: 0; }}
    .hero .sub {{ color: #6f675b; font-size: .93rem; max-width: 44rem; line-height: 1.5; }}
    .pill {{ display: inline-block; margin-top: .85rem; padding: .2rem .8rem;
             border-radius: 99px; font-size: .74rem; color: #4b4439; }}
    .pill.real {{ background: #e3e9dd; }}
    .pill.fixture {{ background: #efe0cf; }}

    [data-testid="stMetric"] {{ background: #fbf8f2; border: 1px solid #e6dfd0;
        border-radius: 12px; padding: .9rem 1.1rem; }}
    [data-testid="stMetricLabel"] p {{ font-size: .74rem; letter-spacing: .08em;
        text-transform: uppercase; color: {MUTED}; }}
    [data-testid="stMetricValue"] {{ font-family: Georgia, serif; font-weight: 400;
        font-size: 1.75rem; color: {INK}; }}

    .stTabs [data-baseweb="tab-list"] {{ gap: 1.6rem; border-bottom: 1px solid #e2d9c8; }}
    .stTabs [data-baseweb="tab"] {{ padding: .45rem 0; font-weight: 500; color: #6f675b; }}
    h2, h3 {{ font-family: Georgia, 'Times New Roman', serif; font-weight: 400; }}
    [data-testid="stSidebar"] {{ border-right: 1px solid #e2d9c8; }}
    [data-testid="stDataFrame"] {{ border: 1px solid #e6dfd0; border-radius: 10px; }}
    .note {{ color: #6f675b; font-size: .88rem; border-left: 2px solid #d9cdb8;
             padding: .1rem 0 .1rem .8rem; margin: .7rem 0 .2rem; line-height: 1.5; }}
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource(show_spinner="Building base case")
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


def note(text: str) -> None:
    st.markdown(f'<div class="note">{text}</div>', unsafe_allow_html=True)


def fig_ax(w: float = 8, h: float = 3.9):
    fig, ax = plt.subplots(figsize=(w, h))
    fig.patch.set_facecolor(PAPER)
    ax.set_facecolor(PAPER)
    ax.spines[["top", "right"]].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#cfc5b1")
    ax.tick_params(colors=INK, labelsize=9)
    ax.grid(axis="y", color="#e6dfd0", lw=0.8)
    ax.set_axisbelow(True)
    return fig, ax


def show(fig) -> None:
    fig.tight_layout()
    st.pyplot(fig)
    plt.close(fig)


base, sheet, hedged, hedge = load()
cfg = DEFAULT_CONFIG
curve = base.curve

# --------------------------------------------------------------------------- #
# Header
# --------------------------------------------------------------------------- #
if base.source_tag == "REAL":
    pill = f'<span class="pill real">FRED Treasury curve, {base.observation_date}</span>'
else:
    pill = '<span class="pill fixture">Synthetic curve (offline fixture)</span>'

st.markdown(
    f"""
    <div class="hero">
      <div class="kicker">Life insurer / interest-rate risk</div>
      <h1>ALM Risk Simulator</h1>
      <div class="sub">Assets and liabilities repriced on a shocked Treasury curve.
      Only the curve is market data; the bond book and liability stream are hypothetical.</div>
      {pill}
    </div>
    """,
    unsafe_allow_html=True,
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
    use_hedge = st.toggle("Key-rate hedge overlay", value=False)
    st.divider()
    st.caption(
        "Overlay: zero-cost zero-coupon Treasury positions at "
        f"{', '.join(f'{k:.0f}y' for k in sheet.key_tenors)}. "
        f"Gross {mm(hedge.gross_notional_mv)}."
    )

active = hedged if use_hedge else sheet


def shock_fn(t: float) -> float:
    return par_bp + twist * float(np.clip((t - 10.0) / 20.0, -0.4, 1.0))


shocked = curve.shift(shock_fn)

a0, l0 = active.asset_value(curve), active.liability_value(curve)
a1, l1 = active.asset_value(shocked), active.liability_value(shocked)
fr0, fr1 = a0 / l0, a1 / l1

c = st.columns(4)
c[0].metric("Assets", mm(a1), mm(a1 - a0))
c[1].metric("Liabilities", mm(l1), mm(l1 - l0), delta_color="inverse")
c[2].metric("Surplus", mm(a1 - l1), mm((a1 - l1) - (a0 - l0)))
c[3].metric("Funding ratio", f"{fr1:.3f}x", f"{fr1 - fr0:+.3f}x")

st.write("")
tab_fr, tab_krd, tab_tbl, tab_risk, tab_detail = st.tabs(
    ["Funding ratio", "Key-rate risk", "Scenarios", "Model risk", "Book and curve"]
)

# --------------------------------------------------------------------------- #
# Funding ratio vs shock
# --------------------------------------------------------------------------- #
with tab_fr:
    un = S.funding_ratio_curve(sheet, curve)
    he = S.funding_ratio_curve(hedged, curve)
    fig, ax = fig_ax()
    ax.plot(un.shock_bp, un.funding_ratio, color=CLAY, lw=2.6, label="Unhedged")
    ax.plot(he.shock_bp, he.funding_ratio, color=SAGE, lw=2.6, label="With overlay")
    ax.axhline(1.0, color=ROSE, ls=(0, (4, 3)), lw=1.1)
    ax.text(198, 1.004, "100% funded", color=ROSE, fontsize=8, ha="right")
    if twist == 0:
        ax.scatter([par_bp], [fr1], s=55, color=TAUPE, zorder=5, label="Selected")
    ax.set_xlabel("Parallel shock (bp)", color=INK)
    ax.set_ylabel("Funding ratio", color=INK)
    ax.legend(frameon=False, fontsize=9)
    show(fig)
    be = np.interp(1.0, un.funding_ratio.values, un.shock_bp.values)
    note(
        f"Unhedged, the book drops below 100% funded at about {be:+.0f}bp. "
        f"Liability duration is {base.liabilities.effective_duration:.1f}y against "
        f"{base.portfolio.effective_duration:.1f}y for assets, so falling rates hurt."
    )

# --------------------------------------------------------------------------- #
# Key-rate risk
# --------------------------------------------------------------------------- #
with tab_krd:
    keys = list(sheet.key_tenors)
    a = active.asset_krd_dv01(curve)
    l = active.liability_krd_dv01(curve)
    x = np.arange(len(keys))
    fig, ax = fig_ax()
    ax.bar(x - 0.2, [a[k] / 1e3 for k in keys], 0.4, color=TAUPE, label="Assets")
    ax.bar(x + 0.2, [l[k] / 1e3 for k in keys], 0.4, color=CLAY, label="Liabilities")
    ax.set_xticks(x, [f"{k:.0f}y" for k in keys])
    ax.set_ylabel("Key-rate DV01 ($000 per bp)", color=INK)
    ax.legend(frameon=False, fontsize=9)
    show(fig)
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
    note(
        "The gap sits at 10-30y. The overlay shorts 2-5y and buys 10-30y until "
        "every net key-rate DV01 is zero."
    )

# --------------------------------------------------------------------------- #
# Scenario table
# --------------------------------------------------------------------------- #
with tab_tbl:
    un_t = S.run_scenarios(sheet, curve)
    he_t = S.run_scenarios(hedged, curve)
    table = pd.DataFrame({
        "Scenario": un_t.scenario,
        "FR unhedged": un_t.funding_ratio,
        "FR hedged": he_t.funding_ratio,
        "Surplus chg, unhedged ($M)": un_t.d_surplus / 1e6,
        "Surplus chg, hedged ($M)": he_t.d_surplus / 1e6,
    })

    def tint_fr(v: float) -> str:
        return "background-color: #f1e0da; color: #8a4538" if v < 1.0 else ""

    def tint_pl(v: float) -> str:
        if v <= -10:
            return "color: #a85a4d"
        if v >= 10:
            return "color: #5f7a5d"
        return ""

    styled = (
        table.style
        .format({"FR unhedged": "{:.3f}", "FR hedged": "{:.3f}",
                 "Surplus chg, unhedged ($M)": "{:+,.1f}",
                 "Surplus chg, hedged ($M)": "{:+,.1f}"})
        .map(tint_fr, subset=["FR unhedged", "FR hedged"])
        .map(tint_pl, subset=["Surplus chg, unhedged ($M)", "Surplus chg, hedged ($M)"])
    )
    st.dataframe(styled, hide_index=True, width="stretch")
    worst_un, worst_he = un_t.d_surplus.abs().max(), he_t.d_surplus.abs().max()
    note(
        f"Worst surplus swing: {mm(worst_un)} unhedged, {mm(worst_he)} hedged "
        f"({(1 - worst_he / worst_un) * 100:.0f}% lower). What remains is convexity "
        "and risk between key tenors."
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
    note(
        f"Interpolating the same pillars log-linearly on discount factors instead of "
        f"linearly on zeros moves liability PV by {ip['difference_bps_of_pv']:.1f}bp "
        f"({money(ip['log_linear_df_pv'] - ip['linear_zero_pv'])})."
    )

# --------------------------------------------------------------------------- #
# Book and curve
# --------------------------------------------------------------------------- #
with tab_detail:
    left, right = st.columns(2)
    with left:
        st.subheader("Zero curve")
        zc = pd.DataFrame(curve.pillars(), columns=["t", "z"])
        zs = pd.DataFrame(shocked.pillars(), columns=["t", "z"])
        fig, ax = fig_ax(5, 3.2)
        ax.plot(zc.t, zc.z * 100, color=TAUPE, lw=2.2, label="Base")
        ax.plot(zs.t, zs.z * 100, color=CLAY, lw=2.2, label="Scenario")
        ax.set_xlabel("Tenor (years)", color=INK)
        ax.set_ylabel("Zero rate (%)", color=INK)
        ax.legend(frameon=False, fontsize=9)
        show(fig)
    with right:
        st.subheader("Liability cash flows")
        lf = base.liabilities.to_frame()
        fig, ax = fig_ax(5, 3.2)
        ax.bar(lf.year, lf.expected_cash_flow_hypothetical / 1e6, color=SAGE, width=0.8)
        ax.set_xlabel("Year", color=INK)
        ax.set_ylabel("Expected outflow ($M)", color=INK)
        show(fig)
    st.subheader("Bond holdings")
    st.dataframe(
        pd.DataFrame([{
            "ID": b.id, "Issuer": b.issuer, "Rating": b.rating,
            "Maturity (y)": b.maturity_years, "Coupon": f"{b.coupon:.2%}",
            "YTM": f"{b.ytm:.2%}", "Market value": money(b.market_value),
        } for b in base.bonds]),
        hide_index=True, width="stretch",
    )
