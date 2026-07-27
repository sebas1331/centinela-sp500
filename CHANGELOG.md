# CHANGELOG — Centinela SP500

Registro de cambios del sistema. **Regla dura:** ningún cambio de umbral, features
o stop se aplica con menos de 30 operaciones cerradas nuevas, y todo cambio se
documenta aquí con su justificación y evidencia estadística. El holdout (último
año) nunca se reutiliza para tunear.

## 2026-07-27 — El día que se perdió una sesión en verde (v0.2.0)

**Infraestructura y persistencia. No se tocó el modelo, el umbral, el stop ni el
backtest.**

### Qué pasó

Los cinco disparos programados de la pre-apertura arrancaron con **2 h 14 min a
2 h 55 min de retraso** (crons de 10:45–12:45 UTC → arranques reales a las 13:29,
13:43, 13:59, 15:10 y 15:30 UTC). Todos aterrizaron **a partir de las 09:30 ET**,
es decir en la apertura o después. La ventana pre-apertura tiene un techo duro 20
min antes del *open* —decidir después sería *look-ahead bias*, porque la compra se
simula justo a ese precio—, así que los cinco escaneos devolvieron
`omitido:fuera-de-ventana`. El paso de commit trataba **cualquier** `omitido:*`
como legítimo. Resultado: cinco workflows en verde, cero escrituras y la sesión
del 27 de julio perdida sin una sola señal de alarma.

La escalera anterior toleraba como mucho 2 h 25 min de retraso. Ese día el mínimo
fue 2 h 14 min sobre un cron que ya salía tarde, y **falló entera**.

### Qué se cambió

- **Vocabulario cerrado de desenlaces** (`centinela/resultados.py`). "Llegué
  pronto" y "llegué tarde" dejan de ser el mismo `omitido:fuera-de-ventana`.
  Perder la ventana es ahora `fallo:ventana-perdida`, sale con código 1 y pinta
  el workflow de **rojo**. Solo tres motivos permiten terminar sin commit, y la
  lista es cerrada: un motivo desconocido se trata como fallo.
- **Espera en vez de muerte.** El escaneo que llega antes de hora duerme hasta su
  momento preferido (08:45 ET / 18:00 ET) en lugar de abortar, con un tope de 120
  min para no retener el turno de concurrencia.
- **Escalera de crons rediseñada.** Pre-apertura: 08:03–13:03 UTC (7 disparos).
  Post-cierre: 20:07–23:07 UTC (4 disparos). Minutos no redondos, donde la cola
  de Actions está menos congestionada. Aguanta retrasos de **0 a 5 h** en verano
  e invierno, y sigue haciendo el trabajo **a las 08:45 ET** con retrasos de
  hasta 4 h 30 min.
- **Verificación independiente** (`scripts/verificar_persistencia.sh`): un job
  aparte clona el repo desde GitHub y comprueba contra el remoto real que la
  cabeza de `main` la escribió `centinela-bot` en ese run.
- **Commit trazable**: cada commit del bot lleva `run: <id> | workflow: … |
  resultado: …` en el cuerpo, y `commit_y_push.sh` verifica autoría y marca de
  run releyendo el remoto.
- **Vigilante** (`vigilante.yml`, diario a las 14:37 UTC): comprueba que ninguna
  sesión exigible se quedó sin sus **dos** escaneos y falla en rojo si falta
  alguno. Es la única capa capaz de detectar que falta un run entero.
- **Sello de prueba de vida** en `estado/estado.json` (`ultima_ejecucion`), con
  el id y la URL del run de Actions que lo escribió.
- **Log diario auto-descriptivo**: la cabecera de cada bloque dice qué escaneo lo
  escribió, para poder auditar sesión a sesión que ambos corrieron.
- **Reentrenamiento mensual**: se le exige `procesado` en vez del `omitido:` fijo
  que llevaba, que dejaba pasar en verde un reentrenamiento sin salida.

### Evidencia

- Reproducción local en sandbox: ambos escaneos con `--forzar` **sí** escriben en
  disco (`estado.json`, `logs/decisiones-2026-07-27.log`, `bitacora.*`,
  `datos/ath.json`). El fallo nunca fue de permisos ni de escritura: `permissions:
  contents: write`, `persist-credentials` y el `GITHUB_TOKEN` funcionaban, como
  demuestran los 13 commits del bot del 20 al 24 de julio.
- El escenario exacto del 27 de julio, ejecutado contra el código nuevo, termina
  en `RESULTADO=fallo:ventana-perdida` con código de salida **1**.
- La escalera vieja falla el test de retrasos desde los 150 min; la nueva cubre
  el rango completo de 0 a 300 min en ambos regímenes horarios.
- 41 tests en verde, incluidos los nuevos de `tests/test_persistencia_escaneos.py`.

### Reglas que se mantuvieron

Cero `|| true`, `continue-on-error`, `2>/dev/null` o `set +e` nuevos (hay un test
que lo verifica). Ningún secreto nuevo: solo `GITHUB_TOKEN`. Idempotencia, doble
cron EDT/EST, `--forzar` y chequeo de calendario bursátil, intactos.

## 2026-07-18 — Puesta en marcha (v0.1.0)

- Entrenamiento inicial y backtest walk-forward sobre 11 años y 491 tickers del
  S&P 500 (233 218 eventos en drawdown ≥30 %).
- Modelo elegido: **regresión logística** calibrada (superó al *gradient boosting*
  por precisión walk-forward: 80.1 % vs 72.4 %).
- Umbral de probabilidad fijado en **0.79** (calibrado para precisión sobre las
  predicciones walk-forward; el holdout no se usó para elegirlo).
- Stop de la Cartera A: **2×ATR(14)**, acotado entre −3 % y −12 %.
- Objetivo variable: máximo entre +5 % y objetivo técnico (2×ATR, resistencia de
  20 días, precio objetivo de analistas), con tope de 6×ATR.
- Métricas honestas publicadas en `reportes/backtest_inicial.md` y en el README.

_A partir de aquí, cada reentrenamiento mensual y cualquier ajuste quedará
registrado debajo con su evidencia._
