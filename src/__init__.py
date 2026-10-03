"""ALM Risk Simulator (educational).

This package implements an *educational* asset-liability management (ALM) and
interest-rate risk analysis for a HYPOTHETICAL life insurance company.

It is NOT a production ALM system and must never be described or used as one.
Only the U.S. Treasury par-yield curve is real (sourced from FRED). All bond
holdings, corporate credit spreads, and liability schedules are hypothetical and
labelled as such.
"""

__all__ = [
    "config",
    "curve",
    "data_fetch",
    "bonds",
    "liabilities",
    "duration",
    "db",
    "validation",
]
