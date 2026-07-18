# Backtest inicial — Centinela SP500

_Generado: 2026-07-18 13:59 ET_

> ⚠️ **Experimento educativo con dinero simulado.** Las métricas se reportan tal cual, sin maquillar. Rentabilidad pasada simulada no garantiza nada.

## Datos y metodología

- Universo: S&P 500 (491 tickers con datos).
- Ventana: 11 años (2016-05-03 a 2026-07-02).
- Eventos (filas en drawdown ≥30%): **233,218** | tasa base de +5% en 10 días: **51.2%**.
- Validación: walk-forward estricto + holdout del último año (usado 1 vez).
- Modelo ganador: **logreg**, umbral de probabilidad **0.79**.

## Señal (ML) — fuera de muestra

| Conjunto | N | Señales | Precisión | Recall | Base rate | AUC |
|---|---|---|---|---|---|---|
| Walk-forward | 172,152 | 7826 | 80.1% | 6.9% | 52.7% | 0.646 |
| Holdout (1 vez) | 34,157 | 659 | 68.0% | 2.9% | 45.6% | 0.624 |

## Trading — Cartera A (con stop ATR) vs B (sin stop)

| Periodo | Cartera | N ops | Win rate | Expectancy | Profit factor | Drawdown máx | P&L total (USD, $1k/op) |
|---|---|---|---|---|---|---|---|
| Walk-forward | A | 7826 | 54.9% | 3.85% | 1.81 | -20.1% | 301461 |
| Walk-forward | B | 7826 | 65.6% | 6.41% | 2.77 | -11.9% | 501744 |
| Holdout | A | 659 | 51.6% | 1.74% | 1.43 | -20.6% | 11499 |
| Holdout | B | 659 | 53.6% | 2.03% | 1.53 | -19.8% | 13358 |

### Motivos de salida (walk-forward)

- Cartera A: {'tiempo': 3053, 'stop': 2690, 'objetivo': 2083}
- Cartera B: {'tiempo': 5553, 'objetivo': 2273}

## Comparativa de modelos (señal walk-forward)

| Modelo | Umbral | Señales | Precisión | Recall | AUC |
|---|---|---|---|---|---|
| hgb | 0.77 | 10152 | 72.4% | 8.1% | 0.631 |
| logreg | 0.79 | 7826 | 80.1% | 6.9% | 0.646 |

## Lectura honesta

- Predecir rebotes de corto plazo es genuinamente difícil; el valor está en la PRECISIÓN del umbral alto (pocas señales, mejores que la tasa base), no en un AUC alto.
- Sesgo de supervivencia: se usan los constituyentes ACTUALES del S&P 500 (no hay historial point-in-time gratuito). Esto infla algo los resultados históricos.
- El objetivo no se recalcula día a día en el backtest (el sistema en vivo sí lo hace); es una aproximación conservadora.