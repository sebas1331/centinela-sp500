# Auditoría de fiabilidad del rendimiento

_Periodo: 2026-07-21 → 2026-09-24 (47 sesiones bursátiles, ~2.2 meses). Generado por `scripts/auditar_fiabilidad.py`._

Este informe no lo escribe el sistema que opera: lo escribe un auditor que lee su bitácora y trata de romperla. Todo lo que sigue sale de `bitacora.csv` y de precios descargados de nuevo, no de ninguna cifra que el propio sistema hubiera publicado antes.

> **Aviso sobre el tamaño de la muestra.** Son 125 operaciones cerradas en 47 sesiones. Cualquier ratio anualizado de aquí (CAGR, Sharpe) es una extrapolación violenta de dos meses y se publica porque se ha pedido, no porque sea predictivo. El error estándar del Sharpe con esta muestra ronda ±0,3.

## 1. Rentabilidad de la CUENTA, no suma de trades

Cuenta de $10,000 por cartera, dividida en 20 slots. Cada entrada invierte un slot al precio de apertura y el capital compone al cerrar. El modelo completo y sus decisiones interpretativas están documentados en `centinela/cuenta.py`.

| | Cartera A (con stop) | Cartera B (sin stop) |
|---|---|---|
| Capital final (bruto) | $11,394 | $11,318 |
| Rentabilidad (bruta) | +13.94% | +13.18% |
| **Capital final (neto)** | $11,207 | $11,173 |
| **Rentabilidad (neta)** | +12.07% | +11.73% |
| CAGR anualizado (neto) | +84.24% | +81.22% |
| Drawdown máximo (neto) | -10.44% | -12.16% |
| Sharpe aprox. (neto) | 1.92 | 1.83 |

Para comparar, la cifra que el dashboard publicaba hasta ahora —la suma de los retornos de cada operación— era de +244.10% (A) y +199.26% (B). La diferencia con la rentabilidad de la cuenta no es un error de ninguna de las dos: son dos preguntas distintas. La suma de retornos mide la estrategia; la cuenta mide el dinero, y el dinero está limitado a 20 posiciones a la vez.

## 2. Fricciones

Comisión y spread de 0.10% **por lado**, más 0.15% de slippage en las órdenes a mercado: compra market-on-open, stop disparado y venta market-on-close por tiempo. La salida por objetivo NO paga slippage, porque es una orden límite y se ejecuta a su precio o mejor.

| Cartera | Bruto | Neto | Coste de la fricción |
|---|---|---|---|
| A | +13.94% | +12.07% | -1.87 pp |
| B | +13.18% | +11.73% | -1.45 pp |

Son 125 operaciones cerradas y 16 abiertas: en torno a -1.7 puntos de rentabilidad se van en costes. Es el precio de una estrategia que rota la cartera entera cada diez sesiones.

## 3. Look-ahead en la ejecución simulada

### 3.1 Lo que está BIEN

- **Stop antes que objetivo.** `centinela/simulador.py:evaluar_salida_dia` comprueba el stop primero: si en la misma sesión se tocaron el stop y el objetivo, la Cartera A sale por el stop. Es el supuesto conservador y está aplicado. Confirmado también en `centinela/ejecucion.py`, que usa el backtest.
- **Gaps al precio real.** Si la apertura salta por debajo del stop o por encima del objetivo, se ejecuta al open real, no al nivel teórico. Pesimista en el stop, realista en el objetivo.
- **La decisión de entrada no ve el open.** La pre-apertura corre con un techo duro 20 minutos antes de la apertura y el run que llega tarde muere en rojo en vez de decidir (`resultados.FALLO_VENTANA_PERDIDA`). La entrada se ejecuta al open de la sesión decidida, no al de un día posterior.
- **Ninguna sesión sin evaluar.** Las 47 sesiones del periodo tienen su post-cierre registrado en `logs/`, así que no hay barras que el simulador se haya saltado y en las que un stop hubiera saltado sin que nadie lo viera.

### 3.2 El sesgo OPTIMISTA que sí existe

**Las 13 salidas por objetivo del periodo —13 de 13— se ejecutaron exactamente en el máximo de la sesión.**

No es casualidad. El post-cierre hace dos cosas en este orden (`simulador.gestionar_posiciones`):

1. **Recalcula el objetivo** con `_atr(df)` y `resistencia_reciente(df)`, donde `df` ya incluye la barra del día que acaba de cerrar. La resistencia es el máximo de los últimos 20 highs, así que en un día de máximos **la resistencia es el máximo de hoy** y el objetivo se clava justo ahí.
2. **Evalúa la salida** contra ese objetivo recién puesto. La condición `high >= objetivo` se cumple con igualdad exacta y la venta se registra al máximo del día.

Un operador real no puede vender ahí: su orden límite del martes es la que calculó el lunes por la noche. El precio existió durante la sesión, pero no había ninguna orden esperándolo.

**Cuantificación.** Se ha vuelto a simular la bitácora entera cambiando una sola cosa: el objetivo de cada sesión es el que ya estaba puesto al abrirla (el calculado la víspera). Todo lo demás —stop primero, gaps al open, salida por tiempo al cierre del día límite— es idéntico.

| | Real (publicado) | Sin look-ahead | Diferencia |
|---|---|---|---|
| P&L medio por operación | +3.29% | +3.05% | -0.24 pp |
| Rentabilidad de la cuenta A (neta) | +12.07% | +11.14% | -0.93 pp |
| Rentabilidad de la cuenta B (neta) | +11.73% | +11.06% | -0.67 pp |

De 121 operaciones cerradas, 2 cambian de motivo de salida y 26 mueven su P&L más de 0,1 pp. Las más afectadas:

| Op. | Ticker | Real | Sin look-ahead | Diferencia |
|---|---|---|---|---|
| 130 | MRNA/A | objetivo +26.06% | objetivo +16.78% | -9.28 pp |
| 150 | MRNA/B | objetivo +23.59% | objetivo +15.87% | -7.72 pp |
| 68 | SMCI/B | objetivo +20.40% | objetivo +14.03% | -6.37 pp |
| 67 | SMCI/A | objetivo +20.40% | objetivo +14.03% | -6.37 pp |
| 45 | LITE/A | objetivo +41.80% | objetivo +35.74% | -6.05 pp |
| 28 | LITE/B | objetivo +38.29% | objetivo +32.39% | -5.90 pp |
| 140 | MRVL/B | objetivo +19.81% | objetivo +14.67% | -5.13 pp |
| 139 | MRVL/A | objetivo +19.81% | objetivo +14.67% | -5.13 pp |

**No se ha corregido nada en la lógica de decisión**, por instrucción expresa: este informe mide, no arregla. Lo que sí queda dicho es cuál es la cifra honesta —la de la columna 'sin look-ahead'— y dónde está el arreglo si algún día se hace: recalcular el objetivo **antes** de evaluar la salida, o evaluar contra el objetivo de la víspera.

## 4. Concentración

| | Cartera A | Cartera B |
|---|---|---|
| P&L realizado (neto) | $962 | $878 |
| Aportado por las 5 mejores | $774 (80%) | $627 (71%) |
| **Rentabilidad SIN las 5 mejores** | +4.26% | +5.38% |
| Operaciones en semiconductores | 57/71 | 40/54 |
| P&L de semiconductores | $813 (85%) | $694 (79%) |

**Sí, sigue siendo positivo sin las cinco mejores**, que es la pregunta que importaba. Pero el perfil es el de una estrategia que vive de pocas operaciones grandes: quitando 5 de 71 en A se evapora la mayor parte del resultado.

Y la concentración sectorial es el riesgo más serio del sistema. El filtro de partida —acciones a más del 30% de su máximo histórico— seleccionó en este periodo, casi en bloque, la cadena de valor del silicio. Esto no es una cartera diversificada del S&P 500: es una apuesta al ciclo de los semiconductores con 20 patas. Un giro del sector se llevaría por delante casi todas las posiciones a la vez, y ni el stop de la Cartera A protege de eso, porque saltaría en todas el mismo día.

Reparto del P&L por sector GICS (neto, Cartera A):

| Sector | P&L | Operaciones |
|---|---|---|
| Information Technology | $705 | 56 |
| Health Care | $195 | 5 |
| Industrials | $66 | 2 |
| Communication Services | $12 | 2 |
| Financials | $12 | 1 |
| Utilities | $-3 | 2 |
| Consumer Staples | $-24 | 3 |

## 5. Régimen de mercado: ¿habilidad o beta?

| | Cartera A | Cartera B | SPY (comprar y mantener) |
|---|---|---|---|
| Rentabilidad del periodo | +12.07% | +11.73% | +3.05% |
| Drawdown máximo | -10.44% | -12.16% | -3.06% |
| Sharpe aprox. | 1.92 | 1.83 | 1.35 |

- **Cartera A**: beta 1.72 contra SPY; alfa del periodo +6.51%.
- **Cartera B**: beta 1.73 contra SPY; alfa del periodo +6.15%.

El mercado subió +3.05% en estas 47 sesiones, así que el sistema batió al índice. Pero lo hizo con un drawdown 3.4 veces mayor, que es el precio que se pagó por ese exceso. Y dos meses de mercado alcista no distinguen entre una estrategia con alfa y una estrategia con beta alta; para eso hace falta un tramo bajista, que este periodo no tiene.

## Conclusión honesta

**1. La cuenta gana dinero, y menos del que parecía.** Neto de fricciones, +12.07% en A y +11.73% en B sobre $10,000, frente al +3.05% del SPY. Descontado además el look-ahead de la ejecución, queda en +11.14% y +11.06%. Sigue batiendo al índice.

**2. Lo ROBUSTO es el filtro de entrada.** Que la selección siga siendo rentable sin sus cinco mejores operaciones (+4.26% en A, +5.38% en B) y con 71 cierres dice que la ventaja no depende de un golpe de suerte aislado. El stop de A tampoco destruye valor: A y B terminan a menos de un punto de distancia.

**3. Lo FRÁGIL es la ejecución de las salidas.** 13 de 13 salidas por objetivo se registraron en el máximo exacto de la sesión, un precio que ninguna orden límite real habría capturado. Infla el resultado en 0.24 puntos por operación y 0.93 puntos de rentabilidad de la cuenta. Es el único sesgo optimista encontrado, pero es sistemático, no anecdótico.

**4. Lo MÁS FRÁGIL es la concentración.** 57 de 71 operaciones y el 85% del P&L están en la cadena del silicio. Esto no es un sistema sobre el S&P 500: es una apuesta apalancada al ciclo de los semiconductores que hasta ahora ha salido bien. Un giro del sector golpea las 20 posiciones el mismo día y el stop no protege de una caída correlacionada.

**5. Dos meses no demuestran nada, y el CAGR del 84% es un artefacto aritmético.** 47 sesiones, todas en mercado alcista, sin un solo tramo bajista que separe el alfa de la beta. Las cifras de arriba son un punto de partida creíble y bien medido; no son una expectativa. Con dinero real, el tamaño de posición debería fijarse por el drawdown observado (-10.4%) y no por la rentabilidad.

