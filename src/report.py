"""Generate the README charts (``docs/*.png``) from the live model.

Run with:  ``python -m src.report``
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from . import scenarios as S  # noqa: E402
from .config import DEFAULT_CONFIG  # noqa: E402
from .pipeline import build_base_case  # noqa: E402

DOCS = Path(__file__).resolve().parents[1] / "docs"
BLUE, ORANGE, GREY = "#1f4e79", "#d1722b", "#8a8a8a"


def _style(ax) -> None:
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", alpha=0.25)


def funding_ratio_chart(base, sheet, hedged) -> Path:
    un = S.funding_ratio_curve(sheet, base.curve)
    he = S.funding_ratio_curve(hedged, base.curve)
    fig, ax = plt.subplots(figsize=(7.5, 4.2))
    ax.plot(un.shock_bp, un.funding_ratio, color=ORANGE, lw=2.4, label="Unhedged")
    ax.plot(he.shock_bp, he.funding_ratio, color=BLUE, lw=2.4,
            label="With key-rate overlay")
    ax.axhline(1.0, color=GREY, ls="--", lw=1)
    ax.text(-198, 1.003, "fully funded", color=GREY, fontsize=8)
    ax.set_xlabel("Parallel shock to Treasury curve (bp)")
    ax.set_ylabel("Funding ratio (assets / liabilities)")
    ax.set_title("Funding ratio under parallel rate shocks")
    ax.legend(frameon=False)
    _style(ax)
    fig.tight_layout()
    out = DOCS / "funding_ratio_vs_shock.png"
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return out


def krd_chart(base, sheet, hedged) -> Path:
    keys = list(sheet.key_tenors)
    a = sheet.asset_krd_dv01(base.curve)
    l = sheet.liability_krd_dv01(base.curve)
    ah = hedged.asset_krd_dv01(base.curve)
    x = np.arange(len(keys))
    w = 0.27
    fig, ax = plt.subplots(figsize=(7.5, 4.2))
    ax.bar(x - w, [a[k] / 1e3 for k in keys], w, color=BLUE, label="Assets")
    ax.bar(x, [l[k] / 1e3 for k in keys], w, color=ORANGE, label="Liabilities")
    ax.bar(x + w, [ah[k] / 1e3 for k in keys], w, color=GREY,
           label="Assets + overlay")
    ax.set_xticks(x, [f"{k:.0f}y" for k in keys])
    ax.set_ylabel("Key-rate DV01 ($ thousands per bp)")
    ax.set_title("Where the mismatch sits on the curve")
    ax.legend(frameon=False)
    _style(ax)
    fig.tight_layout()
    out = DOCS / "key_rate_dv01.png"
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return out


def main() -> None:
    DOCS.mkdir(exist_ok=True)
    base = build_base_case(DEFAULT_CONFIG, write=False)
    sheet = S.BalanceSheet.from_base_case(base)
    hedged = sheet.with_hedge(S.solve_key_rate_hedge(sheet, base.curve))
    for p in (funding_ratio_chart(base, sheet, hedged), krd_chart(base, sheet, hedged)):
        print("wrote", p)


if __name__ == "__main__":  # pragma: no cover
    main()
