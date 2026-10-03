-- ALM Risk Simulator - data-quality checks (educational).
--
-- Each check is introduced by a header comment of the form:
--   -- CHECK: name=<id>; severity=<error|warning>; table=<table>; desc=<text>
-- followed by a single SELECT returning the OFFENDING rows (zero rows == pass).
-- validation.py parses these headers and executes each query.
--
-- Severity policy: structural / integrity defects are errors (they fail loudly);
-- plausibility defects (e.g. yields outside a sane band) are warnings.

-- CHECK: name=missing_values; severity=error; table=holdings; desc=Required holding fields must not be NULL
SELECT * FROM holdings
WHERE id IS NULL
   OR issuer IS NULL
   OR face IS NULL
   OR coupon IS NULL
   OR issue_date IS NULL
   OR settlement_date IS NULL
   OR maturity_date IS NULL
   OR ytm IS NULL;

-- CHECK: name=duplicate_ids; severity=error; table=holdings; desc=Instrument IDs must be unique
SELECT id, COUNT(*) AS n
FROM holdings
GROUP BY id
HAVING COUNT(*) > 1;

-- CHECK: name=invalid_maturity; severity=error; table=holdings; desc=Maturity must be after settlement
SELECT * FROM holdings
WHERE maturity_date <= settlement_date;

-- CHECK: name=issue_after_maturity; severity=error; table=holdings; desc=Issue date must not be after maturity
SELECT * FROM holdings
WHERE issue_date > maturity_date;

-- CHECK: name=settlement_before_issue; severity=error; table=holdings; desc=Settlement must not precede issuance
SELECT * FROM holdings
WHERE settlement_date < issue_date;

-- CHECK: name=negative_coupon_or_face; severity=error; table=holdings; desc=Coupon must be >= 0 and face > 0
SELECT * FROM holdings
WHERE coupon < 0
   OR face <= 0;

-- CHECK: name=out_of_range_yield; severity=warning; table=holdings; desc=YTM should sit within a plausible band
SELECT * FROM holdings
WHERE ytm < :min_yield
   OR ytm > :max_yield;

-- CHECK: name=wrong_sign_liability; severity=error; table=liabilities; desc=Expected liability outflows must be non-negative
SELECT * FROM liabilities
WHERE expected_cash_flow_hypothetical < 0;
