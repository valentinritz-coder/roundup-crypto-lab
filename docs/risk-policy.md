# Risk policy

This project is an experiment, not the household investment portfolio.

## Funding

- EUR 40 fixed monthly contribution.
- Eligible payment roundups calculated from a CSV export.
- No exceptional top-ups following losses.
- Real transfers are manual in v1.

## Market exposure

- Crypto spot only.
- BTC/EUR and ETH/EUR only during the first milestone.
- No leverage, margin, futures, derivatives, or short selling.
- Maximum one open trade.
- Keep at least 20% of the wallet outside open positions.

## Operational safety

- Dry-run only at project start.
- Exchange credentials are absent from version control.
- Any future trading key must have withdrawals disabled.
- Any future live milestone requires a separate explicit review and PR.
- API/UI must not be exposed publicly without authenticated private networking.

## Promotion gate to real money

No real-money mode before all of the following exist:

1. causal backtests across multiple market regimes;
2. explicit fees and slippage assumptions;
3. successful Freqtrade look-ahead analysis;
4. several months of uninterrupted dry-run;
5. reconciliation between signals, simulated orders, and portfolio state;
6. a tested kill switch and documented recovery procedure;
7. an explicit maximum bankroll accepted as fully loseable.
