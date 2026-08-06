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

## 📊 Dashboard

**→ [sebas1331.github.io/centinela-sp500](https://sebas1331.github.io/centinela-sp500/)**

Panel web con todo el experimento de un vistazo. **Se regenera solo tras cada
post-cierre**, así que lo que se ve ahí es siempre la última sesión cerrada.

Está pensado **para el móvil primero**: en pantalla pequeña cada operación se
convierte en una tarjeta con el ticker y su P&L destacados, sin scroll lateral.

Qué hay dentro:

- **Cuatro cifras de cabecera** — operaciones cerradas, win rate global, P&L
  acumulado y posiciones abiertas ahora mismo.
- **P&L acumulado por cartera** — para A y para B, lo **realizado** (solo
  cerradas, ya no cambia) junto al **total** (realizado + marca a mercado de las
  abiertas, que se mueve cada día). Son **sumas de retornos equiponderados**, no
  una curva de capital compuesta: este experimento no modela asignación de
  capital, y el dashboard lo dice ahí mismo.
- **Comparativa A vs B** — nº de cerradas, win rate, expectancy, profit factor y
  el mejor y el peor trade de cada cartera, lado a lado.
- **Curva de equity** — evolución del P&L acumulado **realizado** de las dos
  carteras (A azul continuo, B violeta discontinuo), con referencia en 0%. Al
  pasar el ratón —o mantener el dedo en el móvil— aparece el detalle de esa
  fecha: acumulado y nº de cerradas de cada cartera. Es SVG dibujado a mano, sin
  ninguna librería de gráficos.
- **Tabla de todas las operaciones**, abiertas y cerradas, con filtros
  combinables (`Abiertas`, `Cerradas`, `Cartera A`, `Cartera B`, `Ganadoras`,
  `Perdedoras`), buscador por ticker y cualquier columna ordenable. El P&L de una
  posición abierta va precedido de **`~`**: es una marca a mercado contra el
  último cierre disponible, **no** un resultado realizado, y **no** cuenta en el
  win rate ni en el P&L acumulado.
- **Sección plegable MFE/MAE** de las posiciones abiertas, ordenada por MFE.
- **Tema claro/oscuro**, que respeta el del sistema y recuerda tu elección.

Es HTML+CSS+JS plano, sin frameworks ni CDNs: unos 25 KB que se sirven estáticos
desde [`docs/`](docs/). Todos los agregados los calcula
[`scripts/generar_dashboard.py`](scripts/generar_dashboard.py) en Python y viajan
ya hechos en `docs/datos.json` — el HTML solo pinta, así que no hay dos sitios
donde una misma cifra pueda salir distinta.

Para verlo en local hace falta servirlo (el navegador bloquea `fetch` sobre
`file://`):

```bash
python scripts/generar_dashboard.py
python -m http.server 8000 --directory docs   # y abrir http://localhost:8000
```

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
  resultados.py       vocabulario CERRADO de desenlaces de un escaneo
  reportes.py         reporte semanal y mensual
  runtime.py          preparación de datos compartida
  notificaciones.py   Telegram (DESACTIVADO por defecto)
scripts/              entrenar_inicial, escaneo_preapertura, escaneo_postcierre,
                      reentrenar_mensual, generar_reporte, vigilante
                      commit_y_push.sh, verificar_persistencia.sh
.github/workflows/    preapertura.yml, postcierre.yml, vigilante.yml,
                      reentrenamiento.yml
tests/                pruebas (pytest)
```

### Cómo se garantiza que nada falla en silencio

Un workflow verde que no escribe nada es **peor** que uno rojo: disimula el
fallo. Cuatro capas independientes lo impiden:

1. **Vocabulario cerrado** ([`resultados.py`](centinela/resultados.py)): cada
   escaneo declara su desenlace. Solo tres motivos permiten terminar sin commit;
   cualquier otro es un fallo. Un motivo nuevo no hereda el permiso de callarse.
2. **Commit verificado** ([`commit_y_push.sh`](scripts/commit_y_push.sh)): si el
   escaneo dijo `procesado`, tiene que haber cambios, commit y push; luego relee
   el remoto y comprueba autor y marca del run.
3. **Verificación independiente** ([`verificar_persistencia.sh`](scripts/verificar_persistencia.sh)):
   un job aparte clona el repo de nuevo desde GitHub y comprueba contra el remoto
   de verdad que la cabeza de `main` la escribió el bot en ese run.
4. **Vigilante** ([`vigilante.py`](scripts/vigilante.py)): a diario, comprueba que
   ninguna sesión exigible se haya quedado sin sus **dos** escaneos. Es la única
   capa que detecta lo que ningún run puede detectar por sí mismo: que falte un
   run entero.

## ⏰ Calendario de ejecución

El cron de Actions corre en **UTC**, no entiende el horario de verano y **se
retrasa muchísimo**: el 2026-07-27 los cinco disparos de la pre-apertura llegaron
entre **2 h 14 min y 2 h 55 min tarde**, todos pasada la apertura, y la sesión se
perdió con los cinco workflows en verde. Ese fallo dio forma al diseño actual.

| Workflow | Crons (UTC) | Ventana válida (ET) | Qué hace |
|---|---|---|---|
| Pre-apertura | 08:03, 08:53, 09:43, 10:33, 11:23, 12:13, 13:03 · L-V | de 4 h a 20 min **antes** de las 09:30 | Decide las entradas del día |
| Post-cierre | 20:07, 21:07, 22:07, 23:07 · L-V | desde 30 min **después** de las 16:00 | Ejecuta entradas, gestiona salidas, reportes |
| Vigilante | 14:37 · diario | — | Falla en rojo si faltó alguna sesión |
| Reentrenamiento | 06:07 del día 1 | — | Reajusta el modelo con datos nuevos |

La ventana pre-apertura es **asimétrica a propósito**, y esa asimetría es la
clave de todo:

- **Llegar pronto no cuesta nada.** Los datos son del cierre anterior.
- **Llegar tarde es irrecuperable.** Decidir después del *open* sería
  *look-ahead bias*: la compra se simula justo a ese precio.

Por eso los disparos salen **muy por delante** y el run que llega antes de hora
**se duerme** hasta las 08:45 ET (18:00 ET en el post-cierre) en vez de morir. El
que llega ya con retraso trabaja de inmediato. Resultado: el escaneo se hace a su
hora de siempre con retrasos de hasta 4 h 30 min, y aguanta hasta **5 h** antes de
perder el día. Todo sigue siendo **idempotente**: el primero que trabaja gana y
los demás terminan sin hacer nada.

Si la escalera de crons se toca, el test
`test_escalera_de_crons_preapertura_aguanta_retrasos` comprueba el rango entero
de retrasos en verano e invierno y avisa si se reabre el agujero.

---

## ✅ Cómo verificar desde el celular que el sistema está vivo

Abre el repo en el navegador o la app de GitHub y mira **la fecha del último
commit** en la portada. No hace falta nada más.

### Qué esperar cada día de mercado (lunes a viernes, salvo festivos)

| Cuándo | Mensaje del commit | Archivos que cambian |
|---|---|---|
| **08:45 ET** = **07:45 Ecuador** | `pre-apertura: decisiones de entrada` | `estado/estado.json`, `logs/decisiones-AAAA-MM-DD.log` |
| **18:00 ET** = **17:00 Ecuador** | `post-cierre: entradas/salidas y reportes` | `estado/estado.json`, `logs/…`, `datos/ath.json`, `bitacora.csv` y `bitacora.sqlite` |

> **En invierno** (noviembre-marzo) las horas de Ecuador coinciden con las de
> Nueva York: 08:45 y 18:00 en ambos husos.
>
> Si Actions va con retraso, el commit puede llegar **más tarde** (hasta las
> 09:10 ET la pre-apertura, sin tope el post-cierre). Lo que **no** puede pasar
> es que no llegue: eso es un fallo y se ve en rojo.

Los dos commits aparecen **aunque no haya ninguna operación**: cada escaneo deja
siempre constancia de lo que evaluó en `logs/decisiones-AAAA-MM-DD.log` y sella
su paso en `estado/estado.json`. Un día de mercado **sin commits es un fallo**,
no un día tranquilo.

Los viernes hay además un **reporte semanal** nuevo en [`reportes/`](reportes/), y
el primer día de mercado de cada mes, uno mensual.

### Si no aparece el commit

1. **Pestaña Actions → filtra por «Vigilante».** Corre todos los días a las 14:37
   UTC (09:37 Ecuador) y su único trabajo es comprobar que no falte ninguna
   sesión. Si está **verde**, el sistema está al día aunque a ti te parezca que
   no. Si está **rojo**, el propio error dice qué sesión y qué escaneo faltan.
2. **Filtra por «Escaneo pre-apertura» y «Escaneo post-cierre».** Abre el run del
   día y mira la línea `RESULTADO=` del paso de escaneo:

   | `RESULTADO=` | Qué significa | ¿Preocupa? |
   |---|---|---|
   | `procesado` | Trabajó y guardó. Su commit existe. | No |
   | `omitido:sin-mercado` | Era festivo. | No |
   | `omitido:ya-procesado` | Otro disparo del día ya lo hizo; busca su commit. | No |
   | `omitido:antes-de-ventana` | Llegó pronto y cedió el turno al siguiente disparo. | No |
   | `fallo:ventana-perdida` | **Se perdió la sesión.** Actions se retrasó más de 5 h. | **Sí** |

3. **Avísame** (Sebastián) con el **enlace del run rojo**. Copia la URL de la
   barra de direcciones; con eso basta.
4. Si **no hay ningún run** ese día → GitHub desactiva los crons de los repos sin
   actividad durante 60 días; basta con hacer un commit cualquiera para
   reactivarlos.
5. Arreglo manual de una sesión perdida: **Actions → Escaneo post-cierre → Run
   workflow**, marca *forzar* y pon la fecha de la sesión. Ojo: la pre-apertura
   **no** se recupera a posteriori, porque decidir entradas después de la
   apertura falsearía el experimento.

> **Un verde no puede significar «no hice nada».** Cada escaneo publica un
> resultado de un vocabulario cerrado ([`centinela/resultados.py`](centinela/resultados.py)),
> y tanto el paso de commit como un job de verificación independiente rompen en
> rojo si ese resultado no cuadra con lo que hay en el repositorio.

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
