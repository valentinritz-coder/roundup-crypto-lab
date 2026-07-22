# Variantes expérimentales du breakout

`RoundupBreakoutStrategy` reste le témoin (baseline) inchangé. Les trois stratégies suivantes
servent uniquement à mesurer l'effet marginal de filtres additionnels, sur le même jeu de données,
le même timerange et la même configuration spot/dry-run en 4 heures.

| Stratégie | Filtre ajouté | Rôle |
| --- | --- | --- |
| `RoundupBreakoutTrendStrategy` | SMA50 > SMA100 et SMA100 montante | Évite les cassures contre une tendance haussière établie. |
| `RoundupBreakoutAtrStrategy` | close > plus-haut précédent + 0,25 × ATR14 | Exige une cassure d'une ampleur minimale adaptée à la volatilité. |
| `RoundupBreakoutAtrVolumeStrategy` | volume > SMA du volume sur 20 bougies | N'accepte la cassure ATR que si l'activité est au-dessus de sa moyenne récente. |

Les quatre stratégies utilisent le plus-haut de 20 bougies **décalé d'une bougie** : aucune ne
connaît le plus-haut de la bougie courante au moment de décider. Les variantes gardent la sortie
`close < SMA20` et le stoploss personnalisé à deux ATR14.

Ces variantes ne sont pas présumées rentables. Leur objectif est d'identifier l'effet marginal de
chaque filtre, pas de sélectionner une stratégie sur une seule période. Toute conclusion doit être
vérifiée sur plusieurs fenêtres temporelles et régimes de marché, ainsi que par les contrôles
look-ahead et récursifs existants.

## Comparaison reproductible

Exécutez d'abord les quatre backtests séparément, avec le même `TIMERANGE`, puis générez un rapport :

```bash
python -m roundup_crypto_lab.compare_strategies \
  --result RoundupBreakoutStrategy=artifacts/results/baseline.zip \
  --result RoundupBreakoutTrendStrategy=artifacts/results/trend.zip \
  --result RoundupBreakoutAtrStrategy=artifacts/results/atr.zip \
  --result RoundupBreakoutAtrVolumeStrategy=artifacts/results/atr-volume.zip \
  --output artifacts/results/breakout-comparison.json
```

Le module refuse un rapport vide, une stratégie en double ou manquante, un résultat absent, et toute
métrique non numérique. Le JSON contient stratégie, nombre de trades, profit relatif et absolu,
winrate, drawdown de compte, profit factor et expectancy. Aucun hyperopt n'est exécuté.
