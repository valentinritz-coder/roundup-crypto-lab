# Final passive DCA frequency research

The final research protocol extends the validated passive-frequency campaign with
one explicitly hypothetical fixed-cost sensitivity profile. The base campaign is
not modified: `config/passive-dca-frequency-research.json` materializes a frozen
overlay containing all four research profiles.

## Cost profiles

The final matrix uses, in order:

1. `frictionless-control-v1`;
2. `proportional-fee-v1`;
3. `proportional-plus-spread-v1`;
4. `hypothetical-fixed-cost-v1`.

The fixed-cost profile adds a hypothetical 0.10 fixed charge per executed order.
It is a sensitivity analysis only and does not represent Kraken, Bitvavo or any
other venue tariff.

## Research sequence

The existing **Passive DCA frequency campaign** confirmation run may be used as
a three-profile end-to-end smoke test. It must not be interpreted as research or
used to choose a frequency.

After this protocol is merged, run **Passive DCA frequency research** with:

- `phase=exploratory`;
- an empty `start_date`;
- all other inputs left at their committed defaults.

This materializes 60 scenarios and 420 strategy results. The confirmation phase
contains four scenarios and 28 strategy results, but it must be interpreted only
after the exploratory interpretation has been reviewed and frozen.

## Outputs

The aggregate artifact contains:

- coverage diagnostics;
- phase-level results;
- phase-aggregated window summaries;
- frequency rankings and classifications;
- execution-cost decomposition;
- a final research conclusion artifact.

The conclusion reports the rank-one frequency for every committed window set and
cost profile. It reports a consensus frequency only if every window set agrees.
Mixed evidence remains mixed. No best calendar phase is ever selected.

The fixed-cost sensitivity comparison records whether the consensus frequency
under the hypothetical fixed fee differs from the consensus under the realistic
proportional-plus-spread profile.
