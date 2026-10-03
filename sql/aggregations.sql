-- ALM Risk Simulator - portfolio aggregations (educational). Run from Python.
--
-- Each aggregation is introduced by:
--   -- AGG: name=<id>; desc=<text>
-- followed by a single SELECT.

-- AGG: name=mv_by_maturity_bucket; desc=Market value by maturity bucket (years)
SELECT
    CASE
        WHEN maturity_years < 2  THEN '0-2'
        WHEN maturity_years < 5  THEN '2-5'
        WHEN maturity_years < 10 THEN '5-10'
        WHEN maturity_years < 20 THEN '10-20'
        ELSE '20+'
    END AS maturity_bucket,
    COUNT(*)          AS n_holdings,
    SUM(market_value) AS market_value
FROM holdings
GROUP BY maturity_bucket
ORDER BY MIN(maturity_years);

-- AGG: name=mv_by_issuer_type; desc=Market value by issuer type
SELECT
    issuer_type,
    COUNT(*)          AS n_holdings,
    SUM(market_value) AS market_value
FROM holdings
GROUP BY issuer_type
ORDER BY issuer_type;
