# Codex task 001: validate and harden the GitHub Actions laboratory

Work only inside this repository. Read `AGENTS.md`, `docs/architecture.md`, and
`docs/risk-policy.md` first.

## Goal

Make the GitHub Actions workflows a reliable and reproducible validation environment for the
Freqtrade dry-run laboratory. Do not add local Docker requirements or live trading.

## Required work

1. Inspect every existing file before editing.
2. Review `.github/workflows/freqtrade-validation.yml` against the current Freqtrade stable CLI.
3. Run the workflow and use its real logs, not assumptions, to identify failures.
4. Confirm Kraken exposes public 4h OHLCV for BTC/EUR and ETH/EUR through Freqtrade/CCXT.
5. Validate `user_data/config.json` against the installed Freqtrade version.
6. Make strategy discovery succeed.
7. Make public-data download and backtesting complete successfully.
8. Run look-ahead analysis and recursive analysis, fixing causal or warm-up defects found.
9. Review the custom ATR stop for correct backtest and dry-run semantics.
10. Make the short dry-run startup smoke test fail on genuine startup errors but accept the expected
    timeout used to stop the temporary runner.
11. Pin Freqtrade to a reviewed release tag or immutable commit after the initial successful run.
12. Keep workflow permissions read-only and do not add secrets.
13. Ensure artifacts contain logs, exported results, the Freqtrade version/commit, and a concise
    reproducibility manifest even when a later step fails.
14. Update documentation with the exact workflow run, tested date range, and factual result summary.

## Constraints

- Keep `dry_run: true`.
- Spot only, no shorts, no leverage, no margin or futures.
- BTC/EUR and ETH/EUR only.
- 4h timeframe only.
- Maximum one open trade.
- No banking API or exchange credentials.
- No Docker.
- No machine learning, LLMs, hyperopt, grid, DCA, martingale, or averaging down.
- Do not claim profitability from one backtest.
- Do not optimize parameters during this task.

## Acceptance criteria

- Python CI passes.
- Freqtrade validation workflow passes.
- Strategy discovery and resolved configuration succeed.
- Public Kraken data download succeeds.
- Backtest completes and is exported.
- Look-ahead analysis reports no biased entry or exit columns.
- Recursive analysis has no unexplained indicator instability.
- Dry-run startup reaches normal operation before its intentional timeout.
- The final workflow pins a reviewed Freqtrade release or commit.
- Artifacts and documentation identify the exact code and data window tested.
- Deliver one focused pull request with links to the successful Actions runs.
