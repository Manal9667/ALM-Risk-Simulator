-- ALM Risk Simulator - SQLite schema (educational).
--
-- Three tables mirror the three CSV artefacts. The Treasury curve is REAL
-- (FRED). Holdings and liabilities are HYPOTHETICAL (see the `source` /
-- `*_hypothetical` naming and the data/README.md provenance file).

DROP TABLE IF EXISTS curve;
DROP TABLE IF EXISTS holdings;
DROP TABLE IF EXISTS liabilities;

-- Real U.S. Treasury par-yield curve snapshot (from FRED).
CREATE TABLE curve (
    tenor_years        REAL NOT NULL,
    fred_series        TEXT NOT NULL,
    par_yield_pct      REAL,
    par_yield_decimal  REAL,
    observation_date   TEXT NOT NULL,
    source             TEXT NOT NULL
);

-- Hypothetical bond holdings, priced on the real curve.
CREATE TABLE holdings (
    id               TEXT,
    issuer           TEXT,
    issuer_type      TEXT,
    rating           TEXT,
    face             REAL,
    coupon           REAL,
    frequency        INTEGER,
    issue_date       TEXT,
    settlement_date  TEXT,
    maturity_date    TEXT,
    maturity_years   REAL,
    spread           REAL,
    ytm              REAL,
    price            REAL,
    market_value     REAL
);

-- Hypothetical liability schedule.
CREATE TABLE liabilities (
    year                            INTEGER,
    expected_cash_flow_hypothetical REAL,
    discount_factor                 REAL,
    present_value                   REAL,
    duration_contribution_years     REAL
);
