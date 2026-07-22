# Stratégies expérimentales de seconde génération

Ces trois stratégies spot, long-only et dry-run isolent des hypothèses d'entrée. Elles ne modifient
pas les quatre stratégies breakout historiques et ne constituent ni un conseil financier ni une
preuve de rentabilité.

| Stratégie | Hypothèse et entrée causale |
| --- | --- |
| `RoundupTrendPullbackStrategy` | Après SMA50 > SMA100, close > SMA100 et une SMA100 montante sur cinq bougies, la bougie précédente revient à 1 % de SMA20 sans passer plus de 2 % sous SMA50; la bougie courante reprend au-dessus de SMA20 et de son open. |
| `RoundupConfirmedBreakoutStrategy` | La bougie précédente clôture au-dessus du plus-haut des 20 bougies qui la précèdent; la bougie courante maintient ce niveau, ne le réintègre pas de plus de 1 %, et est haussière. |
| `RoundupVolatilitySqueezeStrategy` | La largeur Bollinger (20, 2 écarts-types) précédente est sous le quantile 20 % des 100 largeurs précédentes; la bougie courante élargit les bandes et casse le plus-haut précédent sur 20 bougies. |

Les niveaux de breakout utilisent systématiquement `rolling(...).max().shift(1)` et le quantile de
compression est également décalé. La pente compare SMA100 à sa valeur passée; aucune règle ne lit
une bougie future. Les trois variantes partagent ATR14, le stoploss personnalisé causal à `2 × ATR`,
le stoploss absolu de -12 %, aucune position short/trailing/levier, et la sortie taggée lorsque
`close < SMA20`.

## Comparaison contrôlée

Lancez **Actions → All strategy comparison → Run workflow**, choisissez `4h` et fournissez un
timerange strict `YYYYMMDD-YYYYMMDD`. Le workflow consomme exclusivement le cache Kraken préparé
par **Update Kraken data**, exécute les sept stratégies avec la même configuration et désactive le
cache de résultats (`--cache none`). Le JSON garde les ratios bruts; le Summary offre trois
classements séparés (profit, profit factor, drawdown), sans score composite ni gagnant.

Un seul timerange ne démontre pas une rentabilité hors-échantillon. Réalisez des tests
hors-échantillon et walk-forward avant toute décision; aucune stratégie ne doit être sélectionnée
pour le trading live à partir de cette comparaison seule.
