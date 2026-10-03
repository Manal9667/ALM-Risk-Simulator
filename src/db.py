"""SQLite data layer: load curve, holdings and liabilities; run aggregations.

This module reads only cached CSV artefacts (never the network) and loads them
into a SQLite database using ``sql/schema.sql``. It also runs the aggregation
queries defined in ``sql/aggregations.sql`` from Python.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pandas as pd

from .config import Config, DEFAULT_CONFIG


def connect(cfg: Config | None = None, in_memory: bool = False) -> sqlite3.Connection:
    """Open a SQLite connection (file-backed by default, or in-memory)."""
    cfg = cfg or DEFAULT_CONFIG
    if in_memory:
        conn = sqlite3.connect(":memory:")
    else:
        cfg.paths.data.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(cfg.paths.sqlite_db)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection, cfg: Config | None = None) -> None:
    """Create tables from ``sql/schema.sql`` (drops any existing tables)."""
    cfg = cfg or DEFAULT_CONFIG
    schema_sql = (cfg.paths.sql / "schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    conn.commit()


def _load_frame(conn: sqlite3.Connection, df: pd.DataFrame, table: str) -> int:
    """Append a DataFrame into an existing table; return rows loaded."""
    df.to_sql(table, conn, if_exists="append", index=False)
    conn.commit()
    return len(df)


def load_curve_csv(conn: sqlite3.Connection, path: Path) -> int:
    """Load a Treasury curve snapshot CSV into the ``curve`` table."""
    return _load_frame(conn, pd.read_csv(path), "curve")


def load_holdings_csv(conn: sqlite3.Connection, path: Path) -> int:
    """Load a holdings CSV into the ``holdings`` table."""
    return _load_frame(conn, pd.read_csv(path), "holdings")


def load_liabilities_csv(conn: sqlite3.Connection, path: Path) -> int:
    """Load a liabilities CSV into the ``liabilities`` table."""
    return _load_frame(conn, pd.read_csv(path), "liabilities")


def load_all(
    conn: sqlite3.Connection,
    curve_csv: Path,
    holdings_csv: Path,
    liabilities_csv: Path,
    cfg: Config | None = None,
) -> dict[str, int]:
    """Initialise schema and load all three artefacts. Returns row counts."""
    cfg = cfg or DEFAULT_CONFIG
    init_schema(conn, cfg)
    return {
        "curve": load_curve_csv(conn, curve_csv),
        "holdings": load_holdings_csv(conn, holdings_csv),
        "liabilities": load_liabilities_csv(conn, liabilities_csv),
    }


# --------------------------------------------------------------------------- #
# Aggregations
# --------------------------------------------------------------------------- #

_AGG_HEADER = re.compile(r"^--\s*AGG:\s*name=([^;]+);\s*desc=([^\n]+)", re.MULTILINE)


def _parse_labelled_statements(sql_text: str, header: re.Pattern) -> list[tuple[dict, str]]:
    """Split a labelled .sql file into ``[(meta, statement), ...]``.

    A statement runs from just after its header line to the next header (or EOF).
    """
    out: list[tuple[dict, str]] = []
    matches = list(header.finditer(sql_text))
    for i, m in enumerate(matches):
        start = sql_text.index("\n", m.end())
        end = matches[i + 1].start() if i + 1 < len(matches) else len(sql_text)
        body = sql_text[start:end].strip().rstrip(";").strip()
        meta = {"raw_header": m.group(0)}
        meta["name"] = m.group(1).strip()
        meta["desc"] = m.group(2).strip()
        out.append((meta, body))
    return out


def run_aggregations(
    conn: sqlite3.Connection, cfg: Config | None = None
) -> dict[str, pd.DataFrame]:
    """Run every aggregation in ``sql/aggregations.sql`` and return DataFrames."""
    cfg = cfg or DEFAULT_CONFIG
    sql_text = (cfg.paths.sql / "aggregations.sql").read_text(encoding="utf-8")
    results: dict[str, pd.DataFrame] = {}
    for meta, stmt in _parse_labelled_statements(sql_text, _AGG_HEADER):
        results[meta["name"]] = pd.read_sql_query(stmt, conn)
    return results
