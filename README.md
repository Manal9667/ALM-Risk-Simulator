# ALM Risk Simulator

> **Educational project.** This is a teaching tool for quantitative
> asset-liability management (ALM) and interest-rate risk analysis for a
> **hypothetical** life insurance company. **It is not a production ALM system
> and must not be used or described as one.**

Phase 1 (this stub) builds the foundation: a bootstrapped zero curve from real
U.S. Treasury par yields, a hypothetical bond portfolio priced on that curve, a
hypothetical liability schedule, duration/convexity/DV01 and key-rate analytics,
ALM balance-sheet metrics, and a SQL-driven data-quality layer, all under test.

A full README (overview, methodology, scenarios, optimizer, plots) is **Phase
2** and is intentionally left out here.

## What is real vs hypothetical

- **Real:** U.S. Treasury constant-maturity par yields from FRED.
- **Hypothetical (labelled as such):** bond holdings, corporate credit spreads,
  and the liability schedule.

See [`data/README.md`](data/README.md) for full provenance.

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt

python -m src.data_fetch          # fetch the real Treasury curve snapshot
python -m src.pipeline            # build hypothetical book + liabilities, print base case
pytest                            # run the test suite
```

If FRED is unreachable, `data_fetch` tells you and writes nothing; the pipeline
and tests can fall back to the clearly-labelled offline fixture.

## Layout

```
├── data/        # curve snapshot (real) + hypothetical holdings/liabilities + provenance
├── sql/         # schema.sql, validation_checks.sql, aggregations.sql
├── src/         # config, data_fetch, curve, bonds, liabilities, duration, db, validation, pipeline
├── tests/       # pytest suite + offline curve fixture
└── requirements.txt
```

## Status

Phase 1 foundation. Scenario generation, the ALM optimizer, plotting and the
full write-up are Phase 2.
