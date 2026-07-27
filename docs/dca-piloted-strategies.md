# Preregistered piloted-DCA research batch

Issue #92 introduces four buy-only DCA deployment hypotheses. Their defaults are frozen in
`config/dca-strategy-registry.json` before any historical campaign is inspected. They are research
strategies, not recommendations, and they must not be retuned after seeing one convenient window.

All four strategies:

- consume only cash already contributed;
- emit only `DcaBuyOrder` values and never sell;
- use prior-known indicator observations;
- cap every order at currently available cash;
- retain exact decimal parameter provenance through the registry;
- force old pending cash to deploy after 180 days;
- use a minimum gross order of 1.00 with deterministic skip behavior;
- keep BTC/EUR and ETH/EUR as separate scenarios.

Monthly DCA remains the primary control. Daily and Weekly DCA remain secondary controls.

## Frozen defaults

| Registry ID | Implementation | Cadence | Frozen allocation rules |
| --- | --- | --- | --- |
| `drawdown-reserve-dca` | `immediate_floor_drawdown_reserve` | Every completed candle | Invest 50% of each newly visible contribution. Release 25%, 50% or 100% of the remaining new reserve at causal drawdowns of 10%, 20% or 30%. A previously released tier is not released repeatedly without new cash. |
| `no-sell-value-averaging` | `no_sell_value_averaging` | Every completed candle | Target marked crypto value equal to cumulative contributions. Buy the positive shortfall, limited by cash. When no shortfall exists, invest at least 10% of a newly visible contribution. Never sell an excess. |
| `ma-deviation-dca` | `moving_average_deviation` | Daily | Allocate 10% of available cash in the neutral band, 20% when the prior close is at least 5% below the causal SMA200, and 5% when it is at least 5% above. |
| `ker-adx-accumulation` | `ker_adx_accumulation` | Every completed candle | Invest 10% of each newly visible contribution. When KER20 is at least 0.35 and ADX14 is at least 25, also release 50% of the remaining reserve. |

The registry is the authority for these values. Documentation describes the frozen experiment but
does not override the versioned JSON.

## Causal indicator contracts

Indicator observations are accepted only when `observed_at <= decision_at`. The common
`DcaDecisionContext` rejects future observations before strategy evaluation.

### Rolling drawdown

`drawdown-reserve-dca` receives `rolling_drawdown`, defined from the previous completed close
relative to the previous rolling 180-candle high:

```text
rolling_drawdown = max(0, 1 - previous_close / prior_rolling_high_180)
```

The rolling high is shifted by one candle. Warm-up is 181 candles. Exact tier boundaries are
inclusive: 10%, 20% and 30% enter tiers 1, 2 and 3 respectively.

### Moving-average deviation

`ma-deviation-dca` receives:

- `previous_close`, shifted by one completed candle;
- `long_ma`, a 200-candle simple moving average shifted by one candle.

Warm-up is 201 candles. A deviation exactly equal to -5% uses the below-average multiplier, and a
deviation exactly equal to +5% uses the above-average multiplier.

### KER20 and ADX14

`ker-adx-accumulation` uses the same core definitions and thresholds as the existing
`RoundupTrendQualityKerAdxStrategy`:

- KER over 20 candles;
- Wilder ADX over 14 candles;
- KER threshold 0.35;
- ADX threshold 25.

Both values are observed from completed data and shifted by one decision candle. The conservative
warm-up is 120 candles, matching the existing KerADX research strategy. Unlike that trading
strategy, this pilot never opens or closes a trade position. The signal only accelerates deployment
of contributed cash.

## Strategy behavior

### Immediate floor plus drawdown reserve

**Hypothesis.** Immediate participation avoids missing a persistent bull market, while explicit
drawdown tiers reserve cash for materially cheaper prices.

**Expected advantage.** Higher crypto quantity during volatile or declining paths without allowing
the entire contribution to sit idle.

**Expected failure mode.** In a smooth bull market, retained reserve can create cash drag. Repeated
small drawdowns may also fail to reach the release tiers.

**State.** The strategy records cumulative contributions already processed and the highest drawdown
tier already released. A recovered market does not reset that released tier for old reserve cash.
New contributions still receive the release fraction appropriate to the current drawdown.

**Stable decision tags.**

- `immediate_floor_drawdown_reserve.buy-floor`
- `immediate_floor_drawdown_reserve.buy-tier-1`
- `immediate_floor_drawdown_reserve.buy-tier-2`
- `immediate_floor_drawdown_reserve.buy-tier-3`
- `immediate_floor_drawdown_reserve.buy-cash-expiry`
- `immediate_floor_drawdown_reserve.skip-reserve`

**Rejection criteria.** Reject the hypothesis if rolling and frozen confirmation campaigns show no
robust improvement over Monthly DCA in final quantity or final value, or if cash-age and deployment
metrics reveal persistent underdeployment without compensating downside benefits.

### No-sell value averaging

**Hypothesis.** Buying the gap between contributed capital and marked crypto value automatically
allocates more after declines and less when the accumulated position is ahead.

**Expected advantage.** Larger purchases after losses without introducing a sell rule or borrowing.

**Expected failure mode.** A prolonged bull market can keep the marked position above target and
leave reserve cash idle until the minimum floor or expiry rule acts.

**Target.**

```text
target_crypto_value = cumulative_contributions * 1.00
shortfall = max(0, target_crypto_value - marked_crypto_value)
```

Cash itself is not included in the target comparison because moving cash into crypto would otherwise
leave total portfolio value almost unchanged and make the target mechanically meaningless.

**Stable decision tags.**

- `no_sell_value_averaging.buy-shortfall`
- `no_sell_value_averaging.buy-minimum-floor`
- `no_sell_value_averaging.buy-cash-expiry`
- `no_sell_value_averaging.skip-above-target`

**Rejection criteria.** Reject the hypothesis if it does not improve final quantity or final value
across regimes, if the majority of apparent drawdown reduction is explained by cash retention, or
if expiry purchases dominate the strategy's intended value-averaging decisions.

### Moving-average deviation DCA

**Hypothesis.** A bounded allocation multiplier based on deviation from a long causal average may
buy more when price is depressed relative to trend and less when price is extended.

**Expected advantage.** Better average acquisition price in oscillating or mean-reverting markets.

**Expected failure mode.** Persistent trends can make the long average slow. The strategy may buy
too cautiously during sustained appreciation or allocate heavily during a structural decline.

**Frozen regimes.**

| Prior-close deviation from SMA200 | Multiplier | Effective daily cash fraction |
| --- | ---: | ---: |
| At or below -5% | 2.00 | 20% |
| Between -5% and +5% | 1.00 | 10% |
| At or above +5% | 0.50 | 5% |

The multiplier is bounded and the final allocation fraction cannot exceed 100% of available cash.

**Stable decision tags.**

- `moving_average_deviation.buy-below-ma`
- `moving_average_deviation.buy-neutral`
- `moving_average_deviation.buy-above-ma`
- `moving_average_deviation.buy-cash-expiry`
- `moving_average_deviation.skip-zero-allocation`

**Rejection criteria.** Reject the hypothesis if adjacent preregistered windows show unstable sign
or magnitude, if the strategy loses to Monthly DCA in both quantity and value across most regimes,
or if its result depends on a narrow threshold boundary.

### KerADX accumulation

**Hypothesis.** A small immediate floor preserves market participation, while KER20 and ADX14 can
identify efficient directional movement where faster reserve deployment is justified.

**Expected advantage.** Faster deployment during strong, smooth trends without using the signal to
sell accumulated crypto.

**Expected failure mode.** Trend-quality signals can arrive after price has already advanced or can
accelerate into false breakouts. In weak markets, the reserve may remain mostly in cash until expiry.

**Stable decision tags.**

- `ker_adx_accumulation.buy-immediate-floor`
- `ker_adx_accumulation.buy-accelerated`
- `ker_adx_accumulation.buy-cash-expiry`
- `ker_adx_accumulation.skip-wait-signal`

**Rejection criteria.** Reject the hypothesis if acceleration does not improve final quantity or
value relative to the immediate floor alone, if false-signal windows dominate, or if performance is
not stable around the frozen KER and ADX thresholds.

## Validation boundary

Synthetic tests cover:

- bull, bear, flat and oscillating price/indicator paths;
- exact drawdown and moving-average boundaries;
- 180-day cash expiry;
- shortfalls larger than available cash;
- missing and future indicator observations;
- deterministic state transitions;
- exact registry provenance;
- buy-only outputs and overspend rejection.

Issue #93 will add the controlled GitHub Actions comparison workflow. Issue #94 will add rolling
windows and frozen confirmation campaigns. Neither later issue may silently replace these defaults
with historically optimal values.
