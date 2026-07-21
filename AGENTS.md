# Codex instructions

## Product goal

Build a small, auditable crypto spot swing-trading lab funded by EUR 40/month plus payment
roundups.

## Non-negotiable constraints

- Dry-run only until a later explicitly approved milestone.
- Spot only.
- No leverage, margin, futures, derivatives, shorts, borrowing, staking, lending, copy trading,
  grids, martingale, or averaging down.
- Maximum one open position.
- No banking API in v1. CSV import only.
- No exchange secrets in the repository or GitHub Actions.
- Never enable withdrawal permissions in examples or documentation.
- Preserve exact monetary values using integer cents or `Decimal`; never binary floats for bank
  amounts.
- External contributions must never be counted as strategy profit.
-