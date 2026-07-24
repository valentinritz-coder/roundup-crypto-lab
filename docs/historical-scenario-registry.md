# Historical crypto scenario registry

The historical campaign uses a repository-owned, versioned registry instead of dates embedded only in GitHub Actions YAML.

Canonical registry:

```text
config/historical-crypto-scenarios-v1.json
```

Schema:

```text
historical-crypto-scenarios/v1
```

## Frozen initial campaign

The first registry contains eight pair-specific one-shot scenarios:

| Window | BTC/EUR | ETH/EUR |
| --- | --- | --- |
| `20240401-20241001` | yes | yes |
| `20241001-20250401` | yes | yes |
| `20250401-20251001` | yes | yes |
| `20251001-20260125` | yes | yes |

Every scenario uses:

- timeframe `4h`;
- capital mode `one_shot_capital`;
- initial capital `40`;
- monthly budget metadata `40`;
- contribution day `1`;
- fee ratio `0.0026`.

The monthly budget and contribution day remain explicit even in one-shot mode so a scenario has a complete investment-plan identity and can later be compared with a separately versioned recurring campaign. One-shot execution still credits only the initial contribution.

## Why the dates are versioned

The dates and parameters were fixed before inspecting the campaign results. Changing a boundary after seeing performance would create post-hoc window selection and weaken the out-of-sample claim.

A future change must therefore:

1. create a new registry file and `registry_id`;
2. explain why the previous frozen registry is insufficient;
3. retain the old registry for auditability;
4. avoid silently replacing historical results produced from an earlier version.

## Scenario identity

Each row contains:

- stable `scenario_id`;
- pair;
- timeframe;
- strict end-exclusive timerange;
- capital mode;
- investment-plan fields;
- fee ratio;
- human-readable regime label.

BTC and ETH are alternative pair-specific scenarios. They are not combined into a diversified portfolio.

The regime labels deliberately use neutral names such as `historical-window-1`. Market-regime interpretations should be derived and documented separately rather than assigned by hindsight in the frozen input registry.

## Validation

Validate and print the canonical representation with:

```bash
python -m roundup_crypto_lab.historical_scenarios
```

Validate another registry:

```bash
python -m roundup_crypto_lab.historical_scenarios \
  --registry path/to/registry.json
```

Write canonical JSON for workflow consumption:

```bash
python -m roundup_crypto_lab.historical_scenarios \
  --output artifacts/historical-scenarios.json
```

Validation rejects:

- unsupported schemas, pairs, timeframes, or capital modes;
- malformed, invalid, empty, or reversed timeranges;
- duplicate scenario IDs or duplicate definitions;
- booleans used as integers;
- non-finite or invalid decimal fields;
- empty required strings;
- overlapping comparable windows unless both rows contain an explicit `overlap_reason`.

Canonical output sorts scenarios by ID and normalizes equivalent decimals such as `40`, `40.0`, and `40.00` to the same representation. JSON is emitted with `allow_nan=False`.

## Downstream contract

The matrix workflow from issue #72 should consume this registry through `roundup_crypto_lab.historical_scenarios`, not duplicate dates or parameters in shell code. It should preserve the registry ID and stable scenario ID in every job and artifact.
