"""Data-quality validation driven by SQL (educational).

Checks are declared in ``sql/validation_checks.sql`` (one SELECT per check,
returning offending rows). This module parses those checks, runs them against a
SQLite connection and returns structured results. Error-severity findings fail
loudly; warnings are collected and surfaced but do not raise.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

from .config import Config, DEFAULT_CONFIG


class ValidationError(RuntimeError):
    """Raised when one or more error-severity checks find offending rows."""


@dataclass(frozen=True)
class CheckResult:
    """Structured outcome of a single data-quality check."""

    name: str
    severity: str          # 'error' | 'warning'
    table: str
    description: str
    rows_affected: int
    details: list[dict] = field(default_factory=list)  # sample offending rows

    @property
    def passed(self) -> bool:
        return self.rows_affected == 0


_CHECK_HEADER = re.compile(
    r"^--\s*CHECK:\s*name=([^;]+);\s*severity=([^;]+);\s*table=([^;]+);\s*desc=([^\n]+)",
    re.MULTILINE,
)


def parse_checks(sql_text: str) -> list[tuple[dict, str]]:
    """Parse the checks file into ``[(meta, query), ...]``."""
    out: list[tuple[dict, str]] = []
    matches = list(_CHECK_HEADER.finditer(sql_text))
    for i, m in enumerate(matches):
        start = sql_text.index("\n", m.end())
        end = matches[i + 1].start() if i + 1 < len(matches) else len(sql_text)
        body = sql_text[start:end].strip().rstrip(";").strip()
        meta = {
            "name": m.group(1).strip(),
            "severity": m.group(2).strip(),
            "table": m.group(3).strip(),
            "desc": m.group(4).strip(),
        }
        out.append((meta, body))
    return out


def run_checks(
    conn: sqlite3.Connection,
    cfg: Config | None = None,
    sample_rows: int = 5,
) -> list[CheckResult]:
    """Execute all checks and return their structured results.

    The yield-range check uses named parameters ``:min_yield`` / ``:max_yield``
    bound from :class:`~src.config.ValidationConfig`.
    """
    cfg = cfg or DEFAULT_CONFIG
    sql_text = (cfg.paths.sql / "validation_checks.sql").read_text(encoding="utf-8")
    params = {
        "min_yield": cfg.validation.min_yield_pct / 100.0,
        "max_yield": cfg.validation.max_yield_pct / 100.0,
    }

    results: list[CheckResult] = []
    for meta, query in parse_checks(sql_text):
        cur = conn.cursor()
        # Only pass bindings when the query actually references them.
        if ":" in query:
            cur.execute(query, params)
        else:
            cur.execute(query)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description] if cur.description else []
        details = [dict(zip(cols, r)) for r in rows[:sample_rows]]
        results.append(
            CheckResult(
                name=meta["name"],
                severity=meta["severity"],
                table=meta["table"],
                description=meta["desc"],
                rows_affected=len(rows),
                details=details,
            )
        )
    return results


def summarise(results: list[CheckResult]) -> dict[str, int]:
    """Count passes, warnings and errors across a result set."""
    summary = {"passed": 0, "warnings": 0, "errors": 0}
    for r in results:
        if r.passed:
            summary["passed"] += 1
        elif r.severity == "error":
            summary["errors"] += 1
        else:
            summary["warnings"] += 1
    return summary


def enforce(results: list[CheckResult]) -> list[CheckResult]:
    """Raise :class:`ValidationError` if any error-severity check failed.

    Returns the list of warning-severity findings (non-fatal) for the caller to
    surface. Error findings are described in the raised exception message.
    """
    errors = [r for r in results if not r.passed and r.severity == "error"]
    warnings = [r for r in results if not r.passed and r.severity == "warning"]
    if errors:
        lines = [
            f"  - [{r.severity}] {r.name} ({r.table}): "
            f"{r.rows_affected} row(s) - {r.description}"
            for r in errors
        ]
        raise ValidationError(
            "data validation failed loudly:\n" + "\n".join(lines)
        )
    return warnings
