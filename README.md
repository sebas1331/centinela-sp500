# 🛰️ Centinela SP500

Sistema **autónomo de paper trading** (dinero **100 % simulado**) sobre el S&P 500.
Cada día de mercado busca acciones caídas ≥30 % desde su máximo histórico (ATH),
filtra por salud fundamental y usa un modelo de *machine learning* clásico para
apostar a un **rebote de +5 % en ≤10 días hábiles**. Corre solo en **GitHub
Actions** (sin depender de ningún ordenador encendido) y escribe una **bitácora
auditable** en este repositorio. Compara dos carteras en paralelo: **A (con stop)**
y **B (sin stop)**.

> ⚠️ **Advertencia.** Esto es un **experimento educativo con dinero simulado**.
> No promete rentabilidad ni garantiza nada. Todas las métricas se reportan tal
> cual, aunque sean malas. Rentabilidad pasada simulada **no** predice el futuro.
> Presupuesto del proyecto: **$0** (solo datos gratuitos, ninguna API de pago).

---

## 📱 Cómo consultar la bitácora desde el celular

Todo el registro vive en el propio repositorio. Desde el navegador del teléfono:

- **[`bitacora.csv`](bitacora.csv)** — todas las operaciones (abiertas y cerradas),
  con precio de entrada/salida, objetivo, stop, motivo de salida y **P&L %**.
  GitHub lo muestra como tabla.
- **[`reportes/`](reportes/)** — reporte semanal y mensual en Markdown, se leen
  cómodo en el móvil.
- **[`estado/estado.json`](estado/estado.json)** — posiciones abiertas ahora mismo.
- **[`logs/`](logs/)** — `decisiones-YYYY-MM-DD.log`: auditoría de **todos** los
  candidatos evaluados cada día y por qué se entró o no en cada uno.

Consejo: en la app de GitHub o desde el navegador, marca este repo como favorito.
Cada escaneo hace *commit* automático, así que siempre verás lo último.

---

## 🧠 Cómo se decide una ENTRADA

En la **pre-apertura** (~08:45 ET) se ejecuta el escaneo que *decide* las entradas
del día (la compra se simula luego al **precio de apertura oficial** de esa sesión):

1. **Filtro base** — solo acciones en **drawdown ≥30 %** respecto a su ATH.
   El ATH se calculó una vez con todo el historial y se actualiza incremental.
2. **Modelo (señal)** — probabilidad de +5 % en ≤10 días hábiles; se exige
   `prob ≥ umbral` (umbral calibrado para **precisión**: pocas señales, buenas).
3. **Veto fundamental** — si la empresa está en **deterioro grave** se descarta,
   salvo que la señal sea *excepcional* (`prob ≥ 0.70`).
4. **Sentimiento** — score VADER de titulares recientes como matiz secundario.

**Features del modelo (12, solo técnicos):** RSI(14); retornos a 5/20/60 días;
distancia a las medias móviles de 20/50/200; ATR %; volumen relativo; magnitud del
drawdown; días desde el ATH; gap overnight.

> **Decisión de honestidad importante.** Los datos gratuitos de yfinance **no
> tienen historial *point-in-time*** de fundamentales/analistas/sentimiento. Meterlos
> como features del modelo histórico sería *look-ahead bias* (usar el ROE de hoy para
> predecir 2019). Por eso el **modelo se entrena solo con técnicos** (con historial
> completo y sin *leakage*), y los fundamentales/analistas/sentimiento actúan como
> **capa de filtro/veto en vivo**, quedando registrados en la bitácora.

## 🎯 Cómo se decide una SALIDA

El **objetivo es variable** y se **recalcula en cada escaneo** (cada cambio se
registra con su motivo). El objetivo inicial = **máximo entre +5 %** y un
**objetivo técnico** (ATR, resistencia de 20 días, precio objetivo de analistas),
con un tope por ATR. Salidas posibles:

- **Objetivo tocado** → venta simulada al precio objetivo (si hay gap al alza, al
  open real).
- **Límite de tiempo** → 10 días hábiles (~2 semanas): salida al cierre del día 10.
- **Stop loss (solo Cartera A)** → basado en ATR.

**Supuestos conservadores:** si en un mismo día se tocan stop y objetivo, gana el
**stop** (peor caso); si hay gap más allá del nivel, se ejecuta al **open real**.

### ¿Por qué el stop es por ATR y no fijo (−7 %)?
Un stop fijo castiga por igual a una utility tranquila y a una tech muy volátil.
El stop por **ATR** (`entrada − 2×ATR(14)`, acotado entre −3 % y −12 %) se adapta a
la volatilidad real de cada acción, evitando que el ruido normal de una acción
volátil dispare el stop antes de tiempo.

## 🧪 Experimento A vs B
Mismas entradas en ambas carteras. **A** usa stop (ATR); **B** no usa stop (solo
objetivo o tiempo). El objetivo es concluir **con datos** cuál conviene. Spoiler
del backtest: en esta estrategia (comprar sobreventa esperando rebote) el stop
tiende a **cortar rebotes que habrían recuperado** → B suele salir mejor. Se
seguirá midiendo en vivo.

---

## 📊 Resultados del backtest (honestos)

Validación **walk-forward estricta** (entrenar hasta *t*, evaluar el bloque
siguiente; nunca *k-fold* aleatorio) + **holdout del último año usado una sola vez**.
Ventana: **11 años**, **491 tickers**, **233 218 eventos** (filas en drawdown ≥30 %);
tasa base de +5 % en 10 días: **51.2 %**. Modelo ganador: **regresión logística**
(vs *gradient boosting*), umbral **0.79**. Detalle completo en
[`reportes/backtest_inicial.md`](reportes/backtest_inicial.md).

**Señal (fuera de muestra)**

| Conjunto | Señales | Precisión | Recall | Base rate | AUC |
|---|---|---|---|---|---|
| Walk-forward | 7 826 | **80.1 %** | 6.9 % | 52.7 % | 0.646 |
| Holdout (1 vez) | 659 | **68.0 %** | 2.9 % | 45.6 % | 0.624 |

**Trading — Cartera A (con stop) vs B (sin stop)**, $1 000 nominales por operación

| Periodo | Cartera | Ops | Win rate | Expectancy | Profit factor | Drawdown máx |
|---|---|---|---|---|---|---|
| Walk-forward | A | 7 826 | 54.9 % | 3.85 % | 1.81 | −20.1 % |
| Walk-forward | B | 7 826 | 65.6 % | 6.41 % | 2.77 | −11.9 % |
| Holdout | A | 659 | 51.6 % | 1.74 % | 1.43 | −20.6 % |
| Holdout | B | 659 | 53.6 % | 2.03 % | 1.53 | −19.8 % |

**Lectura honesta:** predecir rebotes de corto plazo es genuinamente difícil (el
AUC es modesto, ~0.65). El valor está en la **precisión del umbral alto**: pocas
señales, pero mejores que la tasa base. En holdout el modelo **degrada** (68 % vs
80 %) pero sigue por encima del azar. La Cartera **B** domina en el histórico, pero
la ventaja se estrecha en holdout.

## ⚠️ Limitaciones (sin maquillar)

- **Sesgo de supervivencia:** se usan los constituyentes **actuales** del S&P 500
  para el backtest histórico (no hay historial *point-in-time* gratuito). Esto
  **infla** algo los resultados; algunas empresas que quebraron o salieron del
  índice no aparecen.
- **yfinance no es oficial:** puede fallar o cambiar sin aviso. Hay reintentos,
  backoff, caché y un *fallback* (stooq, que puede estar bloqueado).
- **Sentimiento débil:** VADER no está pensado para titulares financieros; es solo
  un matiz secundario.
- **El objetivo no se recalcula día a día en el backtest** (el sistema en vivo sí);
  es una aproximación conservadora.
- **Sin costes/impuestos/slippage** más allá de los supuestos conservadores de
  ejecución. Es *paper trading*.

---

## 🏗️ Arquitectura

```
centinela/            paquete Python
  config.py           todas las constantes (umbrales, features, ventanas)
  calendario.py       ¿hay mercado hoy? ¿ventana correcta? (exchange_calendars)
  datos.py            yfinance con reintentos/backoff/lotes + caché parquet + stooq
  universo.py         S&P 500 desde Wikipedia (semanal) + respaldo local
  ath.py              ATH inicial (todo el historial) + actualización incremental
  features.py         12 features técnicos, sin look-ahead
  etiquetado.py       etiqueta +5% en 10 días hábiles
  fundamentales.py    score de salud financiera + vetos de deterioro
  sentimiento.py      VADER sobre titulares (yfinance news)
  modelo.py           gradient boosting / logística + calibración + umbral
  backtest.py         walk-forward + holdout + backtest de trading
  objetivos.py        objetivo variable (ATR/resistencia/analistas) y stop ATR
  ejecucion.py        primitivo puro de salida (reglas conservadoras)
  simulador.py        motor en vivo de las 2 carteras
  bitacora.py         SQLite (fuente de verdad) + espejo CSV + decisiones.log
  estado.py           estado persistente (posiciones, idempotencia)
  reportes.py         reporte semanal y mensual
  runtime.py          preparación de datos compartida
  notificaciones.py   Telegram (DESACTIVADO por defecto)
scripts/              entrenar_inicial, escaneo_preapertura, escaneo_postcierre,
                      reentrenar_mensual, generar_reporte
.github/workflows/    preapertura.yml, postcierre.yml, reentrenamiento.yml
tests/                pruebas (pytest)
```

## ⏰ Calendario de ejecución

El cron de Actions corre en **UTC**, no entiende el horario de verano y **se
retrasa** (el 2026-07-20 un disparo llegó **2 h 12 min tarde**). Por eso cada
escaneo tiene **varios disparos escalonados**: el primero que caiga dentro de la
ventana hace el trabajo y los demás terminan sin hacer nada (todo es
**idempotente**).

| Workflow | Crons (UTC) | Ventana válida (ET) | Qué hace |
|---|---|---|---|
| Pre-apertura | 10:45, 11:15, 11:45, 12:15, 12:45 · L-V | de 4 h a 20 min **antes** de las 09:30 | Decide las entradas del día |
| Post-cierre | 22:00, 22:30, 23:00, 23:30 · L-V | desde 30 min **después** de las 16:00 | Ejecuta entradas, gestiona salidas, reportes |
| Reentrenamiento | 06:00 del día 1 | — | Reajusta el modelo con datos nuevos |

La ventana pre-apertura es **asimétrica a propósito**: ancha hacia atrás (correr
temprano es inofensivo, los datos son del cierre anterior) y **tajante en la
apertura**, porque decidir después del *open* sería *look-ahead bias* — la
compra se simula justo a ese precio.

---

## ✅ Cómo verificar que el sistema está vivo (desde el celular)

Abre el repo en el navegador o la app de GitHub y mira **la fecha del último
commit** en la portada. No hace falta nada más.

**Qué commits esperar cada día de mercado (lunes a viernes, salvo festivos):**

| Cuándo (hora Ecuador, verano) | Mensaje del commit | Archivos que cambian |
|---|---|---|
| entre **05:45 y 08:10** | `pre-apertura: decisiones de entrada` | `estado/estado.json`, `logs/decisiones-AAAA-MM-DD.log` |
| entre **17:00 y 18:30** | `post-cierre: entradas/salidas y reportes` | `estado/estado.json`, `logs/…`, `datos/ath.json`, y `bitacora.csv` si hubo operaciones |

> En invierno (noviembre-marzo) suma **1 hora** a esos rangos: Ecuador y Nueva
> York quedan a la misma hora.

Los dos commits aparecen **aunque no haya ninguna operación**: el escaneo siempre
deja constancia de lo que evaluó en `logs/decisiones-AAAA-MM-DD.log`. Un día de
mercado **sin commits es un fallo**, no un día tranquilo.

Los viernes hay además un **reporte semanal** nuevo en [`reportes/`](reportes/), y
el primer día de mercado de cada mes, uno mensual.

### Si no cambian

1. Entra en la pestaña **Actions** del repo. Los workflows ahora **fallan en
   rojo** si trabajan y no consiguen guardar, así que lo normal es que el
   problema sea visible ahí mismo.
2. Si ves un run **en rojo** → ábrelo y lee el paso *«Commit y push
   verificados»*; el mensaje de error dice exactamente qué falló.
3. Si todo está **en verde pero sin commits** → abre el run y busca la línea
   `RESULTADO=` del paso de escaneo:
   - `RESULTADO=omitido:sin-mercado` → era festivo. Todo bien.
   - `RESULTADO=omitido:ya-procesado` → otro disparo del día ya hizo el trabajo;
     busca su commit. Todo bien.
   - `RESULTADO=omitido:fuera-de-ventana` en **todos** los disparos → Actions se
     retrasó más de lo previsto. Es el fallo que hay que reportar.
4. Si **no hay ningún run** ese día → GitHub desactiva los crons de los repos sin
   actividad durante 60 días; basta con hacer un commit cualquiera para
   reactivarlos.
5. Arreglo manual en cualquier caso: **Actions → Escaneo post-cierre → Run
   workflow**, marca *forzar* y pon la fecha de la sesión perdida.

## 🔁 Autoaprendizaje sin sobreoptimizar
- Reentrenamiento walk-forward **mensual** con datos nuevos.
- Análisis post-trade automático (patrones por sector, motivo de salida, etc.).
- **Regla dura:** ningún cambio de umbral/features/stop con menos de **30
  operaciones cerradas nuevas**; todo cambio se registra en
  [`CHANGELOG.md`](CHANGELOG.md) con evidencia. El holdout nunca se reutiliza.

## 🛠️ Uso local

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python scripts/entrenar_inicial.py           # entrenamiento + backtest (pesado)
python scripts/escaneo_preapertura.py --forzar --fecha 2026-07-17   # prueba
python scripts/escaneo_postcierre.py  --forzar --fecha 2026-07-17
python scripts/generar_reporte.py semanal
pytest -q                                    # tests
```

## 🔔 Notificaciones (opcional, desactivadas)
Módulo de Telegram listo pero **apagado**. Para activarlo en el futuro: definir
las variables de entorno `CENTINELA_NOTIF=on`, `CENTINELA_TELEGRAM_TOKEN` y
`CENTINELA_TELEGRAM_CHAT_ID` (p. ej. como *secrets* del repo). Sin ellas, no envía
nada y todo sigue funcionando.
