# CHANGELOG — Centinela SP500

Registro de cambios del sistema. **Regla dura:** ningún cambio de umbral, features
o stop se aplica con menos de 30 operaciones cerradas nuevas, y todo cambio se
documenta aquí con su justificación y evidencia estadística. El holdout (último
año) nunca se reutiliza para tunear.

## 2026-10-05 (4) — Entradas tardías sin umbral de coste; Chromium desde caché

- **Coste de ejecución.** El umbral del 0,5 % se aplica solo a las **compras
  normales**. Las entradas tardías enseñan su coste pero no pintan: son caras por
  diseño. En su lugar, **ámbar si más de 2 de las últimas 10 compras decididas
  acabaron como entrada tardía**: señal de que la compra normal sigue fallando.
  Hoy: 1 de 2 (WDC; MRNA y CTVA fuera por serie rota). Semáforo, verde.
- **Chromium desde caché, sin apt.** El paso tardaba 22-31 s casi siempre, pero
  el 05/10 tardó **826 s**: el mirror de apt de Ubuntu se atascó bajando fuentes
  y mesa (pausas de 343 s y 252 s). Un vigilante de rescate no puede esperar eso.
  Nueva acción `.github/actions/chromium` en los 12 workflows: navegador desde
  `actions/cache`, `playwright install chromium` sin `--with-deps`, comprobación
  de que arranca (`scripts/probar_chromium.py`) y apt **solo** si no arranca,
  dicho en el log. El tiempo queda en el resumen de cada job.

---

## 2026-10-05 (3) — Cuatro errores de la página Operativa

- **Signo del slippage.** `registrar_ejecucion` decidía el lado con
  `tipo == COMPRA`, así que una ENTRADA TARDÍA llevaba el signo de una venta:
  WDC pagó 439,71 frente a una apertura de 429,93 y la página enseñaba
  −2,27 % en verde. Ahora el lado sale de `lado_de` y la convención es la misma
  para todo: **positivo = coste** (comprar más caro o vender más barato que la
  referencia), en rojo. Revisadas todas las filas: solo WDC estaba mal (la de
  CTVA ya se corrigió a mano el 02/10); la fila de WDC queda en +2,2736 %.
- **Equity.** XTB devuelve `equity` igual al saldo: no suma lo que valen las
  acciones al contado (con WDC abierta, 1.758,84 $ de menos). Con el modelo de
  caja medido esta mañana, **equity = saldo + valor de mercado** y el efectivo es
  el saldo. El P&L abierto se calcula con el precio de mercado (el de XTB
  también venía a 0). Si falta el precio de alguna posición, no se enseña un
  equity a medias: queda vacío. El campo de XTB se guarda como `equity_xtb`.
- **Posiciones.** WDC salía sin objetivo ni stop (el simulador no los tiene hasta
  el post-cierre) y con el precio actual igual al de entrada (XTB da 0 y se caía
  a la entrada). La tabla usa ahora los niveles de la orden de hoy —los mismos
  que lee el vigilante— y nunca el precio de entrada como actual. Con el latido,
  la página repinta posiciones y cuenta con el bid y los niveles del vigilante.
  La foto de la cuenta (`estado_broker.volcar`) toma el precio de XTB si es
  positivo, si no el bid del vigilante o una cotización (`precio_fuente`).
- **Notas largas.** El historial de órdenes cortaba la nota a 80 caracteres (y
  el detalle de los componentes a 90). Ahora se ve el principio, cortado por
  palabra, con un «ver más» que la despliega entera.

---

## 2026-10-05 (2) — Sin healthchecks.io: el vigilante se relanza solo; semáforo en sesiones; coste de ejecución

### Fuera healthchecks.io

Decisión del usuario: no se usa. Se retira entera la integración del 02/10:
`centinela/aviso.py`, los pings y la pausa del vigilante, los secrets que el
workflow esperaba (`HEALTHCHECKS_PING_URL`, `HEALTHCHECKS_API_KEY`; nunca se
llegaron a crear), su sección del README, el campo `aviso` del latido y el ámbar
«Sin aviso externo» de la página.

### En su lugar: relanzar, sin emails

Si el latido se corta con posiciones abiertas y el mercado abierto:

1. **Supervisor** (`scripts/supervisor_vigilante.py`), en el mismo job: si el
   proceso termina mal o se cuelga (su latido local quieto 6 min), lo reinicia
   en segundos. Cada reinicio es `::error::`, va en el latido y el job acaba en
   rojo. El relevo de las 5 h 45 se cuenta desde el inicio del job, no del
   proceso.
2. **Guardián** (`scripts/guardian_vigilante.py`), job paralelo en otro runner:
   si el latido publicado lleva más de 11 min parado, lanza un vigilante de
   **rescate** en su propio grupo de concurrencia (no espera a que GitHub dé por
   muerto el run viejo). 11 > 10: un vigilante vivo que no puede publicar se
   retira solo a los 10, así que nunca vigilan dos a la vez. En un relevo, el
   guardián del sucesor no confunde al saliente —que sigue latiendo— con un
   reemplazo: «más nuevo» es «arrancó después», no «latió después».
3. **Escalera**: cada peldaño del pre-apertura vuelve a llamar al vigilante;
   `hace_falta` arranca uno si el latido está muerto o si hay posiciones en XTB
   (`estado/broker.json`), y no si falta más de 45 min para abrir.

En la página, un corte del latido es **rojo** (antes ámbar), también si ya se
recuperó; los reinicios, ámbar.

### Semáforo: antigüedad en sesiones de mercado

«No corre desde hace» ya no cuenta horas (30 h) sino **sesiones completas**
saltadas. Los cuatro ámbares de esta mañana —«60 h»— eran un fin de semana.

### Coste de ejecución de las compras

Precio pagado en XTB frente a la apertura del simulador, compras normales y
entradas tardías por separado, media de las últimas 10 de cada grupo; **ámbar si
alguna supera el 0,5 %**. Hoy: compras normales sin datos (todas las enviadas a
XTB fueron rechazadas) y entradas tardías +2,27 % (WDC, la única; CTVA queda
fuera por serie rota). El semáforo está, por tanto, en ámbar por el coste de las
entradas tardías.

### Publicado también

`e2355b4`: una compra de hoy cuenta como posición que vigilar.

---

## 2026-10-05 — Prueba real superada; dos fallos del arranque corregidos

**Prueba real en la demo** (run 37320179922, 09:53 ET): compra de 1 acción de
F a 12,18; el vigilante, con un objetivo artificial en 12,14, vio el bid 12,17,
vendió y la confirmación dijo **ejecutada** («F.US bajó de 1 a 0 acciones») en
5,9 s. Precio deducido del saldo: 12,17.

**Modelo de saldo de XTB, medido: CAJA.** Al comprar, el saldo bajó exactamente
el importe (29.891,88 → 29.879,70, Δ −12,18 = 1 × 12,18). Las ventas se deducen
con `saldo-caja`; el cálculo del modelo `pnl` se mantiene como contraste, pero
ya no es una incógnita.

**Fallos del arranque de hoy:**

- **El vigilante no arrancó tras la compra fallida de WDC.** El ejecutor murió
  sin escribir `posiciones_xtb`, y GitHub compara `'' != '0'` convirtiendo los
  dos en el número 0: la condición dio falso. Ahora compara texto con `format`.
- **Falso rojo del Vigilante general el 03/10 y el 04/10**: daba por «no
  reportada» la compra RECHAZADA de CTVA del 02/10, porque solo miraba las
  órdenes enviadas. Una orden con fila en `bitacora_broker.csv` está reportada;
  el rechazo ya pinta rojo por su propio camino.

**Decidido el mismo día: las compras salen 5 minutos después de abrir**
(`EJECUTOR_COMPRAS_MIN_TRAS_APERTURA = (5, 60)`). Es hipótesis con dos casos:
si una compra a las 09:35 vuelve a perderse, queda descartada. Contexto:
la compra de WDC se mandó a las 09:30:31
(11 s después de abrir) y XTB la aceptó y la descartó (orden 917440474; 0
posiciones y saldo intacto en la auditoría de las 09:51). El 02/10 CTVA falló
igual a las 09:30:19 — atribuido entonces a los niveles, pero hoy no se mandaron
niveles. Las compras que sí entraron fueron a las 09:52, 09:53 y 10:06.

---

## 2026-10-02 (5) — Las ventas, de verdad; y un semáforo que no miente

La auditoría del día dio «no listo»: las compras funcionaban, pero **las ventas
no eran fiables** y **el semáforo no reflejaba la realidad**. Nada de esto toca
la decisión (modelo, umbral 0.79, features, objetivos, stop).

### Las ventas

- **`confirmar()` reconoce ventas.** Daba una orden por buena solo si la posición
  CRECÍA, así que toda venta correcta salía «rechazada» (CTVA, 134 × 11,97).
  Ahora: compra si crece, venta si baja o desaparece, y una venta **parcial** es
  ejecutada con lo que de verdad se movió (columna `acciones`, nota `PARCIAL`).
  Lo mismo en la resolución de órdenes ambiguas.
- **Ventas por tiempo dentro del vigilante de precios**, 15 min antes del cierre.
  El cron de ventas no llegó a su ventana ni una vez en la semana del 28/09
  (llega 4-7 h tarde) y queda de **respaldo**, con el mismo identificador de
  orden: si el vigilante ya vendió, no repite. Si ninguno llega, plan B en la
  apertura siguiente (`tiempo_diferido`), como antes.
- **El vigilante arranca cada sesión con posiciones**, lo lanza el job de compras
  según lo que XTB tenga (`posiciones_xtb`), no solo cuando hubo compras. Antes
  vigilar el pre-mercado era posible; ahora espera a la apertura o se va en reposo.
- **Relee XTB cada 3 min y tras cada venta**: el latido ya no anuncia posiciones
  vendidas (el 02/10 siguió anunciando CTVA seis minutos después de venderla).
- **Precio real de cada orden.** Si XTB no lo devuelve: en compras, el de
  apertura de la posición nueva; en ventas, deducido del cambio de saldo. Hay dos
  modelos de saldo posibles y las lecturas del 02/10 no los distinguen (los dos
  dan 11,97 para CTVA), así que se calculan los dos y se elige el que cuadra con
  el bid previo; sin referencia, el precio se queda vacío. Columna nueva
  `precio_fuente` (`xtb`, `posicion`, `saldo-caja`, `saldo-pnl`, `manual`). La
  prueba real del lunes deja medido cuál usa XTB.
- **Una venta no se pierde al publicar** (`centinela/diario.py`). Todo lo que el
  broker hace se anota primero en un diario local y se publica APLICÁNDOLO sobre
  el origin más nuevo, en un árbol aparte y sin rebase: no puede chocar. Y la
  **reconciliación cuadra las acciones de XTB contra `bitacora_broker.csv`**: una
  venta en XTB que no esté registrada (un push perdido, un cierre a mano) es rojo.

### El semáforo

- **Un solo grupo de concurrencia** (`centinela-operativa`) para regenerar la
  página, en el workflow reutilizable `pagina_operativa.yml`, que además regenera
  sobre el origin nuevo si el push choca. La salud la registra cada workflow en
  un job propio y la publica por el diario. Fin de los «could not apply …
  operativa» (13 en la semana del 28/09).
- **Estado del broker siempre actual**: se lee tras cada compra, venta, entrada
  tardía y en cada latido; viaja en el latido y la página lo prefiere si es más
  nuevo. `estado_broker.volcar()` ya no se traga los errores.
- **«ok» con significado** en ventas: `ok: N vendidas`, `ok: nada que vender`,
  `fuera-de-ventana: nada que vender` y, si quedaban ventas por hacer,
  `fallo:fuera-de-ventana` (rojo). El respaldo tardío no pisa el resultado del
  vigilante.
- **Latido con historial** (~240): se ve si la sesión estuvo cubierta entera; un
  corte de más de 10 min pinta ámbar.
- Compras: agotar la espera a la apertura ya no termina en verde sin comprar
  (`EJECUTOR_COMPRAS_ESPERA_MAX_MIN` = 250, por encima de las 4 h que puede
  adelantarse el pre-apertura).

### Aviso externo

`centinela/aviso.py`: ping a **healthchecks.io** cada minuto mientras haya
posiciones con el mercado abierto; si se cortan más de 5 min, email. Al terminar
la sesión el check se **pausa** (vuelve solo con el primer ping del día
siguiente). Sin configurar, la página lo dice en ámbar. Procedimiento manual de
emergencia en el README.

### Limpieza

- CTVA en `bitacora_broker.csv` y en el diario de fiabilidad: la venta (orden
  917022383) **ejecutada** a 11,97; la entrada tardía a **12,71** (slippage
  +2,62 % sobre los 12,385 del simulador). `precio_fuente = manual`.
- `commit_y_push.sh` sin ningún `|| true`: cada fallo del bucle se dice y decide
  el bucle. El test de shells ya no tiene excepciones.
- **El número de cuenta fuera del código**: `config.CUENTA_DEMO_HUELLA` es un
  scrypt con sal (n=2^17) y el número real llega por el secret `XTB_CUENTA`. Los
  logs y la página ya no lo imprimen. **Sigue en el historial de git** (4
  commits, el primero `b1d0862`); no se ha reescrito.

### Fallos encontrados por el camino

- **El relevo del vigilante no podía funcionar**: sucesor y saliente estaban en el
  mismo grupo de concurrencia y se esperaban el uno al otro. El relevo va ahora
  en su propio grupo.
- `niveles.sigue_abierta` miraba solo la primera entrada del símbolo; con dos
  compras del mismo ticker decía «ya no está».
- Cualquier `workflow_dispatch` forzaba un vigilante nuevo: el 02/10 hubo once en
  cola, cancelados uno tras otro. Ahora solo fuerza la casilla `forzar` o un
  relevo.
- El respaldo de ventas vendía las acciones de la cuenta simulada, no las de XTB.

Simulación de una sesión entera con un XTB de mentira
(`tests/test_sesion_simulada.py`): compra tras la apertura, venta por objetivo
con un **choque de push real** contra un remoto temporal, vigilante caído 15 min y
relanzado (el historial lo registra como corte), venta por stop, venta por
tiempo 15 min antes del cierre, pausa del aviso y reconciliación final.

---

## 2026-10-02 (4) — Lo que decidió el dato roto deja de contar

La entrada anterior arregló el futuro: el screener ya no mira una serie con una
acción corporativa sin ajustar. Esto es lo otro, lo que **ya se decidió** con
esas series. CTVA seguía abierta en la demo y ocho operaciones de MRNA estaban
dentro de las cifras publicadas.

### CTVA: cerrada, no dejada correr

134 acciones a mercado, en XTB y en el simulador, con motivo propio
—`dato_erroneo`, tipo de orden `venta_dato_erroneo`—. Dejarla correr era dejar
que un artefacto de datos decidiera la salida: su objetivo estaba en +103 % y su
stop salía de un ATR que valía el 51 % del precio.

El cierre lo hace `scripts/cerrar_por_dato_erroneo.py`, manual, y **solo cierra
lo declarado** en `estado/datos_erroneos.json`. Las dos patas van juntas o no va
ninguna: cerrar solo en XTB dejaría al simulador reconciliando contra una
posición fantasma cada mañana, y cerrar solo en el simulador dejaría acciones
vivas en el broker sin nadie que las vigile.

La entrada **no se borra**: se abre en la bitácora como la habría abierto el
post-cierre —misma función, mismo precio de apertura, mismo objetivo— y se cierra
acto seguido. El dinero de la demo se movió de verdad; una bitácora sin esa
operación sería más limpia y más falsa.

**Lo que pasó al cerrarla (14:58 ET, orden 917022383).** XTB vendió las 134 a
**11,97**, pero el script la anotó como *rechazada* y se paró antes de la pata
del simulador. La causa no es de este script: `ambiguas.confirmar()` solo sabe
confirmar **compras** —da "ejecutada" si la posición *crece*— y una venta
correcta hace justo lo contrario. Afecta igual a las ventas del vigilante de
precios y del ejecutor de ventas; hasta hoy no había saltado porque la demo no
había vendido nunca. Queda pendiente de decisión.

Comprobado contra la cuenta (auditoría, run 37051118528): 0 posiciones, 0
órdenes. El precio sale del saldo, no de una suposición: 28.287,90 →
29.891,88 = 1.603,98 = 134 × 11,97, igual que el bid visto antes de mandar.
El simulador se cerró a mano con las mismas funciones del script (A id=160 y
B id=161, 12,385 → 11,97, −3,35 %). La fila de `bitacora_broker.csv` sigue
diciendo *rechazada*: corregirla a mano es un cambio en el registro del broker
que no se ha hecho sin aprobación.

### Las ventanas se declaran a mano, con evidencia

`centinela/datos_erroneos.py` + `estado/datos_erroneos.json`, mismo patrón que
`incidentes.py`: ticker, desde, causa, evidencia y fecha de detección. Sin
evidencia no se guarda.

| | |
|---|---|
| CTVA | desde 01/10 — escisión: 77,65 → 12,57 (×0,16) con ×22 de volumen |
| MRNA | desde 19/08 — contrasplit: 62,96 → 174,38 (×2,77) |

**Por qué declaradas y no detectadas al vuelo.** Recorrer la bitácora llamando a
`continuidad.esta_rota` haría que el win rate del panel cambiara solo el día que
yfinance reajuste una serie o el salto salga de la ventana de 260 sesiones:
cifras de hace dos meses moviéndose sin que nadie decida nada. `continuidad.py`
las **encuentra**; este registro las **declara**.

**El cruce es por solapamiento**, no por fecha de entrada: las dos operaciones
de MRNA anteriores al salto (22/07 y 07/08) viven enteras en la escala antigua y
no están afectadas; una que cruzara el salto sería el caso más sucio de todos.

### Qué cambia en las cifras

Ocho operaciones de MRNA fuera (6 cerradas + 2 abiertas) y las de CTVA, que
nacen ya excluidas. Las excluidas eran **ganadoras en conjunto** (+26,4 pp en A,
+32,3 pp en B), así que las cifras **bajan**: el dato roto llevaba dos meses
adornando el resultado.

| | A antes | A después | B antes | B después |
|---|---|---|---|---|
| Cerradas | 76 | **73** | 61 | **58** |
| Win rate | 63,16 % | **63,01 %** | 67,21 % | **65,52 %** |
| Expectancy | +3,95 pp | **+3,76 pp** | +4,21 pp | **+3,87 pp** |
| Profit factor | 2,02 | **1,97** | 2,68 | **2,47** |
| Suma de retornos | +300,5 pp | **+274,1 pp** | +256,8 pp | **+224,5 pp** |
| Capital | 34.254 $ | **33.845 $** | 33.968 $ | **33.457 $** |
| Rentabilidad | +14,18 % | **+12,82 %** | +13,23 % | **+11,52 %** |
| CAGR | 90,2 % | **79,4 %** | 82,6 % | **69,7 %** |
| Sharpe | 2,09 | **1,92** | 1,92 | **1,71** |
| Drawdown máx. | −10,44 % | −10,44 % | −12,16 % | −12,16 % |
| Slots usados | 5 | **4** | 4 | **3** |

El drawdown no se mueve: los tramos de caída de la curva no los puso MRNA.

### Dónde se aplica la exclusión, y dónde NO

Un solo sitio decide quién cuenta (`cuenta.marcar_excluidas` / `vista_limpia`,
al lado del criterio de las duplicadas) y de ahí lo leen el panel, la página de
operativa, el auditor y el dimensionado de órdenes. Dos copias del criterio es
como el panel y la auditoría acaban contando universos distintos sin que nadie se
entere; ya pasó con `marcar_duplicadas`.

| Se excluye de | No se excluye de |
|---|---|
| win rate, expectancy, profit factor, suma de retornos | la bitácora: las filas siguen, con su P&L |
| cuenta simulada, curva de equity, riesgo por operación | la tabla de operaciones: se pintan **marcadas** |
| «XTB vs. simulador» (slippage de la demo) | **órdenes activas**: una excluida puede seguir viva, con su stop y su límite puestos, y la página no puede callar una orden real |
| el equity que dimensiona las órdenes nuevas | el diario de fiabilidad del broker: mide si XTB ejecuta lo que se le manda, y eso lo hizo bien — la culpa era del dato, no del broker |

La página lo dice en voz alta: una nota bajo la tabla con la causa y la
evidencia de cada serie, el contador avisando de cuántas filas no cuentan, y las
ejecuciones reales de la demo que se caen de «XTB vs. simulador».

`reportes/auditoria_fiabilidad.md` sigue siendo el del universo anterior; la
próxima ejecución del auditor ya aplicará la exclusión.

513 tests (uno nuevo: un registro de datos erróneos ilegible para en rojo; antes se trataba como vacío y devolvía las operaciones rotas a las cifras en silencio).

---

## 2026-10-02 (3) — Una escisión sin ajustar hizo comprar lo que no debía

CTVA entró con un objetivo de **+103 %** cuando la estrategia busca rebotes de
~+5 % en 10 días. Investigando de dónde salía ese número apareció algo peor que
un objetivo raro.

### La fórmula no tiene ningún bug

```
objetivo = max(+5%, min(entrada + 2×ATR, entrada + 6×ATR))
         = 12,39 + 2 × 6,37 = 25,13
```

Funciona exactamente como está diseñada. Lo que está mal es el **ATR**: 6,37, el
**51 % del precio**. Y está mal porque la serie tiene un agujero:

```
2026-09-30   77,65     volumen   4.034.800
2026-10-01   12,57     volumen  88.127.300   ← ×22
```

CTVA no se desplomó un 84 %: **se escindió**, y yfinance no reajustó el
histórico. Con 88 millones de volumen, es una acción corporativa de manual.

### Lo grave: el dato provocó la entrada

```
CTVA | dd=86.2% | prob=1.000 | ENTRAR | obj_analista=92.6
```

El modelo dio **confianza máxima** a una caída del 86 % que no existe: el
drawdown real es ~0 %, la acción vale 12,9 porque repartió el resto en otra
compañía. Y el precio objetivo de analistas (92,6) es el de la empresa **antes**
de escindirse.

No es un caso aislado: barriendo los 28 tickers del historial apareció otro,
**MRNA**, con un salto de ×2,77 el 19/08 (probable contrasplit). Dos de 28.

### El arreglo: fuera del universo mientras la serie sea dudosa

`continuidad.py` detecta saltos de un día fuera de la horquilla ×0,60–×1,70 —un
desplome del 40 % o un +70 % pasan, porque ocurren— y el screener descarta ese
ticker **antes de mirar el drawdown**, que es justo lo que la serie rota
falsea. Cuando el salto sale de la ventana de 260 sesiones, el ticker vuelve
solo.

No se intenta corregir la serie. Reconstruir el factor de ajuste de una escisión
a partir del salto es adivinar, y un error ahí no se ve: produce una serie
plausible y mal.

### El objetivo se queda como está

Decisión explícita, no omisión. **109 de 146 operaciones (74,7 %)** tienen
objetivo > +25 %: no es una anomalía, es la regla. Pero los números no dicen que
perjudique:

| Objetivo | n | Sale por objetivo | P&L medio |
|---|---|---|---|
| +10 a +25 % | 32 | 16 % | +2,63 % |
| > +25 % | 105 | 11 % | **+4,51 %** |

El objetivo casi nunca se alcanza, da igual lo lejos que esté: en la práctica la
estrategia **sale por tiempo a los 10 días**. Queda documentado que el objetivo
es, en la práctica, inerte.

497 tests.

---

## 2026-10-02 (2) — Incidentes resueltos: un rojo arreglado deja de gritar

Los rechazos del 30/09 y del 02/10 tienen causa identificada y arreglo
publicado, pero seguirían pintando rojo y hundiendo la fiabilidad hasta la
semana que viene. Un rojo que se queda días en pantalla ya no informa: entrena
a mirar el panel sin leerlo.

`estado/incidentes.json` declara cada uno con **fecha, causa, arreglo, commit y
las órdenes afectadas**. Sin commit no se guarda — un incidente resuelto sin el
commit del arreglo no es un incidente resuelto, es uno que alguien prefiere no
mirar.

| | |
|---|---|
| **En el semáforo** | no aparece |
| **En la página** | sí, abajo y en gris, con su causa y enlace al commit |
| **En la fiabilidad** | sus órdenes no cuentan, y el porcentaje se muestra «desde el último arreglo» |
| **Un rechazo nuevo** | rojo inmediato, con cualquier causa no registrada |

Lo que NO hace es silenciar. Las órdenes van **listadas una a una**, así que si
la causa vuelve, vuelve el rojo: declarar un incidente no vuelve inmune a un
ticker ni a un componente. Y la excusa de un componente va de la fecha del
incidente a la de su arreglo, ambas incluidas y ni un día más — porque un fallo
así no se queda en un día (la reconciliación del 30/09 siguió dando diferencias
cada mañana hasta que se arregló) pero tampoco dura para siempre.

### Dos cosas más que aparecieron al medirlo

**El diario de fiabilidad contaba dos veces la misma orden.** La entrada tardía
quedaba anotada por `enviar_resolviendo` y otra vez por el script que la mandó.
Un envío deja una fila.

**La reconciliación daba un rojo falso cada día que se compra.** El simulador
mueve las entradas de `entradas_pendientes` a `posiciones` en el post-cierre,
así que entre la compra de la mañana y el cierre la posición existe en XTB y no
en el estado. Eso no es una discrepancia: es el mismo día funcionando como debe.
Lo que nunca se excusa es una posición en XTB que nadie compró.

Semáforo tras el cambio: **verde**, sin motivos. Fiabilidad: **1 de 1 ejecutada
(100 %) desde el último arreglo**.

488 tests.

---

## 2026-10-02 — No era el horario: eran los niveles. Y XTB cuenta doble

El arreglo de ayer funcionó en las dos mitades que se podían probar:

```
08:47 ET  faltan 42 min para la apertura; las compras se mandan DESPUÉS de abrir
09:30:03  0 min tras la apertura          ← durmió 43 minutos y despertó al abrir
09:30:16  enviando compra CTVA x134...
09:30:22  rechazada — XTB no tiene ni posición ni orden    ← y murió en rojo
```

El job esperó a la apertura en vez de mandar antes, y la confirmación detectó el
rechazo en vez de anotar «en_cola» y callarse. Pero **la orden se rechazó
igual**, con el mercado abierto. La hipótesis del horario no bastaba.

### La causa de verdad: los niveles

Con el mercado abierto, el mismo instrumento, el mismo minuto:

| Orden | Objetivo y stop | Resultado |
|---|---|---|
| CTVA × 134 | 26,16 / 11,06 | **rechazada** |
| CTVA × 1 | — | ejecutada |
| CTVA × 50 | — | ejecutada |
| CTVA × 134 | — | **ejecutada** (orden 916937102) |

No es el volumen: son los niveles. Ya se sabía desde el 28/09 que XTB no acepta
stop ni take profit en acciones al contado —entonces los **ignoraba en
silencio**, y la posición volvía con los dos a `None`— y en algún momento pasó a
**rechazar la orden entera**.

Se siguen aceptando como argumento y registrando en la bitácora, porque son la
decisión del simulador y hay que saber cuáles eran. Lo que no se hace es
mandárselos a un broker que los rechaza.

### Y XTB devuelve la misma posición repetida

Una compra de 50 acciones aparecía como «2 entradas, 100 acciones»; una de 134,
como 268. No se ejecuta dos veces —el saldo bajó lo que cuestan 50, no 100— es
la lista que viene duplicada. Sumarlas hacía **vender el doble de lo que había**.
Se deduplica por número de orden.

### El primer día de una posición estaba sin vigilar

Con CTVA ya comprada, el vigilante de precios arrancó diciendo «no hay ninguna
posición con nivel que vigilar». El simulador mueve las entradas de
`entradas_pendientes` a `posiciones` en el **post-cierre**, así que entre la
compra de la mañana y el cierre no tienen niveles en el estado.

Un agujero de una sesión entera, y justo el día en que la posición está más
lejos de su precio de entrada. Los niveles existen desde que se generó la orden
y están en la bitácora: de ahí se leen mientras el estado no los tenga.

### La sesión de hoy, recuperada

CTVA se reenvió como **entrada tardía** a las 10:06 ET, dentro de la primera
hora, y se confirmó contra XTB: 134 acciones a **12,71**, orden 916943840,
«pasó de 0 a 134 acciones». El precio de apertura era 12,385, así que **llegar
36 minutos tarde costó un 2,62 %**. Está registrado con los dos precios, que
para eso tiene tipo propio.

Y un fallo del arreglo de ayer, corregido sobre la marcha: `confirmar()`
concluía «rechazada» en la **primera** comprobación, seis segundos después de
mandar. Una orden recién enviada tarda en aparecer como posición, así que
declararla muerta tan pronto es el mismo error que darla por ejecutada sin
mirar, solo que al revés. Ahora agota las cinco comprobaciones.

471 tests.

---

## 2026-10-01 — "Aceptada" no es "ejecutada": la sesión del 30/09 se perdió entera

El 30 de septiembre el sistema decidió dos entradas, MRNA y FICO, y las mandó a
XTB a las **08:47 ET: 43 minutos antes de la apertura**. XTB respondió «en cola»
para las dos. El sistema lo anotó así, no volvió a preguntar, y la página mostró
las dos órdenes `en_cola`, **«Fiabilidad de XTB 100 %»** y el semáforo en verde.

En xStation 5 las dos figuran como **RECHAZADO**. La cuenta no tiene ninguna
posición y el saldo sigue intacto.

### La causa, comprobada a propósito

El 01/10 a las 23:15 ET, con el mercado **cerrado**, se mandó una compra de 1
acción de F.US. XTB la aceptó y devolvió «en cola» sin ningún error (orden
916785162). Tres minutos después: ni posición, ni orden en cola, ni cambio en el
saldo.

**XTB acepta órdenes de mercado sobre acciones fuera de horario, las encola y
las descarta.** No rechaza al enviar, que es por lo que nadie se enteró.

### El arreglo

**Las compras se mandan DESPUÉS de abrir.** La ventana pasa de «entre 60 y 5
minutos antes» a «desde la apertura, hasta 60 minutos». Un disparo que aterriza
pronto ya no se va: **duerme** hasta que abra, porque la escalera de crons está
pensada precisamente para llegar pronto. Y si llega más de una hora tarde,
`fallo:ventana-perdida` **en rojo** — una compra fuera de esa ventana ya no es
una compra al open.

Se pierden los segundos que van de la apertura al envío. Es el precio, y se
mide: las entradas tardías tienen tipo propio y guardan el precio de apertura
junto al ejecutado.

**Tras cada orden se pregunta a XTB hasta tener un estado definitivo.** Cinco
comprobaciones: si aparece la posición, ejecutada; si la orden sigue en cola,
en cola; si no hay ni una cosa ni la otra, **rechazada**. Y no poder preguntar
—un corte de red— no convierte una orden en rechazada: eso llevaría a comprarla
dos veces.

**Un rechazo es ROJO**, no ámbar como antes, en el job, en el semáforo y en la
fila «Hoy», con el motivo que devuelve XTB. Una orden que termina el día en cola
también: XTB la aceptó y no la ejecutó.

**La fiabilidad se calcula sobre EJECUTADAS**, no sobre enviadas, y se desglosa
en enviadas / ejecutadas / rechazadas / sin desenlace / ambiguas. Con el cálculo
viejo, el 30/09 daba 100 %. Con el nuevo da **0 %**, que es la verdad.

Todo esto vale igual para las ventas por tiempo y para las del vigilante de
precios: la confirmación vive en el camino común por el que pasan las tres.

### La sesión perdida

No se repusieron. Para cuando se diagnosticó había pasado más de una hora desde
la apertura, y el simulador mediría una cosa y el broker otra. Queda documentado
en el log del día.

Y la reconciliación deja de denunciarlas: lee de `bitacora_broker.csv` que su
compra fue rechazada y nadie la repuso, lo explica en cada run y no las cuenta
como diferencia. **No es una lista escrita a mano** —sale de los datos, así que
se mantiene sola y no se puede olvidar de borrar una entrada cuando deje de
aplicar.

### Detalle menor

La fila «Reentrenamiento» decía «nunca ha corrido» y había corrido el 02/08, el
01/09 y el 01/10. El registro de salud solo guarda la última vez y es posterior
a esas ejecuciones; ahora, cuando no sabe nada, se lee el historial de git, que
sí las tiene.

466 tests.

---

## 2026-09-29 (4) — Dos falsos avisos menos, y la librería deja de ser prestada

### Un vigilante en reposo no es un vigilante caído

La página decía «Problema: el vigilante lleva 62 min sin latir» con la cuenta
**vacía** y nada que vigilar. El vigilante había terminado bien —no había
posiciones con nivel— pero se iba en silencio, y su último latido envejecía
hasta que el reloj lo daba por muerto.

Dos cambios, y el segundo importa más que el primero:

1. Un vigilante que termina por su cuenta publica ahora un estado **`en-reposo`
   con el motivo**, y un latido en reposo no se juzga por su antigüedad. El
   motivo es obligatorio: sin él se lee igual que uno caído, que es justo lo que
   se quería evitar.
2. **Rojo solo si hay algo que vigilar.** Un vigilante ausente con la cuenta
   vacía no deja nada al aire; uno ausente con tres posiciones y sus stops, sí.
   Antes solo se miraba el reloj. Ahora se mira también cuántas posiciones tiene
   XTB con objetivo o stop, y estar en reposo **con** posiciones sin vigilar
   sigue siendo rojo — porque entonces sí se fue dejando trabajo sin hacer.

Si luego entra una compra, el vigilante vuelve a arrancar: lo lanza el workflow
de compras, y el peldaño que decide si hace falta uno nuevo entiende el reposo
como «terminó, y si ahora hay trabajo hace falta otro».

### La sesión de XTB deja de avisar de nada

«La sesión caduca en 1.3 h. Renuévala…» era un aviso de cuando se creía que
renovarla exigía a una persona. El 2026-09-29 se midió que no: el TGT caduca de
madrugada **todas las noches** y el login en frío de la mañana entra solo con la
cookie de dispositivo de confianza.

Avisar de lo que se arregla solo es ruido, y el ruido se come la señal. Sale del
semáforo y se queda como una línea gris en Componentes: «Sesión de XTB: válida
1,3 h más». Lo único que sigue pintando rojo es lo único que de verdad exige a
una persona: que un componente muera con `XTB_REQUIERE_CODIGO`.

### La librería, copiada dentro

`github.com/liskeee/xtb-api-python` devuelve **404**. El paquete sigue publicado
y sin retirar, pero no hay dónde consultar incidencias, nada garantiza que siga
publicado mañana, y este sistema **manda órdenes a un broker** con él.

`vendor/xtb_api/` es ahora una copia de la 0.10.0 con su licencia MIT intacta y
los dos parches aplicados **dentro**, cada uno documentado en
[`vendor/xtb_api/CAMBIOS.md`](vendor/xtb_api/CAMBIOS.md) con qué hacía el
original, qué problema daba, qué costó y qué hace ahora:

| Parche | Qué corrige |
|---|---|
| **1** — `_resolve_instrument_id` | Elegir la **acción** y no su CFD cuando XTB ofrece las dos con el mismo símbolo. Era lo que perdió 7 de 8 compras. |
| **2** — `_grpc_call` | Leer `grpc-status`/`grpc-message` de las cabeceras en vez de tirarlos. Era lo que convertía cualquier error en «respuesta vacía». |

`centinela/__init__.py` mete `vendor/` al principio de `sys.path`, así que la
copia gana aunque alguien instale el paquete de PyPI. Los dos monkeypatch que
hacían esto en tiempo de ejecución desaparecen: la corrección vive donde estaba
el fallo. `requirements-broker.txt` deja de descargar el paquete y pasa a
instalar solo **sus** dependencias, que antes venían de arrastre.

Hay un test que arranca un intérprete limpio, sin el paquete instalado, e
importa el sistema entero: si la copia dejara de bastarse, se vería ahí.

449 tests.

---

## 2026-09-29 (3) — La causa de las órdenes "ambiguas", y una corrección

Ayer dejé escrito que «el endpoint de trading de XTB está fallando». Era una
descripción del síntoma, no la causa, y la causa resultó ser otra cosa.

### Lo que decían las cabeceras que nadie leía

El cliente hace `raise_for_status()` —o sea, el HTTP es 200— y si el cuerpo
viene vacío devuelve `b""`. Pero en gRPC-web **los errores llegan con HTTP 200**
y el motivo en las cabeceras. Al leerlas, el motivo apareció al primer intento:

```
HTTP         : 200 OK
grpc-status  : 3                                  (INVALID_ARGUMENT)
grpc-message : Could not find instrument for id: 335
```

335 es el identificador que el **propio buscador de XTB** devuelve para F.US. Se
repitió con INTC (`id: 277`), así que no es cosa de un ticker: el servicio de
trading de XTB no reconoce los identificadores que su servicio de búsqueda
acaba de dar. Mandamos exactamente lo que XTB dice y XTB lo rechaza.

Eso explica también la intermitencia —una de ocho entró— mejor que ninguna otra
hipótesis: una flota parcialmente actualizada, donde algunos backends todavía
conocen esos ids y la mayoría no. No hay nada que arreglar por nuestra parte, y
la librería está huérfana: su repositorio (`liskeee/xtb-api-python`) devuelve
404, aunque el paquete siga publicado y 0.10.0 siga siendo la última versión.

### Lo que sí se arregla: que el motivo se vea

Ocho intentos a ciegas frente a un mensaje claro es la diferencia entre
diagnosticar en un minuto y en una tarde. `parche_grpc.py` envuelve la llamada,
lee las cabeceras y pone el motivo real en el error de la ejecución, así que
sale en el log del ejecutor y en la página en vez de en un diagnóstico que hay
que acordarse de lanzar.

### La causa raíz, y es nuestra

Buscando el símbolo aparecieron **dos** instrumentos llamados `F.US`:

```
id=7813   clave=9_F.US_US_STC        Ford Motor Co
id=335    clave=4_F.US_US_STC CFD    CLOSE ONLY / Ford Motor Co CFD
```

Uno es la acción al contado y el otro su **CFD**, además en *close only*. El
cliente resuelve el símbolo devolviendo **el primero que coincida**, y el orden
de esa lista cambia entre sesiones. Cuando salía primero el CFD, se mandaba su
id al servicio de acciones al contado, que con toda la razón contesta que no lo
conoce.

Eso explica todo lo que no encajaba: la intermitencia de 1 de cada 8 (era una
moneda al aire), el mensaje raro, y que INTC fallara igual con su propio CFD
(id 277). No era XTB rompiéndose: era una ambigüedad que la librería resolvía a
suertes.

`parche_instrumento.py` la resuelve con criterio: entre las coincidencias
exactas se queda con la de contado y descarta los CFD; si no hay ninguna de
contado, falla con un mensaje claro en vez de operar el instrumento equivocado.
Se distingue por la clave (`9_` contado, `4_` CFD), que es estructura, y de
reserva por el nombre, que es texto para humanos.

**Verificado con el mercado abierto**: con el parche puesto, la compra entró **a
la primera** (`en_cola`, orden 916133272), después de ocho intentos fallidos sin
él.

### La prueba completa, con el mercado abierto

Con el instrumento bien resuelto, lo que ayer no se pudo terminar salió a la
primera. Las dos compras entraron **sin una sola ambigua**:

| | Nivel | Bid que disparó | Orden | Latencia |
|---|---|---|---|---|
| **Objetivo** | 12,27 | 12,29 | 916134784 | **1,88 s** |
| **Stop** | 12,32 | 12,30 | 916135107 | **1,87 s** |

Y queda registrado con sus tres precios, que es lo que permite medir el coste
real de vigilar en vivo:

```
2026-09-29|A|F|objetivo_intradia … objetivo_intradia,1,en_cola,,916134784,,,12.29,12.27,,
2026-09-29|A|F|stop_intradia     … stop_intradia,1,en_cola,,916135107,,,12.30,12.32,,
```

El precio de ejecución va vacío a propósito: la orden quedó `en_cola` y todavía
no tiene precio de relleno. Es justo el hueco que la verificación posterior a la
apertura rellena al día siguiente.

### Y un fallo propio que salió en la misma prueba

El bucle de limpieza vendía una vez por cada entrada que XTB devolvía. XTB
devolvió la misma posición partida en tres, así que hizo tres viajes de ida y
vuelta donde debía hacer uno. Se vio porque el saldo bajó seis céntimos en vez
de dos. Ahora se vende el TOTAL de una vez, agrupando por símbolo.

### La cuenta, limpia

Saldo 29.999,94, cero posiciones, cero órdenes en cola, y el saldo **cuadra al
céntimo** con la única operación registrada. Como cada ida y vuelta se come el
spread, un viaje sin registrar habría dejado un hueco: **ninguna de las siete
compras ambiguas llegó a ejecutarse**.

---

## 2026-09-29 (2) — Vigilante de precios: los niveles se ejecutan en vivo

XTB no acepta stop loss ni take profit en acciones al contado, así que los
niveles los ejecutaba el ejecutor **una vez al día**, a las 15:45 ET. Eso está
medido y es malo: la Cartera A pasaba de +12,07 % con drawdown −10,44 % a
**+9,32 % con −12,50 %**, peor en las dos dimensiones que no tener stop, porque
7 de los 21 stops se dispararon por un precio que el día cerró por encima.

Ahora hay un proceso mirando el **bid** tick a tick toda la sesión, por el
WebSocket de XTB, que dispara en el momento del cruce.

### Lo primero fue medir, no diseñar

**¿Admite XTB dos sesiones simultáneas con la misma cuenta?** De eso dependía
todo: si no las admite, el vigilante tendría que absorber también la
verificación de la apertura y las ventas por tiempo, y los demás workflows
callarse mientras esté vivo. No se puede contestar leyendo documentación —XTB
cerró su API oficial y lo que queda es ingeniería inversa— así que se midió.

**Respuesta: SÍ.** Dos conexiones conviven, las dos siguen vivas y las dos
reciben cotizaciones. El vigilante vive en su propio workflow y nadie tiene que
callarse.

La prueba comprueba las dos cosas que pueden fallar, no solo una: que la segunda
sesión conecte, y que la **primera siga viva después**. El modo de fallo más
probable no era que XTB rechazara la segunda, sino que la segunda echara a la
primera sin avisar.

### El candado no es un fichero

Al final de la sesión hay dos procesos que pueden querer cerrar la misma
posición, y corren en máquinas que no se ven. Un fichero de bloqueo sería una
promesa: entre que uno lo escribe y el otro lo lee caben segundos y un `push`.
Preguntarle a XTB es un hecho, y es la misma fuente para los dos. Antes de
vender, los dos releen la posición; el que llega segundo se la encuentra cerrada
y se calla. Al ponerlo apareció que **al ejecutor de ventas por tiempo le
faltaba**: vendía sin comprobar.

### El latido, y por qué no va en el historial

Un latido cada dos minutos son ~195 al día. Un commit por latido multiplicaría
por veinticinco los commits del repositorio y enterraría la bitácora; latir cada
15 minutos para que fueran pocos haría imposible la regla de «más de 10 minutos
= rojo». Va en una rama aparte con **un solo commit** reescrito por force-push:
`main` no se entera y la página lo lee en vivo.

**Lo medido, que cambia una garantía:** `raw.githubusercontent` sirve con
`cache-control: max-age=300` y el parámetro anticaché **no la esquiva** —se
comprobó: con `?t=` distinto seguía devolviendo una copia de 159 s—. Lo peor que
ve la página son 7 minutos (2 de origen + 5 de CDN), así que **no hay falsos
rojos**, pero un vigilante muerto tarda 10-15 minutos en verse ahí. Quien lo ve
exacto es el Vigilante general, que lee la rama por `git fetch` y es el que
relanza: la página es la vista humana, la comprobación que actúa no tiene margen.

### La prueba real quedó bloqueada por XTB, no por el código

El encargo pedía probarlo con dinero de mentira y mercado abierto: comprar una
acción barata, ponerle un nivel pegado al precio y ver al vigilante venderla.
**No se pudo terminar**, y el motivo no está en este repositorio.

Ocho intentos de compra en la demo, con el mercado abierto: **uno entró**. Los
otros siete volvieron `ambigua` con el mismo error —`gRPC trade endpoint
returned an empty response`—, que significa que el POST llegó y el cuerpo vino
vacío. Se descartó lo propio antes de culpar a XTB:

| Hipótesis | Cómo se descartó |
|---|---|
| Sesión cacheada con JWT caducado | Un login en frío falla igual |
| Pedir cotización antes de comprar rompe algo | La compra que sí entró el 28/09 también la pedía |
| El WebSocket está mal | Las cotizaciones llegan y se mueven todo el rato |
| Problema del reintento | Los tres intentos seguidos fallan idénticos |

Es el riesgo que `requirements-broker.txt` documenta desde el primer día: esto
es ingeniería inversa de xStation5 y puede romperse cualquier día sin aviso.

**Lo que sí quedó probado en vivo**, en el intento que entró (run 36599379502):
el vigilante conectó, superó el candado demo, cargó la posición con su nivel y
**se suscribió a los ticks de F.US**. Murió al publicar el latido, por un fallo
propio que se arregló (el repositorio del latido no heredaba las credenciales de
`actions/checkout`). El eslabón que queda sin probar contra XTB es el último
—`vender` tras el cruce—, y es el mismo `broker.vender()` que el ejecutor de
ventas por tiempo usa en producción desde hace días.

**Y un hallazgo que vale más que la prueba:** si el endpoint sigue así, el
sistema no puede operar. Lo bueno es que no lo esconde: `ambigua` cuenta como
no-ok, así que `enviar()` rompe en rojo, y no se traduce como «rechazada» a
propósito para no mandar la orden dos veces. La limpieza de la prueba aprendió
de paso a cancelar órdenes en cola, porque mirar solo posiciones y concluir que
la cuenta está limpia era saltarse justo el caso que la palabra «ambigua»
describe.

### El techo de 50 KB era un mal número

Medía el fichero en crudo, y el 20 % de estas páginas son comentarios: 10,4 KB
de explicación solo en el dashboard. Al añadir el coste de los disparos, el
fichero pasó de 50 KB y la única forma de volver a entrar era borrar
explicaciones. Lo que un teléfono siente es lo que **viaja**: 16,5 KB
comprimidos, no 50,6. El techo pasa a medir eso, y se añade un segundo techo en
crudo mucho más holgado para lo que el primero no ve —un volcado de datos
incrustado en el HTML comprime muy bien y no movería la aguja—.

---

## 2026-09-29 — Un "ok" que no dice cuántas órdenes no es un "ok"

La página dijo «Compras en XTB: ok» a las 07:47 con cero órdenes enviadas y XTB
vacío tras la apertura. El «ok» era literalmente cierto y aun así no informaba
de nada.

### Qué pasó de verdad

El escaneo recorrió 503 tickers, 182 pasaron el filtro de drawdown y **uno** dio
señal: COHR con prob 0.808 sobre el umbral 0.79. Se descartó porque ya había
posición abierta de ese ticker, así que se generaron 0 órdenes y no había nada
que enviar. Para saber eso había que abrir Actions y leer 187 líneas de log.

**Señales 1 → decididas 0 → enviadas 0 → ejecutadas 0.**

La posición de COHR que bloqueó la entrada existe **solo en el simulador**: es
una de las cinco heredadas del paper trading, y XTB tiene cero posiciones. Se
decide dejarlo así —las cinco vencen entre hoy y el 7 de octubre y a partir de
ahí simulador y broker vuelven a coincidir solos—, pero ya no en silencio.

### Los dos fallos que había al lado y que hoy no se notaron

**Una carrera.** Los jobs `ordenes` y `ejecutor` arrancaron EL MISMO SEGUNDO
(12:45:53), porque los dos colgaban solo del escaneo. El ejecutor hizo checkout
a las 12:45:55 y las órdenes se empujaron a las 12:46:18: leyó una copia rancia.
Hoy daba igual porque había 0 órdenes de todas formas; con decisiones habría
enviado nada y terminado en verde. Ahora `needs: [escaneo, ordenes]`, y además
el ejecutor relee hasta encontrar la decisión del día y muere con
`fallo:sin-decision` si la ventana se agota.

**Un fichero con dos escritores.** `guardar_pendientes()` reescribía
`ordenes/pendientes.json` entero. El post-cierre de ayer había dejado ahí dos
ventas por tiempo para hoy (VRT y GLW, las dos con `dia_limite` de hoy) y el job
de órdenes de esta mañana las borró al escribir `"ordenes": []`. Sesión perdida
del lado del broker, documentada en el log del día y sin recuperación: no se
envían ventas tardías. No costó dinero porque las dos son heredadas y XTB no las
tenía. Ahora cada momento reemplaza solo los tipos que él genera.

### Nunca más un "ok" mudo

El ejecutor termina siempre con un veredicto contable: `ok: 3 de 3 órdenes`, o
`ok: 0 órdenes — COHR: ya hay posición abierta en Cartera A y Cartera B`, con el
motivo sacado del propio log de decisiones. Si se decidieron tres y salieron
dos, es `fallo:envio-incompleto` y rojo, aunque las dos que salieron fueran
perfectas.

La página abre con la fila **Hoy**: señales → decididas → enviadas → ejecutadas,
y debajo quién se quedó en cada escalón y por qué. Un cero con motivo es
información; un cero sin motivo es una pregunta.

### Y la pregunta que quedó abierta ayer, contestada

Ayer se añadió un disparo del Vigilante a las 07:07 UTC para avisar de la
caducidad **antes** de la ventana de compras, sabiendo que podía dar rojo todas
las mañanas. Hoy hay dato: el TGT caducó a las 05:11 UTC y el run de compras de
las 12:46 hizo **login en frío y entró sin pedir código** — la cookie de
dispositivo de confianza sigue valiendo, y la cuenta se operó con normalidad.

O sea: la sesión caduca **todas las noches** y no pasa nada. Denunciarlo habría
sido un rojo cada mañana laborable por algo que se arregla solo. La regla pasa a
ser la correcta: una sesión caducada se informa en el log, y lo que se denuncia
es el único síntoma que prueba que la cookie dejó de valer — que algún
componente muriera con `XTB_REQUIERE_CODIGO`.

### Verificación de la apertura (nuevo, cuarto momento del día)

Corre 30-90 minutos después de abrir y no decide nada: compara lo decidido, lo
enviado y lo que XTB tiene de verdad. Corrige el precio de la bitácora —una
compra encolada antes de abrir no tiene precio real hasta que abre— y calcula el
slippage contra el open. Es rojo si una orden enviada no se ejecutó, si alguna
fue rechazada, o si una decisión no llegó a orden. El Vigilante denuncia si este
paso deja de correr.

Sobre el stop y el objetivo: se comprueban y **se intenta** ponerlos si faltan,
pero lo medido es que XTB los ignora en acciones al contado y que el cliente no
oficial no permite ponerlos después. Se registra lo que pase y no se pinta de
rojo: es un límite conocido del broker, y un rojo diario deja de significar nada.
Si algún día XTB empieza a aceptarlos, se verá el mismo día.

23 tests nuevos. 352 en total.

---

## 2026-09-28 (5) — La caché de precios dejaba sin sitio a la sesión de XTB

Verificando la página de Operativa apareció esto: el repositorio estaba al
**99 % de sus 10 GB de caché**, con 74 cachés de precios de 135 MB (9,75 GB).

La causa es la clave, `precios-${{ github.run_id }}`: al llevar el id del run
**nunca acierta** en la clave primaria, así que cada ejecución guarda una caché
nueva. La escalera de la pre-apertura sola dejaba ~17 al día, y el post-cierre
guardaba la misma caché tres veces, una por cada job que la lee.

No es solo desperdicio. Al pasar de 10 GB, GitHub desaloja por orden de último
uso, y lo que desaloja es la sesión de XTB, que pesa **359 bytes**. El Vigilante
la toca a diario contra la regla de los 7 días sin uso, pero la restricción que
mandaba no era esa: era la cuota, y esa desaloja en horas.

| | antes | ahora |
|---|---|---|
| Cachés nuevas al día | ~17 | 2 |
| Clave | id del run | fecha |
| Jobs que guardan | 6 | 3 (los que amplían la caché) |
| Uso del repositorio | 9,89 GB | 274 MB |

### Y un fallo propio, del mismo tipo

GitHub calcula la **versión** de una caché a partir de su lista de rutas, y una
clave solo casa dentro de su versión. El paso que se añadió para leer la
caducidad de la sesión pedía **una** ruta mientras los demás piden **tres**:
acertó con las cachés antiguas por casualidad y no habría acertado nunca más. El
aviso de «la sesión caduca en X h» se habría quedado mudo para siempre, sin
ningún error que lo delatara — el paso sale en verde diciendo «Cache not found».

Eso explica además el único run raro de la tarde: el Vigilante de las 21:01 no
encontró la caché de las 16:38 porque fue el primero en correr con la lista de
tres rutas. No era un fallo, era la transición.

Cuatro tests fijan las dos invariantes: todos los pasos de la sesión piden la
misma lista de rutas, y solo guardan caché los jobs que la amplían.

---

## 2026-09-28 (4) — Página de Operativa: la salud del sistema en una pantalla

El dashboard contesta "¿cuánto gana esto?". Faltaba quien contestara la otra
pregunta, la que se hace cuando algo huele mal: **"¿está funcionando ahora
mismo?"**. Hasta hoy eso obligaba a rebuscar en la pestaña Actions run por run.

[`docs/operativa.html`](docs/operativa.html) (31 KB, sin frameworks ni CDNs,
móvil primero) lo resume en un semáforo con los motivos escritos, más siete
secciones: componentes, cuenta en XTB, posiciones abiertas cruzando broker y
simulador, historial de órdenes con buscador y slippage, historial de objetivos
y stops, reconciliación y alertas del Vigilante.

### Por qué un fichero y no la API de GitHub

La página podría preguntarle a Actions qué runs hubo, pero eso exigiría un token
en una página pública. En vez de eso cada workflow escribe cómo le fue en
`estado/salud.json` y la página lee un JSON estático.

Quien registra es **un job posterior con `needs` e `if: always()`**, no el propio
script: un `salud.registrar()` al final del escaneo solo se ejecuta si el escaneo
llega al final, y los fallos que más importan son justo los otros —el runner sin
tiempo, el job cancelado, el `pip install` que revienta antes de empezar—. El job
ve el `result` aunque el componente muriera sin decir nada. Cuando el componente
**sí** habló (el ejecutor distingue candado, sesión caducada y diferencias), su
versión manda: `--solo-si-falta`.

### Las tres reglas que no podían salir mal

**No se filtra nada.** La página es pública: el número de cuenta sale enmascarado
(`•••385`) y de la sesión no se escribe nada, ni siquiera en un cálculo
intermedio (el aviso de caducidad lee la fecha, no el TGT). El test busca cada
secreto conocido en el texto entero del JSON, no campo a campo, para que un campo
nuevo no se cuele.

**Un verde viejo es peor que no tener página**, porque da tranquilidad sin
haberla comprobado. El navegador compara la hora de generación con la actual:
a las 30 h avisa, a las 72 lo pinta en rojo. Esa comprobación **solo puede
empeorar** el color, nunca mejorarlo. Verificado en un Chromium de verdad
adelantando el reloj: +20 h verde, +31 h ámbar, +73 h rojo.

**Publicar no puede romper el trading.** La regeneración va siempre en un job
aparte con `needs`, el último: cuando corre, la bitácora, el estado y la bitácora
del broker ya están persistidos y verificados contra el remoto. Y nadie depende
de ese job, así que si la página revienta, revienta sola. En el Vigilante eso
obligó además a separar permisos: `vigilar` sigue siendo de solo lectura y el
`contents: write` vive únicamente en el job de la página, que entra a mano en el
grupo de concurrencia (el workflow entero no puede, porque la espera en cola
cuenta contra su timeout de 5 minutos — lección del run 31119690487).

El Vigilante la republica **todos los días**, fines de semana incluidos: es el
único que corre siempre, y sin él un puente largo dispararía el aviso de datos
viejos sin que pasara nada malo.

### «No corrió» no es «salió mal»

Primer rojo en producción, y era falso. El segundo post-cierre del día fue
idempotente (`omitido:ya-procesado`), así que su ejecutor no llegó a correr y el
job quedó saltado. Dos errores encadenados:

1. El registro del job saltado **pisó** el `ok` real de la reconciliación de
   tres horas antes. Ahora un job saltado no registra nada: si un componente
   deja de correr de verdad, quien lo denuncia es la regla de «lleva X h sin
   aparecer», que para eso está.
2. El semáforo leía «cualquier cosa distinta de ok» como discrepancia, y pintó
   «la reconciliación encontró diferencias: ver el run» cuando no había
   encontrado nada porque no había mirado. Ahora solo es rojo si la
   reconciliación **corrió** y no cuadró.

### El turno de escritura no era para esto

El job de la página del Vigilante entró primero en `centinela-escritura`, el
grupo de concurrencia de los escaneos. Duró una tarde: el post-cierre de las
21:11 tomó el turno, el job se puso en cola a las 21:22, a las 21:53 llegó un
segundo post-cierre y GitHub —que solo guarda **un** run en cola por grupo—
canceló el más viejo. La página se habría quedado vieja justo el día que hay que
mirarla.

Publicar una página no puede depender de un turno que un escaneo retiene hasta
160 minutos. Ahora tiene grupo propio. Los ficheros que toca no los toca ningún
escaneo, así que el `fetch`+`rebase` de `commit_y_push.sh` basta para el caso
raro de dos publicaciones a la vez.

### Dos ámbares que ya existían y no se veían

Al pintarlo todo junto apareció que un rojo tapaba los ámbares. Ahora se recogen
**todos** los motivos y el color es el del peor: quien entra a arreglar algo
quiere ver todo lo que hay, no solo lo más grave.

Y al mirar la página ya publicada, con datos reales, apareció el otro: decía
«todo en orden» con seis de los siete componentes **sin haber reportado nunca**,
porque las reglas de retraso solo miran a los que tienen fecha. Un workflow mal
cableado habría dejado la página en verde para siempre. Ahora un componente
crítico que nunca ha reportado pinta **ámbar** —no rojo, porque recién
desplegado es indistinguible de «todavía no le ha tocado»— y se apaga solo en
cuanto cada uno corre una vez.

Tests: 57 nuevos (schema, cada regla del semáforo, enmascarado, fuga de
secretos, traducción del `result` de un job, cableado de los cinco workflows).
314 en total, todos en verde.

---

## 2026-09-28 (3) — Sesión de XTB renovable desde el móvil y plan B para la salida por tiempo

Dos agujeros del ejecutor, los dos del mismo tipo: el sistema sabía que algo iba
mal y no tenía forma de arreglarlo sin un humano delante de un terminal.

**La sesión caduca cada 8 h** y XTB no ofrece códigos de app: SMS, push o correo,
los tres con una persona leyendo. Eso no se puede automatizar, pero sí se puede
quitar el terminal de en medio. Ahora el run muere con `XTB_REQUIERE_CODIGO` y un
mensaje que dice qué hacer, en vez del `CASError` ilegible de antes; el workflow
«Renovar sesión XTB» se lanza desde la web del móvil con el código pegado en un
campo. Va en dos fases porque no se puede pegar un código antes de tenerlo.

El Vigilante avisa **con 3 h de margen** —avisar cuando ya caducó llega tarde,
porque para entonces un escaneo ya murió— y toca la caché a diario para que
GitHub no la borre por inactividad.

**La ventana de ventas dura 25 minutos** y el cron de Actions se ha retrasado
horas en este repositorio más de una vez. Si se pierde, la posición ya no se
puede cerrar ese día. El plan B la cierra en la apertura siguiente con motivo
`tiempo-diferido`, registra la diferencia de precio contra el cierre que se
pretendía, y el Vigilante denuncia cada salida diferida. Lo que **no** puede
pasar es que una posición pasada de fecha siga abierta un día más sin que nadie
lo diga: eso ahora pinta el semáforo de rojo.

---

## 2026-09-28 (2) — El ejecutor sale del Mac: 100 % en GitHub Actions

El ejecutor vivía en un Mac porque las credenciales de XTB abren también las
cuentas reales del titular. Confirmado que esas cuentas están vacías, ese
motivo desaparece y las credenciales pasan a ser secrets del repositorio.

### Lo que se comprobó antes de mover nada

Quedaba la otra duda: si el login de xStation5 funciona desde una IP de
datacenter, que es exactamente lo que su WAF existe para frenar.
`probar_broker.yml` (manual, y con la operación detrás de una casilla) lo
respondió:

| Prueba | Resultado |
|---|---|
| Solo lectura | login en 11,5 s, candado superado, saldo leído |
| Compra y cierre reales | **2,1 s**, orden 915795796, cuenta sin residuos |

Los 2,1 segundos son lo importante: la sesión guardada en `actions/cache` evita
que XTB pida el código por correo en cada ejecución, que era lo único que podía
hacer inviable la automatización. El TGT dura 8 h y cada runner es una máquina
nueva; sin esa caché, cada run sería un dispositivo nuevo para XTB.

### El candado cambia de ancla

Lo más importante de este cambio no es dónde corre el ejecutor, sino contra qué
compara el candado. Antes miraba las credenciales; ahora `config.CUENTA_DEMO`,
que está **versionada**. Un secret se cambia desde una página web, sin diff, sin
revisión y sin dejar rastro; la configuración exige un commit.

Y pasar a real exige cambiar DOS constantes —`TIPO_CUENTA_BROKER` y
`CUENTA_DEMO`— en el mismo commit, para que un despiste con una sola variable
no pueda sacar órdenes a una cuenta con dinero. El README estrena un apartado
**"Antes de pasar a dinero real"** con los pasos obligatorios, empezando por
reactivar el segundo factor.

### Qué cambia

- `ejecutor` como JOB APARTE con `needs` en preapertura (compras) y postcierre
  (reconciliación): cuando corre, la bitácora ya está persistida y verificada.
  Un XTB caído se ve en rojo y no revierte nada.
- `ventas.yml`, workflow propio para el tercer momento —cerrar por tiempo y
  vigilar stops— porque tiene que caer con el mercado abierto y a punto de
  cerrar, y eso no cuelga de ningún escaneo. **Su ventana son 25 minutos y eso
  es un riesgo conocido**: el cron de Actions se ha retrasado horas en este
  repositorio, y contra eso una ventana estrecha no tiene defensa. Se
  densifican los disparos y el Vigilante denuncia lo que no se reportó.
- 19 tests nuevos (`test_ejecutor_en_actions.py`) que atan lo que no puede
  romperse: el aislamiento del broker, los secretos, las dos franjas horarias y
  que lo ya ejecutado se commitea aunque el job muera a medias.

### Dos fallos que solo aparecieron ejecutando de verdad

**La reconciliación denunció cinco posiciones el primer día, y tenía razón.**
"APH, CIEN, COHR, GLW, VRT: abiertas en el simulador y NO en XTB" — cierto: el
simulador llevaba meses operando en papel y el ejecutor acababa de nacer, así
que esas posiciones nunca se compraron en el broker. No es un fallo, es
herencia; pero denunciarla cada día sería un rojo diario por algo correcto, y
un rojo que sale siempre enseña a ignorar los rojos. `config.EJECUCION_DESDE`
marca la primera sesión en que el ejecutor pudo comprar; lo anterior se queda
solo en el simulador hasta que cierre, y se dice en voz alta en cada pasada.

**Y `git add` de rutas que no existían.** `bitacora_broker.csv` y
`ordenes/pendientes.json` se creaban al primer uso, pero el paso de commit los
nombra siempre y `commit_y_push.sh` no silencia ese fallo a propósito. Ahora
existen en el repositorio desde el principio, vacíos.

### El Mac queda limpio

Agentes de `launchd` descargados y borrados, credenciales fuera del Llavero,
sesión de XTB borrada. El despertar programado necesita contraseña de
administrador, así que va en `mac/Desinstalar-Centinela.command`: doble clic,
sin terminal.

## 2026-09-28 — XTB ignora el stop: lo vigila el ejecutor

### Lo que se comprobó operando de verdad

Orden real en la demo, con el mercado abierto:

    COMPRANDO 1 x F.US | objetivo 13.70 | stop 11.21
    RESULTADO: estado=en_cola orden=915782911 error=None
    posiciones tras la compra: 1
       F.US x1.0 @ 12.45 | STOP=None | OBJETIVO=None

**XTB aceptó la orden, la ejecutó y descartó los niveles sin dar ningún
error.** Coincide con lo que documenta para acciones al contado —los niveles
van como órdenes pendientes independientes (sell stop / sell limit), que el
cliente no sabe crear— pero había que comprobarlo contra la API en vez de
fiarse de una página que describe la interfaz web.

La posición se cerró vendiendo el mismo volumen, lo que confirma que en
acciones al contado **vender netea** y no abre una posición corta. Cuenta
verificada después: 0 posiciones, 0 órdenes, saldo 29.999,98 (los dos centavos
son el spread de la ida y vuelta).

### Lo que cuesta vigilar el stop desde el ejecutor, medido

El ejecutor solo puede vender con el mercado abierto, así que tiene UNA ventana
al día (15:45 ET). Re-simulando la Cartera A con esa regla:

| | Rentabilidad | Drawdown máx. |
|---|---|---|
| A con stop en el mercado (lo publicado) | +12,07 % | −10,44 % |
| **A con stop vigilado 1 vez al día** | **+9,32 %** | **−12,50 %** |
| B (sin stop, por diseño) | +11,73 % | −12,16 % |

Pierde rentabilidad **y** gana riesgo. De las 21 operaciones que el simulador
cerró por stop, 7 no se habrían cerrado ese día porque el precio tocó el nivel
intradía pero cerró por encima; el peor caso individual empeoró 9,88 pp.

El dato incómodo: **el stop vigilado sale peor que no tener stop**, en las dos
dimensiones. Asume el riesgo de la Cartera B y además corta posiciones que
habrían recuperado.

Se implementa igualmente, por decisión expresa tomada a la vista de estos
números. La divergencia queda escrita en el panel, bajo la sección "XTB vs.
simulador", para que nadie compare las dos cifras sin saber que miden cosas
distintas.

### Qué cambia

- `ejecutor_xtb.vigilar_niveles`: en la ventana de las ventas, compara el
  precio de cada posición con su stop y su objetivo y cierra a mercado la que
  los cruce. Regla conservadora del simulador: si se cruzan los dos, gana el
  stop. Sin precio actual no se cierra nada — cerrar a ciegas sería peor que
  esperar a la siguiente pasada.
- `ordenes.py`: dos tipos nuevos, `venta_stop` y `venta_objetivo`. No se
  escriben en `pendientes.json` porque no se pueden decidir la víspera: nacen
  de comparar el precio con el nivel en el momento.
- 8 tests nuevos del vigilante de niveles.

## 2026-09-25 (2) — Fase beta (2/2): ejecutor para la demo de XTB

**Nada de esto toca la lógica de decisión.** Los escaneos deciden exactamente
igual y no saben que existe un broker.

### Arquitectura: decidir y ejecutar son dos cosas

Los escaneos escriben `ordenes/pendientes.json` con lo que hay que mandar
(`scripts/generar_ordenes.py`, en un job aparte de Actions que no tiene
credenciales y solo escribe un fichero). El ejecutor lo lee, opera en XTB y
anota en `bitacora_broker.csv` lo que pasó DE VERDAD: hora, precio, cantidad y
número de orden. Si el broker se cae, el sistema sigue decidiendo y simulando
igual; simplemente nadie ejecuta.

La diferencia entre las dos bitácoras es el slippage real, y es la única forma
honesta de saber cuánto vale la estrategia fuera del papel. El panel lo publica
en la sección **"XTB vs. simulador"**, que permanece oculta mientras no haya ni
una ejecución: una sección a cero se leería como "no hay diferencia" cuando la
verdad sería "todavía no se ha operado".

### El ejecutor corre en el Mac, no en GitHub Actions

Decisión tomada con el usuario, y la razón principal es de seguridad: **XTB no
da credenciales separadas para la demo**. El mismo email y la misma contraseña
abren también la cuenta real, así que ponerlas en los secrets de un repositorio
remoto era un riesgo que no compensaba por correr en la nube. Viven en el
**Llavero de macOS** y no salen del ordenador. Se suma que el login de
xStation5 pasa por un WAF y las IPs de datacenter de GitHub son justo lo que
ese WAF existe para frenar.

`mac/` trae los tres agentes de launchd, el script de `pmset` para que el Mac
se despierte solo y las instrucciones de energía. Cada agente dispara a DOS
horas porque launchd programa en hora local y no entiende de husos: cuando
Nueva York cambia de horario, la misma hora ET se mueve respecto de Guayaquil.
El disparo que cae fuera de ventana se calla en verde — la misma escalera de
crons que ya usan los escaneos.

### Tres momentos al día, y por qué el tercero

| Momento | Qué hace | Cuándo (ET) |
|---|---|---|
| `compras` | Manda las compras; XTB las deja EN COLA y las ejecuta al abrir | 60–5 min antes de la apertura |
| `ventas` | Cierra las posiciones que cumplen su décima sesión | 30–5 min antes del cierre |
| `reconcilia` | Compara XTB con el simulador | ≥30 min tras el cierre |

El de las ventas se añadió tras medir las 89 salidas por tiempo del histórico:
cerrar al cierre del día 10 se desvía **0,03 pp** del simulador, mientras que
hacerlo a la apertura del día 11 se desvía 0,40 pp **con 3,14 pp de
dispersión** (peor caso −7,74). Esa dispersión es gap overnight que la
estrategia no contempla, y no compensaba ahorrarse un despertar.

### Solo se opera la Cartera A

Tiene stop, así que su riesgo por operación está acotado por diseño; el de la B
no lo está por nada. La B sigue simulándose igual y la comparación entre las
dos sigue siendo el experimento.

### Candado demo

`broker_xtb.BrokerXTB` verifica en CADA conexión que el endpoint es el de demo
y que el número de cuenta es el esperado. Si algo no cuadra —o simplemente no
se puede leer— no se manda nada y se lanza `CuentaNoDemo`. Importa: la librería
por defecto se conecta a la cuenta REAL cuando `XTB_ACCOUNT_TYPE` no está
puesto, así que el tipo se pasa explícito y además se comprueba después.

### Idempotencia y reconciliación

Cada orden lleva un id determinista `fecha|cartera|ticker|tipo` registrado en
`estado/ordenes_enviadas.json`, y se marca DESPUÉS de que el broker responda:
marcar antes perdería la orden si el envío fallara. Tras cada ejecución se
comparan las posiciones de XTB con `estado.json` y cualquier diferencia hace
fallar en rojo con el detalle. El Vigilante, además, denuncia las órdenes que
el ejecutor no reportó — un Mac dormido no deja ningún run rojo que mirar, que
es el mismo agujero del 2026-07-27 al otro lado del cable.

### Dashboard

Nueva sección **"Riesgo y resultados por operación"**, en dólares y en
porcentaje: capital por posición, riesgo por operación (distancia al stop),
riesgo agregado si saltaran todos los stops a la vez, ganancia y pérdida medias
y extremas. La Cartera B no publica un riesgo por operación calculado, porque
sin stop no está acotado por nada; publica la peor pérdida observada y lo dice.
La tabla de operaciones estrena **Acciones, Inversión $ y P&L $**: un +19% no
dice si fueron ocho dólares o doscientos.

### Limitaciones del cliente, medidas

- **No se puede modificar el take profit** de una posición abierta. Con el TP
  fijo en el objetivo inicial: A +12,25% (vs +12,07%), B +11,15% (vs +11,73%).
- **No se puede cerrar por id**: se vende el mismo volumen y la reconciliación
  comprueba que la posición desapareció.
- **Sin acciones fraccionadas por la API**: el cliente redondea con
  `int(v + 0.5)` y rechaza lo que quede bajo 1. La capa solo acepta enteros ya
  calculados, porque un redondeo al alza silencioso rompería el tamaño de
  posición.

## 2026-09-25 — Fase beta (1/2): se corrige el sesgo de salida y se retira Telegram

### 1. El objetivo del día vuelve a ser el de la víspera

**Es lo único de la lógica de decisión que se ha tocado, y es la corrección del
sesgo que encontró la auditoría de ayer.**

`simulador.gestionar_posiciones` hacía dos cosas en este orden: recalcular el
objetivo con `_atr(df)` y `resistencia_reciente(df)` sobre un `df` que ya
incluía la barra del día cerrado, y después evaluar la salida contra ese nivel
recién puesto. En un día de máximos la resistencia de 20 sesiones ES el máximo
de hoy: el objetivo se clavaba ahí, `high >= objetivo` se cumplía con igualdad
exacta y la venta se registraba en el máximo EXACTO de la sesión. Ocurrió en
las 13 salidas por objetivo del periodo auditado, las 13.

Ahora el orden es el inverso: **primero se evalúa la salida contra el objetivo
vigente al abrir la sesión** (el calculado la víspera, que es el que un
operador tendría puesto como orden límite) y solo si la posición sobrevive se
recalcula el objetivo, para que rija mañana. Una posición que cierra hoy ya no
registra cambio de objetivo: mover la orden límite de algo vendido no
significa nada.

**La bitácora histórica NO se recalcula.** Lo ocurrido ocurrió así y
reescribirlo sería falsear el registro; la corrección vale desde hoy. El
impacto medido era de 0,24 pp por operación y 0,93 pp de rentabilidad de la
cuenta.

Tests: `tests/test_sesgo_objetivo.py`, con el caso real de MRNA del 2026-08-13
(operaciones 72 y 73) calcado de la bitácora.

### 2. Se retira Telegram por completo

Funcionó en producción —la pre-apertura del 25 envió sus órdenes de compra—
pero el canal deja de tener sentido cuando la ejecución pasa a ser automática:
avisar a un humano de lo que hay que teclear sobra si nadie va a teclearlo.

Eliminados: `centinela/notificaciones.py`, `scripts/notificar.py`,
`estado/notificaciones.json`, sus dos ficheros de tests, los jobs `notificar`
de los dos escaneos y el job `alertar` del Vigilante, las constantes de
Telegram de `config.py`, el resultado `omitido:sin-notificaciones` del
vocabulario (vuelve a tener tres motivos) y la publicación de problemas del
Vigilante, que solo existía para alimentar ese aviso. Los secrets
`TELEGRAM_TOKEN` y `TELEGRAM_CHAT_ID` se borraron del repositorio.

### 3. Limpieza del dashboard

- Fuera **"P&L acumulado por cartera"**: la Cuenta simulada queda como única
  cifra de rentabilidad, que era el objetivo desde que se introdujo.
- Fuera **"Detalle de posiciones abiertas — MFE/MAE"** y el job `mfe` del
  post-cierre. `scripts/analizar_mfe.py` se queda en el repositorio, pero ya no
  se ejecuta.
- La **curva de equity queda solo en dólares**; se retira el selector de suma
  de retornos y las series `pl_acumulado_*` del JSON.
- Consecuencia que había que resolver: el P&L no realizado de las posiciones
  abiertas salía de `reportes/mfe_actual.md`. Con el job `mfe` apagado ese
  informe se congela, y seguir leyéndolo habría publicado el P&L del día en que
  el análisis corrió por última vez **sin que nada lo dijera**. Ahora el
  generador lo valora contra los precios que ya descarga, por ratio contra el
  open del día de entrada, igual que `cuenta.curva_diaria`.
- Se retiran también los bloques `*_con_duplicados` de `datos.json`: se
  conservaban desde el 2026-09-10 "por si acaso", y al desaparecer la sección
  de P&L dejan de tener lector posible. `datos.json` baja de 55,3 a 44,6 KB y
  `index.html` de 49,5 a 44,7.

### 4. Capa del broker para XTB (preparación)

`centinela/broker_xtb.py`: fachada síncrona sobre el cliente no oficial de
xStation5, con el **candado demo** verificado en cada conexión (endpoint y
número de cuenta; "no se pudo determinar" cuenta como fallo, no como permiso) y
traducción de los desenlaces del broker al vocabulario de este repositorio. 26
tests con un cliente simulado, sin red.

`centinela/riesgo.py`: riesgo y resultados por operación en dólares y en
porcentaje, con el riesgo agregado si saltaran todos los stops a la vez.

### Hallazgos sobre XTB que condicionan el diseño

- **Solo hay un cliente viable**: `xtb-api-python` 0.10.0 (MIT, Python 3.12+).
  Los demás (`xapi-python` ★46, `xapi-node` ★64, `xapi-php`, `xapi-cpp`) están
  archivados: usaban la API oficial que XTB cerró el 14 de marzo de 2025.
  Auditadas sus 10.824 líneas: los únicos dominios a los que se conecta son de
  XTB.
- **No se puede modificar el take profit** de una posición abierta. Medido: con
  el TP fijo en el objetivo inicial, la Cartera A habría hecho +12,25% en vez
  de +12,07% y la B +11,15% en vez de +11,73%. Se acepta la divergencia.
- **No se puede cerrar una posición por id**: se vende el mismo volumen y la
  reconciliación comprueba que desapareció.
- **No hay acciones fraccionadas por la API**: el cliente redondea el volumen
  con `int(volume + 0.5)` y rechaza lo que quede bajo 1. Con $10.000 y 20 slots
  (un slot de $500), **23 de las 141 entradas del histórico no se habrían
  podido ejecutar** y un 15% del capital se pierde en redondeo. A partir de
  $50.000 no se pierde ninguna.
- **Salida por tiempo**: cerrar al cierre del día 10 (lo que simula hoy) se
  desvía 0,03 pp; hacerlo a la apertura del día 11 se desvía 0,40 pp de media
  pero con ±3,14 pp de dispersión, que es ruido de gap overnight que la
  estrategia no contempla.

## 2026-09-24 — Formato final: cuenta simulada, auditoría de fiabilidad y notificaciones

**No se tocó la lógica de decisión: ni el modelo, ni el umbral (0.79), ni las
features, ni el cálculo de objetivos, ni el stop, ni el backtest, ni el
simulador.** Todo lo que sigue es contabilidad derivada, comunicación y
presentación, sobre la bitácora que el sistema ya escribía.

### 1. La cifra principal pasa a ser el dinero, no la suma de retornos

Hasta hoy el sistema publicaba la SUMA de los retornos de cada operación:
+244,10% en la Cartera A. Esa cifra responde "cuánto rinde la estrategia por
operación", pero no responde "cuánto habría ganado mi dinero", que es la única
pregunta que importa si esto llega a operar en real. Faltaban dos cosas:
composición (una cuenta reinvierte) y capital finito (una cuenta tiene 20 slots
y no puede abrir la 21ª posición aunque la señal sea buena).

- **`centinela/cuenta.py` (nuevo)**: reinterpreta la bitácora como una cuenta.
  `equity_contable = caja + coste de las abiertas`; cada entrada invierte
  `equity/SLOTS` y el capital compone **al cerrar**. Acciones fraccionarias
  (con $10.000 y 20 slots un slot son ~$500, y SNDK cotizaba a $1.510: con
  acciones enteras la operación sería de cero acciones). Marca a mercado por
  RATIO contra el open del día de entrada de la misma serie descargada, no por
  precio absoluto: los precios de la bitácora se guardaron ajustados en su día
  y los de hoy están reajustados por dividendos posteriores (discrepancia
  medida: 0,07% de media). Las decisiones interpretativas están todas escritas
  en la cabecera del módulo.
- **`centinela/config.py`**: `CAPITAL_INICIAL_CUENTA` (10.000), `SLOTS_CUENTA`
  (= `MAX_POSICIONES_ABIERTAS`), `COMISION_SPREAD_POR_LADO` (0,10%) y
  `SLIPPAGE_MERCADO` (0,15%). Son parámetros de contabilidad: no entran en
  ninguna decisión de trading.
- `marcar_duplicadas` se muda de `scripts/generar_dashboard.py` a
  `centinela/cuenta.py`. La auditoría, las notificaciones y el dashboard usan
  ahora exactamente la misma definición; dos copias habrían acabado contando
  universos distintos sin que nadie se enterara. El nombre se reexporta desde
  el generador para no romper a quien lo importaba de allí.

Resultado (neto de fricciones, $10.000 por cartera, 47 sesiones):
**A +12,07% · B +11,73%**, frente al +3,05% del SPY en el mismo periodo.

### 2. Auditoría de fiabilidad — y un sesgo optimista encontrado

**`scripts/auditar_fiabilidad.py` (nuevo)** y su informe
`reportes/auditoria_fiabilidad.md`. Es solo lectura: mide, no arregla.

El hallazgo importante: **las 13 salidas por objetivo del periodo se ejecutaron
exactamente en el máximo de la sesión, las 13.** No es casualidad.
`simulador.gestionar_posiciones` recalcula el objetivo con `_atr(df)` y
`resistencia_reciente(df)` sobre un `df` que YA incluye la barra del día que
acaba de cerrar, y después evalúa la salida contra ese objetivo recién puesto.
En un día de máximos, la resistencia de 20 sesiones ES el máximo de hoy, el
objetivo se clava ahí y `high >= objetivo` se cumple con igualdad exacta. Un
operador real no puede vender ahí: su orden límite del martes es la que calculó
el lunes por la noche.

Cuantificado con una re-simulación completa que cambia UNA cosa —el objetivo de
cada sesión es el que ya estaba puesto al abrirla—: **0,24 puntos por operación
y 0,93 puntos de rentabilidad de la cuenta** (A pasaría de +12,07% a +11,14%).

**No se ha corregido**, por instrucción expresa del encargo. Queda escrito
dónde está el arreglo si algún día se hace: recalcular el objetivo antes de
evaluar la salida, o evaluar contra el objetivo de la víspera.

Lo que SÍ está bien y se verificó: stop antes que objetivo (supuesto
conservador aplicado), gaps ejecutados al open real, la decisión de entrada no
ve el open, y ninguna de las 47 sesiones se quedó sin post-cierre.

Lo más frágil no es el sesgo, es la **concentración**: 57 de 71 operaciones y
el 85% del P&L están en la cadena de valor del silicio. El filtro de
"drawdown >= 30% del ATH" seleccionó casi en bloque el mismo sector. Un giro
del ciclo golpearía las 20 posiciones el mismo día y el stop de la Cartera A no
protege de una caída correlacionada.

### 3. Notificaciones por Telegram

- **`centinela/notificaciones.py` (reescrito)**: el módulo estaba preparado y
  desactivado desde el inicio. Ahora envía de verdad, con reintentos y backoff
  exponencial, y **lanza si no puede enviar**. Antes devolvía `False` y se
  tragaba el error: un Telegram caído era indistinguible de un día sin
  noticias, que es exactamente el modo en que este repositorio ha perdido días
  enteros dos veces.
- Siete tipos de mensaje, todos con cartera, ticker y precios a dos decimales:
  orden de compra (pre-apertura, con importe y número de acciones según la
  cuenta simulada), entrada confirmada, actualización de objetivo, venta
  ejecutada, aviso de salida por tiempo de mañana, resumen diario y alerta del
  Vigilante. Las funciones que construyen el texto son puras y se prueban sin
  red.
- **Anti-duplicados**: cada aviso lleva un id `fecha|tipo|cartera|ticker`
  registrado en `estado/notificaciones.json`. La escalera de crons puede
  reejecutar un peldaño sin que llegue nada dos veces. El id se marca DESPUÉS
  de un envío correcto, para que un fallo sea reintentable.
- **`scripts/notificar.py` (nuevo)**: reúne los datos y decide qué toca enviar.
- **`config.CARTERAS_NOTIFICADAS`**: `["A", "B"]`. Para dejar de recibir una,
  se quita de esa lista y nada más.

**La regla dura del diseño**: el envío vive en un JOB APARTE de los workflows,
con `needs`, igual que el análisis MFE y el dashboard. Cuando ese job corre, la
bitácora y el estado ya están persistidos y VERIFICADOS contra el remoto. Un
Telegram caído deja el job en rojo y no revierte ni bloquea nada. El aviso del
Vigilante va en su propio job por la razón simétrica: si fallara dentro del
Vigilante, un Telegram caído podría enmascarar justo la avería recién
detectada.

- `centinela/resultados.py`: nuevo `omitido:sin-notificaciones`. El vocabulario
  es cerrado por diseño, así que añadir un motivo obliga a decidir
  explícitamente si puede terminar sin commit. Este puede: no tener nada que
  enviar es legítimo; no PODER enviar, no.
- `scripts/vigilante.py`: publica los problemas en `GITHUB_OUTPUT` para que el
  job de alerta los ponga en el mensaje. Sigue sin escribir nada en el repo.

### 4. Dashboard

- Nueva sección **"Cuenta simulada"** arriba del todo: capital inicial, capital
  actual, rentabilidad, CAGR, drawdown máximo y Sharpe, netos, para A y B. La
  suma de retornos se queda más abajo con su etiqueta de siempre.
- La **curva de equity** pasa a ser DIARIA (un punto por sesión, no por fecha
  de salida) y su serie principal es el valor de la cuenta en dólares, marcado
  a mercado. La suma de retornos sigue disponible como vista secundaria con un
  selector. Una curva con puntos solo en los cierres escondía justo los tramos
  de caída.
- Nueva sección **"Órdenes activas"**: órdenes límite de venta, órdenes stop
  (solo A) y ventas al cierre programadas, ordenadas por urgencia.
- El generador ya no es puramente offline: necesita precios para marcar a
  mercado. Si falta el precio de una posición abierta **falla en rojo** en vez
  de valorarla a coste, porque eso publicaría un drawdown y un Sharpe distintos
  sin que nada lo dijera. Vive en su propio job: si revienta, la web se queda
  con los datos de ayer y el trading no se entera.
- **Techo de 50 KB respetado** (49,5 KB). Para hacer sitio se eliminó
  duplicación real: la paleta de tema oscuro estaba escrita DOS veces idéntica
  (media query + override manual). Un script mínimo en el `<head>` resuelve
  `data-tema` antes de que se aplique el CSS, así la paleta se declara una sola
  vez y además desaparece el destello claro al abrir en modo oscuro.

### 5. Tests

De 123 a 181. Nuevos: `tests/test_cuenta.py` (17) con la aritmética de la
cuenta, las fricciones y el drawdown contra números calculados a mano;
`tests/test_notificaciones.py` (24) con el transporte, el anti-duplicados y
cada tipo de mensaje; `tests/test_aislamiento_notificaciones.py` (15) que
comprueba por los dos lados que un fallo de Telegram no toca la bitácora: el
código no la abre en escritura y los workflows aíslan el job.

`requirements-dev.txt` (nuevo) separa pytest y PyYAML de las dependencias que
corren en los workflows de trading.

### Estado del sistema revisado

Últimos 300 runs: 291 en verde, 9 cancelados (todos por la cola del grupo de
concurrencia, ninguno un fallo). Reentrenamiento del 1 de septiembre en verde y
el cron del 1 de octubre intacto. Integridad de datos: cero posiciones
duplicadas nuevas desde el fix del 2026-08-06, estado y bitácora coherentes
(8 abiertas en cada cartera, mismos ids), y ningún precio nulo o absurdo.

## 2026-09-10 (2) — Dashboard: se elimina el toggle de auditoría

**Solo presentación del dashboard. No se tocó el modelo, el umbral (0.79), las
features, los objetivos, el stop, el backtest ni el simulador, y no se ha
borrado ni una fila de `bitacora.csv`.**

### Qué pasaba

El cambio anterior de hoy mismo invirtió el toggle "Solo operaciones limpias"
por uno de auditoría ("Mostrar operaciones duplicadas por bug"), apagado por
defecto, para poder seguir viendo las 13 duplicadas del bug del 2026-08-06 sin
que fueran el dato por defecto. En la práctica nadie lo usa: es una casilla que
solo sirve para volver a mezclar estadísticas con un bug ya corregido, y su
presencia (checkbox, nota permanente explicándolo, badge "Duplicada") es ruido
visual sin lector.

### Qué cambia

- `scripts/plantilla_dashboard.html`: se elimina el toggle "Mostrar operaciones
  duplicadas por bug (auditoría)", su casilla, su detalle, la marca "Vista de
  auditoría", el JS que alternaba entre las dos vistas (`aplicarVista`,
  `pintarTodo`, la clave de `localStorage` `centinela-auditoria`) y el badge
  "Duplicada" de la tabla (ya no hace falta: las duplicadas nunca se pintan).
  También desaparece la nota permanente que explicaba el toggle. En su lugar
  hay una nota corta y no técnica: **"Estadísticas del sistema en operación.
  Ver histórico completo en el repositorio."**, con "repositorio" enlazando al
  repo de GitHub.
- La tabla sigue filtrando por `es_duplicada` (ahora de forma incondicional,
  sin toggle que la desactive): las 13 duplicadas nunca aparecen en pantalla.
- `scripts/generar_dashboard.py`: **sin cambios**. Se decide conservar los
  bloques `resumen_con_duplicados`, `comparativa_ab_con_duplicados`,
  `pnl_por_cartera_con_duplicados` y `curva_equity_con_duplicados` en
  `datos.json` en vez de quitarlos. Razón: pesan ~2 KB, muy por debajo del
  presupuesto de 500 KB, y quitarlos no simplifica nada que importe — el HTML
  ya no los lee, así que no hay complejidad de la que librarse quitándolos del
  JSON. Mantenerlos deja la puerta abierta a una consulta programática o un
  script de auditoría futuro sin tener que releer y reprocesar `bitacora.csv`
  a mano. Cada operación conserva su flag `es_duplicada`, que es la fuente de
  verdad real para cualquier auditoría.
- `tests/test_dashboard.py`: se eliminan los tests del toggle
  (`test_html_tiene_la_nota_permanente_y_el_toggle_de_auditoria`) y se
  añaden dos en su lugar: uno que confirma que no queda ningún rastro del
  toggle en el HTML, y otro que confirma la nota nueva. Los tests de schema de
  `datos.json` y de los cálculos limpios/con-duplicados no cambian: siguen
  siendo el contrato de los datos, independientemente de qué pinte el HTML.

### Qué NO cambia

`bitacora.csv` sigue teniendo sus filas tal cual, duplicadas incluidas: siguen
siendo la fuente para cualquier auditoría real. El simulador no se toca. La
flag `es_duplicada` de cada operación tampoco se toca: solo deja de tener un
control de UI que la muestre u oculte a demanda, porque ya se oculta siempre.

### Verificación

123 tests en verde. Renderizado real (jsdom, sin navegador Chrome conectado en
esta sesión): el toggle ya no existe en el DOM, las tarjetas muestran los
mismos números limpios de siempre, y cero errores de consola.

## 2026-09-10 — Dashboard: se invierte el toggle de duplicadas

**Solo presentación del dashboard. No se tocó el modelo, el umbral (0.79), las
features, los objetivos, el stop, el backtest ni el simulador, y no se ha
borrado ni una fila de `bitacora.csv`.**

### Qué pasaba

El bug corregido el 2026-08-06 (filtro de tickers ocupados que solo miraba la
Cartera A) dejó una cicatriz: 13 entradas duplicadas del mismo ticker en la
misma cartera. El dashboard las incluía por defecto en las cuatro tarjetas de
resumen, la comparativa A vs B, la curva de equity y la tabla, con un banner
ámbar de aviso y un toggle "Solo operaciones limpias" para ocultarlas si el
usuario elegía activarlo.

Eso era al revés de lo que hace falta para leer el sistema día a día: las
estadísticas que de verdad importan (¿está funcionando la estrategia?) son las
limpias, y tenerlas detrás de un toggle apagado por defecto significa que la
lectura habitual del dashboard es la mezclada con el bug.

### Qué cambia

- Los datos LIMPIOS (sin las 13 duplicadas) pasan a ser los que alimentan las
  tarjetas de resumen, la comparativa A vs B, la curva de equity y la tabla por
  defecto, en `scripts/generar_dashboard.py`.
- Los datos CON duplicados se siguen calculando y publicando en `datos.json`,
  ahora bajo las claves `resumen_con_duplicados`, `comparativa_ab_con_duplicados`,
  `pnl_por_cartera_con_duplicados` y `curva_equity_con_duplicados`: cambia su
  rol (dejan de ser el defecto), no su cálculo ni su disponibilidad.
- El banner ámbar desaparece: con datos limpios por defecto ya no hay
  estadísticas mixtas que avisar, y un aviso sin nada que decir es ruido.
- El toggle se invierte y se renombra: "Mostrar operaciones duplicadas por bug
  (auditoría)", apagado por defecto. Encenderlo vuelve a los datos con
  duplicados y marca cada una en la tabla con el badge "Duplicada". Guarda su
  estado en una clave de `localStorage` nueva (`centinela-auditoria`), distinta
  de cualquier interruptor anterior, para que nadie herede un estado
  incoherente con la semántica invertida.
- Nota permanente y discreta bajo las tarjetas de resumen explicando que las
  cifras excluyen las 13 duplicadas del bug del 6 de agosto de 2026, con enlace
  a este CHANGELOG y referencia al toggle de auditoría.
- Cada operación del JSON lleva ahora la flag `es_duplicada` (antes
  `duplicada`), que es lo que la tabla usa para filtrar según el toggle.

### Qué NO cambia

`bitacora.csv` conserva sus filas tal cual, duplicadas incluidas: son historia
auditable del bug, no se destruyen. El simulador no se toca — la corrección de
fondo ya vive ahí desde el 6 de agosto — y este cambio es exclusivamente de
presentación por defecto en el dashboard. Los cálculos de win rate, expectancy,
profit factor y P&L (la función `_vista`) son literalmente los mismos de
siempre; solo cambia cuál de las dos vistas que ya existían es la que se pinta
sin tener que pedirlo.

### Verificación

122 tests en verde, incluidos los nuevos: esquema de `datos.json` con los
nombres `_con_duplicados`, conteo de filas de la tabla con el toggle apagado y
encendido, que las tarjetas por defecto usan los cálculos limpios, y que
`bitacora.csv` no pierde ni una fila al generar el dashboard.

## 2026-08-27 — INCIDENCIA: el cron de GitHub se retrasó ~10 h y se perdió una sesión

**Solo infraestructura y detección. No se tocó el modelo, el umbral (0.79), las
features, los objetivos, el stop ni el backtest.**

### Qué pasó

Los siete disparos programados del workflow *Escaneo pre-apertura* del jueves
2026-08-27 (día de mercado normal, sesión 09:30–16:00 ET) fueron creados por
GitHub entre **9h40m y 10h57m tarde**, todos pasada la apertura. Los siete
murieron en rojo con `fallo:ventana-perdida` y **la ventana de decisión de esa
sesión se perdió**. Los cuatro disparos del *Escaneo post-cierre* del mismo día
tampoco habían llegado a existir a las 01:07 UTC del 28, a tres horas de que la
fecha ET rodara y la sesión se quedara sin cerrar.

No hubo bug en el código. En los siete runs la API de Actions devuelve
`created_at == started_at`: el retraso está **antes** de que el run exista, en el
scheduler de cron de GitHub. No es la cola de runners ni el job.

| run | creado (UTC) | cron | retraso |
|---|---|---|---|
| [33106300280](https://github.com/sebas1331/centinela-sp500/actions/runs/33106300280) | 19:00 | 08:03 | +10h57m |
| [33107633605](https://github.com/sebas1331/centinela-sp500/actions/runs/33107633605) | 19:16 | 08:53 | +10h23m |
| [33111130470](https://github.com/sebas1331/centinela-sp500/actions/runs/33111130470) | 19:59 | 09:43 | +10h16m |
| [33114423249](https://github.com/sebas1331/centinela-sp500/actions/runs/33114423249) | 20:39 | 10:33 | +10h06m |
| [33116319865](https://github.com/sebas1331/centinela-sp500/actions/runs/33116319865) | 21:03 | 11:23 | +9h40m |
| [33121657602](https://github.com/sebas1331/centinela-sp500/actions/runs/33121657602) | 22:14 | 12:13 | +10h01m |
| [33124245743](https://github.com/sebas1331/centinela-sp500/actions/runs/33124245743) | 22:53 | 13:03 | +9h50m |

El diseño del 2026-07-27 hizo su trabajo: el día se perdió **en rojo**, no en
verde. Lo que faltó fue alcance (la escalera cubría ~5 h de retraso, no 11) y una
señal que sumara los siete rojos en vez de tratarlos como fallos sueltos.

### Qué se ha hecho

- **Escalera de la pre-apertura: de 7 a 16 peldaños**, arrancando a las 00:33 UTC
  del mismo día. Cubre retrasos de 0 a ~12 h en EDT y EST.
- **Escalera del post-cierre: de 4 a 9 peldaños**, arrancando a las 15:07 UTC.
  Cubre retrasos de 0 a ~12 h antes de que la fecha ET ruede.
- `VIGILANTE_MARGEN_HORAS`: **8 → 14**. Con retrasos de 11 h, 8 h de margen
  producían falsos rojos por un post-cierre que llegó tardísimo pero llegó. A las
  14:37 UTC el Vigilante sigue exigiendo la sesión del día anterior.
- **El Vigilante detecta rachas de rojos** (`VIGILANTE_RACHA_MINIMA = 3` en
  `VIGILANTE_RACHA_HORAS = 24`). Consulta la API de Actions con timeout explícito
  y tres reintentos con backoff exponencial, y emite
  `EMERGENCIA: N pre-aperturas rojas seguidas desde HH:MM. Ver runs <IDs>.`
  Si la API no responde tras los reintentos, **sale en rojo**: un vigilante ciego
  que dice "todo bien" es el mismo silencio verde que este repo persigue.
  Requiere el permiso `actions: read`, añadido al workflow.
- Tests nuevos: la escalera se verifica ahora contra 0–12 h a paso de 5 min,
  contra los retrasos reales de este día, y con la comprobación de que la
  escalera vieja **sí** fallaba en ese escenario (para que el test no pase por
  vacuidad). Más nueve tests de la detección de rachas. **116 tests en verde.**

### La sesión del 2026-08-27

- **Pre-apertura: perdida, y así queda.** No se recuperó a propósito. Forzarla
  significaría decidir las entradas del 27 conociendo ya los precios del 27, y el
  post-cierre las simularía comprando al open de una sesión cerrada: look-ahead
  bias. Contaminar el experimento cuesta más que perder un día. Documentada en
  `logs/decisiones-2026-08-27.log`.
- **Post-cierre: recuperado.** Run
  [33131874415](https://github.com/sebas1331/centinela-sp500/actions/runs/33131874415),
  lanzado a mano a las 21:08 ET, **dentro de su ventana legítima** y antes de que
  la fecha ET rodara — el control de ventana del script lo validó por su cuenta,
  sin `--forzar`. El post-cierre no admite look-ahead: cierra posiciones y
  recalcula objetivos con datos ya públicos. Cerró CIEN en la cartera B por
  límite de tiempo (−9,46%) y recalculó 4 objetivos. Cero entradas ejecutadas,
  coherente con que la pre-apertura no decidiera ninguna.

### Una trampa que casi silencia el hueco

La nota que documenta la sesión perdida citaba literalmente la cabecera
`===== escaneo <tipo> ... =====` para explicar que **no** la llevaba. El
Vigilante no lee prosa: busca esa cadena en el fichero, la encontró dentro de la
explicación y dio la pre-apertura por corrida. Documentar el hueco casi lo tapó.
Corregido, y fijado con `test_documentar_un_hueco_no_lo_tapa`, que vale para
cualquier sesión futura.

## 2026-08-06 — BUG: posiciones duplicadas del mismo ticker en una cartera (v0.4.0)

**Corrección del simulador y de su reporting. No se tocó el modelo, el umbral
(0.79), las features, los objetivos, el stop ni el backtest.**

> **Las estadísticas anteriores a esta corrección están sesgadas por entradas
> duplicadas.** Afecta casi por completo a la Cartera B y a toda comparativa
> A vs B calculada antes del 2026-08-06.

### El bug

`simulador.tickers_ocupados()` decidía qué tickers ya estaban en cartera mirando
**siempre las posiciones de la cartera A**, también cuando la pregunta era sobre
la B:

```python
ocup = {p["ticker"] for p in estado["posiciones"].get("A", [])}   # ← siempre "A"
```

Cada entrada abre posición en A y en B a la vez. A cierra pronto —13 de 16
cerradas fueron por stop, media de 4.75 días hábiles— mientras B, que no tiene
stop, aguanta los 10 días completos —10 de 12 salidas por tiempo, media de 9.33
días—. Así que en cuanto un ticker volvía a dar señal, el filtro veía el hueco
libre de A, daba la entrada por buena y abría una **segunda posición en B encima
de la primera, que seguía viva**.

Por eso el fallo es **asimétrico por construcción**: 13 duplicados, los 13 en B y
ninguno en A.

### Casos afectados (13 entradas, todas en Cartera B)

| Ticker | id duplicado | Entró | Ya abierta desde |
|---|---|---|---|
| COHR | 24 | 2026-07-28 | id10 (2026-07-21) |
| COHR | 44 | 2026-07-30 | id10 y id24 |
| SNDK | 22 | 2026-07-28 | id2 (2026-07-21) |
| SNDK | 42 | 2026-07-30 | id2 y id22 |
| GLW | 26 | 2026-07-28 | id4 (2026-07-21) |
| MRVL | 32 | 2026-07-29 | id6 (2026-07-21) |
| WDC | 34 | 2026-07-29 | id8 (2026-07-21) |
| LITE | 46 | 2026-07-30 | id28 (2026-07-28) |
| TER | 50 | 2026-07-30 | id38 (2026-07-29) |
| CIEN | 56 | 2026-07-31 | id12 (2026-07-22) |
| KLAC | 58 | 2026-07-31 | id20 (2026-07-23) |
| ON | 64 | 2026-08-03 | id14 (2026-07-22) |
| LRCX | 66 | 2026-08-04 | id52 (2026-07-31) |

### Cuánto de la ventaja de B era el bug

Solo dos duplicados han cerrado ya, y los dos fueron COHR/B (+35.13% y +42.84%).
Bastan para dar la vuelta al resultado de la cartera:

| Cartera B | Con duplicados | Sin duplicados |
|---|---|---|
| P&L realizado | **+31.05%** | **−46.91%** |
| P&L total (con abiertas) | +254.39% | +45.22% |
| Operaciones cerradas | 12 | 10 |
| Win rate | 33.3% | 20.0% |
| Expectancy | +2.59% | −4.69% |
| Profit factor | 1.52 | **0.22** |

La Cartera A no cambia (no tiene ni un duplicado): realizado −108.08%, win rate
12.5%, profit factor 0.33.

Es decir: **B seguía batiendo a A en P&L, pero no era rentable, y por profit
factor pasa a ser PEOR que A** (0.22 frente a 0.33). El "B supera a A" del
backtest no queda refutado por esto —son cosas distintas—, pero la evidencia
en vivo que parecía respaldarlo era, en su mayor parte, el bug.

### El arreglo

- `tickers_ocupados(estado, cartera)` recibe ahora **la cartera** y mira sus
  propias posiciones. Es la línea que causaba todo.
- `registrar_decisiones_entrada()` calcula la ocupación de A y de B por separado
  y guarda en la entrada pendiente el campo **`carteras`** con aquellas en las
  que sí puede ejecutarse. `ejecutar_entradas_pendientes()` lo respeta y solo
  abre ahí. Las pendientes escritas antes de este cambio no traen el campo y
  siguen valiendo para las dos, así que un estado antiguo no rompe nada.
- Las candidatas frenadas quedan **en el log del día**, con su drawdown y su
  probabilidad:

  ```
  SNDK | dd=46.5% | prob=0.951 | ENTRADA DESCARTADA: ya hay posición abierta en Cartera A y Cartera B.
  COHR | dd=61.3% | prob=0.795 | ENTRADA SOLO EN CARTERA A: ya hay posición abierta en Cartera B.
  ```

  Antes, una candidata con señal que no entraba simplemente desaparecía del
  registro y era indistinguible de una que nunca la tuvo.
- `screener` añade `dd` a la candidata. Es un campo **informativo** para poder
  escribir esa línea; no interviene en ninguna decisión.

**Compensación que este diseño acepta a conciencia:** la regla es por cartera,
así que un ticker puede entrar en A y no en B. Eso rompe el emparejamiento
perfecto A/B en esos casos concretos, y la comparativa deja de ser "las mismas
entradas con stop y sin stop". La alternativa —bloquear en las dos si está
ocupada en una— mantendría el emparejamiento pero haría que la cartera B, que
retiene más tiempo, le robase entradas legítimas a la A. Se eligió lo primero
porque distorsiona menos el comportamiento de cada cartera por separado, pero
conviene tenerlo presente al leer la comparativa a partir de ahora.

### Las duplicadas históricas NO se borran

Siguen en la bitácora tal cual. Son cicatriz del bug, no fraude, y el registro
tiene que reflejar lo que pasó de verdad. Lo que se añade es la forma de leerlas:

- **Aviso en la cabecera del dashboard** (ámbar, descartable, recordado en
  `localStorage`) diciendo que las estadísticas incluyen operaciones duplicadas
  y que la comparativa A vs B solo es interpretable desde esta fecha.
- **Interruptor "Solo operaciones limpias"**, que recalcula resumen, P&L por
  cartera, comparativa A vs B, curva de equity y tabla usando únicamente las
  entradas no duplicadas. Arranca SIEMPRE apagado: por defecto se enseña lo que
  ocurrió, y filtrar es un acto explícito.
- Cada entrada duplicada lleva su etiqueta **Duplicada** en la tabla.
- `datos.json` publica los bloques paralelos `resumen_limpio`,
  `comparativa_ab_limpia`, `pnl_por_cartera_limpio` y `curva_equity_limpia`,
  calculados en Python por la **misma** función que los normales, para que las
  dos vistas no puedan divergir en la definición de ninguna métrica.

### Tests

De 84 a **106**. `tests/test_no_duplicar_posiciones.py` (13) cubre la regla por
cartera, la entrada parcial en la cartera libre, el formato del log, las
pendientes del día como ocupación, la compatibilidad con estados antiguos y que
la ejecución respete la decisión. Los otros 9 cubren la detección de duplicadas,
el schema de los bloques limpios y que el filtro excluya lo que debe.

Dry-run forzado del 2026-08-06 sobre una copia aislada del repo: de 11 señales,
6 descartadas por duplicado con el motivo correcto en el log y 2 entradas nuevas
(APP, MRNA) con `carteras: ["A","B"]`. Las otras 3 ni se evaluaron porque el cupo
del día era 2 (A tenía 18 posiciones de un tope de 20), comportamiento anterior y
ajeno a este cambio.

---

## 2026-08-06 — P&L por cartera y curva de equity en el dashboard (v0.3.1)

**Solo presentación. No se tocó el modelo, el umbral (0.79), las features, los
objetivos, el stop ni el backtest.** El dashboard sigue siendo de solo lectura
sobre la bitácora.

### P&L acumulado por cartera (realizado vs. total)

Cuatro tarjetas nuevas entre el resumen y la comparativa A vs B, agrupadas en dos
columnas. Por cartera: **realizado** (solo cerradas, no puede cambiar) y **total**
(realizado + marca a mercado de las abiertas, que se mueve cada día). El P&L no
realizado sale del informe MFE/MAE que ya existía; no se recalcula nada.

Debajo, la nota que evita la lectura equivocada: *"Suma de retornos con posiciones
equiponderadas (cada trade pesa igual). No es una curva de capital compuesta —
este experimento no modela asignación de capital."* Si alguna posición abierta
todavía no tiene fila en el informe MFE, la nota **lo dice y cuenta cuántas**, en
vez de servir un total corto con aire de definitivo (`abiertas_sin_pnl`).

### Curva de equity

Gráfico de líneas **SVG construido a mano con JS vanilla** — sin Chart.js, D3 ni
ningún CDN, como manda la regla del proyecto. Un punto por fecha de salida, con
los cierres del mismo día agregados; eje X temporal real (los fines de semana se
ven como tramos más anchos), eje Y automático con 10% de margen y línea de
referencia punteada en 0%.

- **Solo operaciones cerradas.** La curva es de resultado realizado: mezclar lo
  no realizado la haría cambiar de forma hacia atrás cada día. Lo no realizado
  vive en las tarjetas de arriba, que sí avisan de que se mueve.
- Cartera A en azul continuo, Cartera B en violeta discontinuo. Las series se
  distinguen **también por trazo**, para que la lectura no dependa de percibir
  bien el color. El violeta es el único tono frío que no colisiona con el
  verde/rojo (ganancia/pérdida) ni con el azul.
- Interacción con eventos de puntero unificados (`pointerdown/move/up/cancel`) y
  `setPointerCapture`: hover en escritorio y **tap sostenido** en móvil, con
  línea guía, círculos en ambas series y globo con fecha, acumulado de A y B y
  nº de cerradas hasta esa fecha. `touch-action:pan-y` para no secuestrar el
  scroll vertical de la página.
- Con **menos de 3 operaciones cerradas** no se dibuja nada: se explica que aún
  no hay suficientes. El JSON se genera igual y válido.

Alto 320 px en escritorio y 240 px en móvil, repintado al cambiar de tamaño. El
tema NO obliga a repintar: los colores son variables CSS.

### Detalles que costaron una segunda pasada

- El eje Y se quedaba en dos marcas en móvil. El redondeo a "número bonito" con
  cortes en 1/2/5 convertía un paso ideal de 5.6 en 10. Ahora los cortes están en
  las medias geométricas (√2, √10, √50) y el eje da siempre ~4 líneas.
- Los decimales del eje los fija el **paso**, no cada valor: antes convivían
  `-150%` y `-50.0%` en el mismo eje.
- El gráfico no usa `.caja`: en móvil esa clase se despoja de fondo y borde a
  propósito (ahí la tabla se vuelve tarjetas), y el gráfico debe seguir siendo
  una tarjeta a cualquier ancho.

### Tests

De 73 a **84**. Los 11 nuevos cubren el schema ampliado, el realizado/total por
cartera (incluido el caso de una abierta sin P&L conocido), la curva punto a
punto contra una bitácora sintética, la agregación de cierres del mismo día, que
las abiertas no entren en la curva, y los casos de <3 cerradas y de 0 cerradas.

Un test propio (`test_html_es_autocontenido_y_responsive`) falló al añadir SVG,
porque el **namespace XML** `http://www.w3.org/2000/svg` contiene `http://`. Es un
identificador, no una descarga: se descuenta explícitamente antes de auditar en
vez de relajar la prohibición, que es la que impide colar un CDN de verdad.

---

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
## 2026-09-01 — Reentrenamiento mensual
- Datos: 231,467 eventos, hasta 2026-08-17.
- Precisión walk-forward (fresca): 82.0% (señales=9171).
- Operaciones cerradas nuevas desde 2026-08-02: 79.
- Umbral SIN cambios (0.79). Regla dura: se requieren ≥30 cierres nuevos y evidencia. Solo se re-ajustaron los pesos con datos nuevos.
## 2026-10-01 — Reentrenamiento mensual
- Datos: 233,113 eventos, hasta 2026-09-16.
- Precisión walk-forward (fresca): 84.4% (señales=10284).
- Operaciones cerradas nuevas desde 2026-09-01: 58.
- Umbral SIN cambios (0.79). Regla dura: se requieren ≥30 cierres nuevos y evidencia. Solo se re-ajustaron los pesos con datos nuevos.
