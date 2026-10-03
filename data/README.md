# `data/` provenance

This directory holds the data artefacts for the **ALM Risk Simulator**, an
**educational** project. It is **not** a production ALM system.

Every file is tagged below as **REAL** (sourced from a public dataset) or
**HYPOTHETICAL** (invented for teaching and clearly labelled as such in the file
contents, column names, and here).

| File | Status | Provenance |
|------|--------|-----------|
| `treasury_curve_YYYY-MM-DD.csv` | **REAL** | U.S. Treasury constant-maturity par yields downloaded from [FRED](https://fred.stlouisfed.org/) via the no-API-key CSV endpoint `https://fred.stlouisfed.org/graph/fredgraph.csv?id=<SERIES>`. Series: DGS1MO, DGS3MO, DGS6MO, DGS1, DGS2, DGS3, DGS5, DGS7, DGS10, DGS20, DGS30. Written by `python -m src.data_fetch`. One row per tenor for the most recent date on which all series were populated. The `source` column records the origin. |
| `holdings_hypothetical.csv` | **HYPOTHETICAL** | A seeded, invented bond portfolio (`src/bonds.py`, `generate_portfolio`). Issuer names, ratings, faces, coupons and credit spreads are fictional. The holdings are *priced on the real Treasury curve*: a corporate's discount rate is the Treasury zero rate at its maturity plus a hypothetical credit spread. Only the curve is real. |
| `liabilities_hypothetical.csv` | **HYPOTHETICAL** | A stylised life-insurer liability schedule (`src/liabilities.py`). Expected annual outflows (years 1-40) are a blend of annuity payouts, a death-benefit hump and a long-dated tail, then scaled so the base-case funding ratio sits near 107.5%. Not real policy data. |
| `holdings_corrupted_sample.csv` | **HYPOTHETICAL (intentionally broken)** | A tiny holdings file carrying one deliberate defect per data-quality check (duplicate id, missing value, inverted maturity, issue-after-maturity, settlement-before-issue, negative coupon, absurd yield). Used to prove the SQL validation in `src/validation.py` catches each defect. Do not use for analysis. |
| `alm.sqlite` | derived | SQLite database built by `src/db.py` from the CSVs above. Git-ignored; regenerated on demand. |

## Data policy (summary)

- The **only** real data is the Treasury par-yield curve from FRED.
- `src/data_fetch.py` is the **only** module that touches the network; everything
  else reads the cached CSV snapshot.
- If FRED is unreachable, no snapshot is written and no fabricated data is
  substituted. A clearly labelled offline fixture
  (`tests/fixtures/treasury_curve_fixture.csv`, synthetic and **not** real
  market data) exists for tests only.
- All insurer-specific data (bonds, spreads, liabilities) is hypothetical and
  labelled as such.

## Reproducing

```bash
python -m src.data_fetch     # fetch real curve -> data/treasury_curve_<date>.csv
python -m src.pipeline       # generate hypothetical holdings & liabilities, report
```
