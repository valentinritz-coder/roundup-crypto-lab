# Versioned execution-cost profiles

Passive DCA frequency research uses committed execution-cost profiles instead of
embedding venue assumptions in Python or workflow YAML.

Each profile records:

- a stable identifier and profile version;
- proportional trading fees;
- a half-spread applied to the reference candle-open price;
- an optional fixed fee per order;
- a minimum order amount with carry-forward behavior;
- a profile kind that distinguishes controls, baselines, research assumptions,
  and sensitivity analyses.

The committed profiles are research inputs. In particular,
`hypothetical-fixed-cost-v1` is deliberately labelled as a sensitivity analysis
and is not presented as a Kraken, Bitvavo, or other platform tariff.

## Accounting

For each execution, the ledger keeps the reference price, spread-adjusted
execution price, proportional fee, fixed fee, net notional, acquired quantity,
and estimated spread cost separately.

The following exact identities are enforced:

```text
gross cash = proportional fee + fixed fee + net notional
quantity = net notional / spread-adjusted execution price
spread cost = net notional - quantity * reference price
```

Explicit fees reduce the order notional. Spread changes the execution price.
Cash below the configured minimum stays pending until a later eligible
execution.

## CLI

Run a frequency scenario with a committed profile identifier:

```bash
python -m roundup_crypto_lab.dca_cost_profile_scenario \
  --pair BTC/EUR \
  --timeframe 4h \
  --timerange 20240101-20260101 \
  --registry config/dca-strategy-registry.json \
  --initial-capital 40 \
  --monthly-budget 40 \
  --contribution-day 1 \
  --cost-profile proportional-plus-spread-v1 \
  --repository-commit "$GITHUB_SHA" \
  --output-dir artifacts/cost-profile-scenario
```

A direct path is accepted in place of the identifier. The legacy interface is
also available through `--fee`; it is projected into a fee-only profile with no
spread, fixed fee, or minimum order. `--fee` and `--cost-profile` are mutually
exclusive.
