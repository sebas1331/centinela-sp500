# CHANGELOG — Centinela SP500

Registro de cambios del sistema. **Regla dura:** ningún cambio de umbral, features
o stop se aplica con menos de 30 operaciones cerradas nuevas, y todo cambio se
documenta aquí con su justificación y evidencia estadística. El holdout (último
año) nunca se reutiliza para tunear.

## 2026-08-06 — Dashboard en GitHub Pages y blindaje del Vigilante (v0.3.0)

**Infraestructura y presentación. No se tocó el modelo, el umbral (0.79), las
features, los objetivos, el stop ni el backtest.**

### El Vigilante #12 en rojo tras 15m02s

**No se colgó: nunca llegó a ejecutarse.** GitHub no consiguió asignarle máquina
—`The job was not acquired by Runner of type hosted even after multiple
attempts`— y el job se quedó **encolado** hasta agotar el `timeout-minutes: 15`.
La API de jobs lo confirma: la lista de `steps` venía **vacía**, ni siquiera
corrió *Set up job*. Incidencia de infraestructura de Actions, ajena al
repositorio. **El trading no se vio afectado**: los escaneos del día terminaron
todos en verde y la sesión se procesó con normalidad.

Contra la falta de runner no hay código posible. Lo que sí se controla es cuánto
tarda en verse, y de paso se cerraron todas las vías por las que *este* código
podría colgarse de verdad algún día:

- `timeout-minutes` de 15 → **5**. Un Vigilante sano tarda 30-45 s; pasados 5
  minutos o está roto o no hay máquina, y en ambos casos morir pronto y en rojo
  es mejor que un run colgado un cuarto de hora fingiendo que trabaja.
- **Timeout de socket global de 30 s.** Hoy el Vigilante no hace ni una petición
  de red, pero un import futuro que la hiciera heredaría el *default* de Python,
  que es esperar para siempre.
- **Timeout de 60 s** en el subproceso `git log`.
- **Cotas superiores explícitas** en todo lo que se itera: `MAX_SESIONES = 30`
  (aplicado de verdad: `--sesiones 999999` revisa 30) y
  `MAX_COMMITS_LISTADOS = 200`, para que el coste no crezca con el repositorio.

### Los dos escaneos "amarillos" de esa mañana: no era yfinance

Los runs de pre-apertura `31096040571` (42m09s) y `31098882077` (35m11s)
aparecieron cancelados. **No fue rate-limit ni red lenta: fue la escalera de
crons funcionando como está diseñada.** El disparo de las 10:46 UTC entró en el
grupo de concurrencia `centinela-escritura` y **durmió ~2 h** esperando su
ventana de las 08:45 ET (`ESPERA_VENTANA_MAX_MIN = 120`). Mientras tanto GitHub
solo mantiene **un run pendiente por grupo**, así que cada disparo nuevo
desalojaba al anterior. Las cuentas cuadran al segundo: el de las 11:08 murió a
los 42m09s = 11:50:20, justo cuando se encoló el de las 11:50; y ese murió a los
35m11s = 12:25:29, justo cuando se encoló el de las 12:25, que fue el que
finalmente hizo el escaneo en 4 s. Comportamiento correcto y sin pérdida de
trabajo. **Amarillo aquí significa "otro peldaño de la escalera llegó primero",
no "algo falló".**

### Dashboard en GitHub Pages

Panel estático servido desde `docs/`, **regenerado tras cada post-cierre**:
<https://sebas1331.github.io/centinela-sp500/>

- `scripts/generar_dashboard.py` lee `bitacora.csv`, `reportes/mfe_actual.md` y
  `estado/estado.json`, calcula **todos** los agregados en Python y los escribe
  en `docs/datos.json`. El HTML no calcula nada: solo pinta. Una sola fuente de
  verdad y comprobable por los tests sin navegador.
- **Solo lectura sobre el sistema de trading.** No toca modelo, umbral, features,
  objetivos, stops ni bitácora.
- **Job aparte** (`needs: [postcierre, mfe]`, el último del workflow), por la
  misma razón que `mfe`: si la publicación revienta, lo peor que pasa es que la
  web se quede con los datos de ayer, mientras la bitácora y su verificación
  contra el remoto ya quedaron cerradas y en verde varios jobs antes.
- **Idempotente sin aflojar la verificación.** La marca de "última
  actualización" es la fecha del último commit que tocó *datos* (no la hora de
  ejecución), así que dos pasadas iguales producen el mismo byte y no hay commit
  de ruido. El generador publica `cambios=si|no` y el paso de commit solo corre
  si hubo cambios — y entonces se exige commit, push y confirmación contra el
  remoto con el mismo `commit_y_push.sh` de siempre. No se añadió ningún motivo
  nuevo al vocabulario cerrado de `resultados.py`.
- Presupuestos verificados en cada ejecución: `index.html` < 50 KB (25 KB hoy) y
  `datos.json` < 500 KB (21 KB hoy).

### Tests

De 54 a **73**. Los 19 nuevos (`tests/test_dashboard.py`) cubren el contrato de
`datos.json`, la aritmética de win rate / expectancy / profit factor contra una
bitácora sintética de resultados conocidos, que las **posiciones abiertas no
contaminen** las estadísticas de cerradas, que el HTML esté bien formado y sea
autocontenido, la idempotencia, y las cotas del Vigilante.

Un test que ya existía (`test_los_jobs_que_escriben_hacen_checkout_de_la_punta_de_la_rama`)
cazó el job nuevo por tener un comentario entre `with:` y `ref:`. Se movió el
comentario; **el guardarraíl no se tocó**.

---

## 2026-07-28 — Checkout rancio en la cola de concurrencia (v0.2.1)

**Infraestructura. No se tocó el modelo, el umbral, el stop ni el backtest.**

Primer día completo con la arquitectura nueva. Los dos escaneos de producción
funcionaron **clavados en su hora**: post-cierre del lunes a las **18:00:05 ET**
(durmió 42 min esperando su ventana) y pre-apertura del martes a las **08:46:08
ET** (durmió 100 min). El vigilante detectó correctamente que la sesión del
2026-07-27 se quedó sin pre-apertura, incluso con `ultima_preapertura` ya
avanzada al 28: la comprobación por sesión sobre el log diario funcionó.

### El fallo

Un disparo de la escalera (12:19 UTC) salió **rojo**. `actions/checkout` sin
`ref` se trae el **SHA del evento**, es decir el estado del repo de cuando el run
se *encoló*, no de cuando arranca. Ese disparo esperó en la cola de concurrencia
a que el de las 11:04 terminara de dormir y commitear `dd3ebfb`; al arrancar leyó
un `estado.json` anterior a ese commit, no vio la marca de idempotencia, **repitió
el escaneo entero** y murió en un conflicto de rebase sobre `estado/estado.json`.

El grupo de concurrencia sí serializó los jobs; lo que falló fue que cada job
miraba una foto del repositorio congelada en el pasado. Con la escalera de crons
y las esperas de hasta 120 min, esa foto puede tener horas de antigüedad.

### Qué se cambió

- `ref: ${{ github.ref_name }}` en el `actions/checkout` de los tres jobs que
  escriben. Ahora cada job arranca con la punta real de la rama, así que la
  comprobación de idempotencia ve el trabajo del run anterior. (El job de
  verificación ya lo tenía, que es por lo que él sí leía el remoto correctamente.)
- `commit_y_push.sh`: un conflicto de rebase deja de reintentarse dos veces más.
  Reintentar no arregla un conflicto de contenido, solo entierra la causa bajo
  ruido. Ahora corta al primero y lo nombra: fallo de **idempotencia**, no de red.
- Test `test_los_jobs_que_escriben_hacen_checkout_de_la_punta_de_la_rama`, que
  falla si alguien vuelve a dejar un `checkout` sin `ref` en un job que escribe.

### Nota sobre los runs cancelados

Es normal ver algún disparo en gris (*cancelled*): cuando un run está trabajando
o durmiendo, el grupo de concurrencia deja uno en espera y descarta los que
lleguen después. No se pierde nada — el que trabaja ya está haciendo la sesión — y
los que llegan más tarde terminan en `omitido:ya-procesado`.

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
## 2026-08-02 — Reentrenamiento mensual
- Datos: 232,566 eventos, hasta 2026-07-17.
- Precisión walk-forward (fresca): 80.2% (señales=14332).
- Operaciones cerradas nuevas desde 2026-07-18: 12.
- Umbral SIN cambios (0.79). Regla dura: se requieren ≥30 cierres nuevos y evidencia. Solo se re-ajustaron los pesos con datos nuevos.
