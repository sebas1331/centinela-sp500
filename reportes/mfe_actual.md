# Análisis MFE/MAE de posiciones

_Generado 2026-07-29 12:26 ET_

> Experimento educativo, dinero simulado. Solo lectura: no cambia objetivos, stops ni el modelo.

**MFE** (Maximum Favorable Excursion) = mejor precio intradía a favor desde la entrada. **MAE** (Maximum Adverse Excursion) = peor precio intradía en contra. Ambos usan High/Low diarios reales, no el cierre.

## Posiciones abiertas

| Ticker | Cartera | Entrada | Precio entrada | MFE % | Fecha MFE | MAE % | Fecha MAE | P&L actual % | ¿Tocó +5%? | ¿Tocó objetivo? | Objetivo inicial |
|---|---|---|---|---|---|---|---|---|---|---|---|
| SMCI | A | 2026-07-22 | 28.93 | +12.65% | 2026-07-23 | -8.92% | 2026-07-29 | -8.02% | ✅ sí | no | 37.38 |
| SMCI | B | 2026-07-22 | 28.93 | +12.65% | 2026-07-23 | -8.92% | 2026-07-29 | -8.02% | ✅ sí | no | 37.38 |
| SNDK | B | 2026-07-21 | 1510.26 | +12.32% | 2026-07-23 | -33.91% | 2026-07-29 | -32.30% | ✅ sí | no | 2354.39 |
| WDC | B | 2026-07-21 | 529.78 | +8.79% | 2026-07-23 | -20.32% | 2026-07-28 | -12.11% | ✅ sí | no | 779.80 |
| GLW | A | 2026-07-28 | 121.75 | +7.13% | 2026-07-29 | -5.95% | 2026-07-28 | +2.46% | ✅ sí | no | 151.14 |
| GLW | B | 2026-07-28 | 121.75 | +7.13% | 2026-07-29 | -5.95% | 2026-07-28 | +2.46% | ✅ sí | no | 151.14 |
| COHR | B | 2026-07-21 | 307.26 | +6.68% | 2026-07-23 | -28.06% | 2026-07-29 | -27.44% | ✅ sí | no | 439.68 |
| ON | A | 2026-07-22 | 89.90 | +4.85% | 2026-07-22 | -11.56% | 2026-07-29 | -10.83% | no | no | 124.05 |
| ON | B | 2026-07-22 | 89.90 | +4.85% | 2026-07-22 | -11.56% | 2026-07-29 | -10.83% | no | no | 124.05 |
| CIEN | B | 2026-07-22 | 396.95 | +4.75% | 2026-07-23 | -18.49% | 2026-07-29 | -17.79% | no | no | 565.71 |
| MRVL | B | 2026-07-21 | 205.56 | +4.56% | 2026-07-22 | -20.75% | 2026-07-29 | -19.63% | no | no | 314.09 |
| KLAC | B | 2026-07-23 | 213.35 | +3.11% | 2026-07-24 | -19.15% | 2026-07-29 | -18.61% | no | no | 307.37 |
| INTC | A | 2026-07-28 | 86.62 | +2.13% | 2026-07-29 | -5.55% | 2026-07-29 | -4.18% | no | no | 115.65 |
| INTC | B | 2026-07-28 | 86.62 | +2.13% | 2026-07-29 | -5.55% | 2026-07-29 | -4.18% | no | no | 115.65 |
| GLW | B | 2026-07-21 | 161.30 | +1.98% | 2026-07-21 | -29.01% | 2026-07-28 | -22.67% | no | no | 214.07 |
| SNDK | B | 2026-07-28 | 1172.37 | +1.33% | 2026-07-28 | -14.86% | 2026-07-29 | -12.78% | no | no | 2280.80 |
| SNDK | A | 2026-07-28 | 1172.37 | +1.33% | 2026-07-28 | -14.86% | 2026-07-29 | -12.78% | no | no | 2280.80 |
| MRNA | A | 2026-07-22 | 59.37 | +0.83% | 2026-07-22 | -10.15% | 2026-07-28 | -8.17% | no | no | 85.60 |
| MRNA | B | 2026-07-22 | 59.37 | +0.83% | 2026-07-22 | -10.15% | 2026-07-28 | -8.17% | no | no | 85.60 |
| LITE | A | 2026-07-28 | 677.55 | +0.21% | 2026-07-28 | -12.21% | 2026-07-29 | -11.18% | no | no | 1104.89 |
| LITE | B | 2026-07-28 | 677.55 | +0.21% | 2026-07-28 | -12.21% | 2026-07-29 | -11.18% | no | no | 1104.89 |
| COHR | A | 2026-07-28 | 257.28 | +0.02% | 2026-07-28 | -14.08% | 2026-07-29 | -13.34% | no | no | 399.45 |
| COHR | B | 2026-07-28 | 257.28 | +0.02% | 2026-07-28 | -14.08% | 2026-07-29 | -13.34% | no | no | 399.45 |

## Posiciones cerradas en los últimos 30 días

_El MFE/MAE de las cerradas cubre TODO el período desde la entrada hasta hoy (no se corta en la fecha de salida), para ver qué pasó con el precio después de cerrar. Compara `P&L actual %` (hoy) contra `P&L al cierre real` (el que quedó registrado).

| Ticker | Cartera | Entrada | Precio entrada | MFE % | Fecha MFE | MAE % | Fecha MAE | P&L actual % | P&L al cierre real | Motivo salida | ¿Tocó +5%? | ¿Tocó objetivo? | Objetivo inicial |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| SNDK | A | 2026-07-21 | 1510.26 | +12.32% | 2026-07-23 | -33.91% | 2026-07-29 | -32.30% | -12.00% | stop | ✅ sí | no | 2354.39 |
| WDC | A | 2026-07-21 | 529.78 | +8.79% | 2026-07-23 | -20.32% | 2026-07-28 | -12.11% | -12.02% | stop | ✅ sí | no | 779.80 |
| COHR | A | 2026-07-21 | 307.26 | +6.68% | 2026-07-23 | -28.06% | 2026-07-29 | -27.44% | -12.00% | stop | ✅ sí | no | 439.68 |
| CIEN | A | 2026-07-22 | 396.95 | +4.75% | 2026-07-23 | -18.49% | 2026-07-29 | -17.79% | -12.00% | stop | no | no | 565.71 |
| MRVL | A | 2026-07-21 | 205.56 | +4.56% | 2026-07-22 | -20.75% | 2026-07-29 | -19.63% | -12.43% | stop | no | no | 314.09 |
| KLAC | A | 2026-07-23 | 213.35 | +3.11% | 2026-07-24 | -19.15% | 2026-07-29 | -18.61% | -12.00% | stop | no | no | 307.37 |
| GLW | A | 2026-07-21 | 161.30 | +1.98% | 2026-07-21 | -29.01% | 2026-07-28 | -22.67% | -12.00% | stop | no | no | 214.07 |

## Resumen

- **Posiciones abiertas:** 23 (23 con datos de precio).
- **Ya tocaron +5% en algún momento (sin haberse cerrado):** 7 de 23.
- **Tocaron su objetivo inicial pero siguen abiertas:** 0 de 23 con objetivo conocido. **No es necesariamente un bug** — el objetivo se recalcula a diario y puede haber subido desde la entrada, o (en Cartera A) el mismo día se tocó también el stop y por diseño gana el stop (regla conservadora). Revisar caso a caso en `logs/decisiones-*.log` antes de asumir un fallo.
- **MFE promedio (abiertas):** +4.37%.