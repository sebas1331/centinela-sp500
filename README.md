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

> ⚠️ **Hasta el 2026-08-06 el simulador pudo abrir posiciones duplicadas** (una
> segunda entrada del mismo ticker en la misma cartera teniendo la primera aún
> viva), por el bug descrito en el [CHANGELOG](CHANGELOG.md). Son 13, todas en
> la Cartera B, y el dashboard **ya no las cuenta en ninguna cifra**: todo lo
> que se publica sale de la vista limpia. Las filas siguen en `bitacora.csv`
> para cualquier auditoría.

Está pensado **para el móvil primero**: en pantalla pequeña cada operación se
convierte en una tarjeta con el ticker y su P&L destacados, sin scroll lateral.

Qué hay dentro:

- **Cuenta simulada** *(la cifra principal)* — capital inicial y actual,
  rentabilidad, CAGR, drawdown máximo y Sharpe, para A y para B, **netos** de
  comisión, spread y slippage. Es la respuesta a "¿cuánto valdría mi dinero?",
  que no es la misma pregunta que responde la suma de retornos de más abajo.
- **Órdenes activas** — lo que debería estar puesto hoy en el broker: órdenes
  límite de venta (el objetivo vigente de cada posición), órdenes stop (solo la
  Cartera A) y ventas al cierre programadas, con las que vencen mañana en ámbar.
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

## 🩺 Operativa: ¿está funcionando ahora mismo?

**→ [sebas1331.github.io/centinela-sp500/operativa.html](https://sebas1331.github.io/centinela-sp500/operativa.html)**

Segunda página, enlazada con el dashboard. Contestan preguntas distintas y por
eso están separadas: el dashboard dice **cuánto gana la estrategia**; esta dice
**si el sistema está vivo y si XTB y el simulador cuentan lo mismo**. Es la que
hay que abrir cuando algo huele mal, y la que resume en un vistazo lo que antes
obligaba a rebuscar en la pestaña Actions.

Arriba del todo, la fila **Hoy**: `señales → decididas → enviadas → ejecutadas`,
y debajo quién se quedó en cada escalón y por qué. Nace del 2026-09-29, cuando
la página decía «Compras en XTB: ok» con cero órdenes enviadas: era cierto —una
señal, descartada por duplicado— pero averiguarlo obligaba a leer 187 líneas de
log en Actions. Un cero con motivo es información; un cero sin motivo es una
pregunta.

Debajo, el **semáforo** con los motivos escritos:

| Color | Cuándo |
|---|---|
| 🔴 **Problema** | un componente crítico terminó en rojo · la sesión de XTB caducó · saltó el candado de demo · la reconciliación encontró diferencias · una posición pasó de su fecha de salida y sigue abierta |
| 🟠 **Atención** | una salida por tiempo se cerró con el plan B · la sesión de XTB caduca en menos de 3 h · un componente lleva más de 30 h sin correr · una orden se rechazó en los últimos 3 días |
| 🟢 **Todo en orden** | ninguna de las anteriores |

Un rojo **no esconde los ámbares**: se listan todos los motivos, porque quien
entra a arreglar algo quiere ver todo lo que hay, no solo lo más grave.

Debajo, en este orden: los **ocho componentes** con su última ejecución y
enlace a su run; la **cuenta en XTB** (saldo, equity, invertido, P&L abierto y
el candado); las **posiciones abiertas**, cruzando lo que sabe el broker
(acciones, precio) con lo que sabe el simulador (objetivo, stop, fecha de
salida), destacadas si salen mañana o si están a menos de un 2 % del stop; el
**historial de órdenes** con buscador y filtros, el precio pedido frente al
ejecutado y el slippage; el **historial de objetivos y stops**; la
**reconciliación**; y las **alertas recientes** del Vigilante.

**Lo que esta página no hace:** no llama a la API de GitHub —eso exigiría un
token en una página pública—, así que cada workflow deja escrito cómo le fue en
`estado/salud.json` y la página solo lee un JSON estático.

**Privacidad.** Es pública, así que el número de cuenta sale enmascarado
(`•••385`) y no se escribe **nada** de la sesión: ni TGT, ni cookies, ni
credenciales. Hay un test que busca cada secreto conocido dentro del JSON
publicado y falla si aparece alguno.

**Datos viejos.** Un verde pintado con información de hace tres días es peor que
no tener página: da tranquilidad sin haberla comprobado. Por eso el navegador
compara la hora de generación con la actual y, pasadas 30 h, avisa; pasadas 72,
lo pinta en rojo. Esa comprobación **solo puede empeorar** el color, nunca
mejorarlo. El Vigilante republica la página **todos los días**, fines de semana
incluidos, justo para que ese reloj no salte sin motivo en un puente largo.

Se regenera y se publica al final de cada ejecución que toca XTB (compras,
ventas, post-cierre), del Vigilante y del reentrenamiento, siempre **en un job
aparte con `needs`**: si la página falla, la bitácora y el estado ya están
persistidos y verificados, y nadie depende de ella.

```bash
python scripts/generar_operativa.py
python -m http.server 8000 --directory docs   # /operativa.html
```

---

### Coste de ejecución de las compras

La página Operativa compara el precio pagado en XTB con el de **apertura del
simulador**, compras normales y **entradas tardías por separado** (una entrada
tardía es cara por diseño y no puede esconder ni inflar el coste de comprar al
abrir). Se muestra la media de las **últimas 10** de cada grupo, y el semáforo
pasa a **ámbar** si alguna media supera el **0,5 %** (el simulador ya descuenta
0,25 % por compra). Excluye las operaciones de series rotas.

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

## 🔍 ¿Es fiable el rendimiento que se reporta?

El sistema se auditó a sí mismo con
[`scripts/auditar_fiabilidad.py`](scripts/auditar_fiabilidad.py), que lee la
bitácora, vuelve a descargar los precios y trata de romperla. El informe
completo está en
[`reportes/auditoria_fiabilidad.md`](reportes/auditoria_fiabilidad.md).

**Cuenta de $10.000 por cartera, 20 slots, neta de fricciones, 47 sesiones:**

| | Cartera A | Cartera B | SPY |
|---|---|---|---|
| Rentabilidad | **+12.07 %** | **+11.73 %** | +3.05 % |
| Drawdown máximo | −10.44 % | −12.16 % | −3.06 % |
| Sharpe aprox. | 1.92 | 1.83 | 1.35 |
| Sin las 5 mejores operaciones | +4.26 % | +5.38 % | — |

**Lo que es de fiar:** la selección de entradas. Sigue siendo rentable sin sus
cinco mejores operaciones, y el stop de la Cartera A no destruye valor — A y B
acaban a menos de un punto de distancia.

**Lo que no:**

1. **Un sesgo optimista en las salidas.** Las 13 salidas por objetivo del
   periodo se ejecutaron **en el máximo exacto de la sesión**, las 13. El
   objetivo se recalcula con la barra del día ya cerrada y después se evalúa la
   salida contra ese nivel recién puesto, así que la simulación vende a un
   precio en el que ninguna orden límite real estaba esperando. Vale 0.93
   puntos de rentabilidad de la cuenta. Está medido y **no corregido**, a
   propósito: tocarlo sería cambiar la lógica de decisión en caliente.
2. **La concentración.** 57 de 71 operaciones y el 85 % del P&L están en la
   cadena de valor del silicio. El filtro de "drawdown ≥ 30 % del ATH"
   seleccionó casi en bloque el mismo sector. Esto no es una cartera
   diversificada del S&P 500; es una apuesta al ciclo de los semiconductores
   con 20 patas, y el stop no protege de una caída correlacionada.
3. **Dos meses no demuestran nada.** El CAGR del 84 % es un artefacto de
   anualizar 47 sesiones, todas en mercado alcista. Sin un tramo bajista no hay
   forma de separar el alfa de la beta (que es 1.72).

## 🔑 Si ves `XTB_REQUIERE_CODIGO`: 3 pasos desde el móvil

La sesión de XTB dura **8 horas** y XTB no ofrece códigos de app (sus métodos
son SMS, notificación push y correo), así que cada cierto tiempo hace falta una
persona. Cuando eso pasa, el run muere en rojo con este mensaje:

```
XTB_REQUIERE_CODIGO: la sesión caducó. XTB acaba de enviarte un código
de verificación por correo.
```

**Eso no es una avería del sistema**: es la parte que XTB no deja automatizar.
Se arregla en tres pasos, sin terminal:

1. **Abre tu correo.** XTB ya te envió un código de 6 dígitos — lo mandó en el
   mismo intento que falló, así que ya está en tu bandeja.
2. **Ve a la pestaña *Actions* del repositorio → *Renovar sesión XTB* → botón
   *Run workflow*.** Pega el código en el campo y dale a *Run workflow*.
3. **Espera a que salga en verde** (unos dos minutos). La sesión queda renovada
   por otras 8 horas y los demás runs la encontrarán solos.

**¿Y si el código ya no vale?** Lanza el mismo workflow **con el campo vacío**:
eso pide uno nuevo y te llega otro correo. Luego repite el paso 2 con ese.

> **Casi nunca hace falta.** Medido el 2026-09-29: el TGT dura 8 h y se refresca
> con el último login del día, así que caduca de madrugada **todas las noches**
> —aquel día, a las 05:11 UTC—. El run de compras de las 12:46 hizo login en
> frío y entró **sin pedir ningún código**, porque la cookie de dispositivo de
> confianza sigue valiendo. El sistema es autónomo día a día.
>
> Por eso el Vigilante **no** denuncia una sesión caducada: lo informa en su log
> y sigue. Lo que sí denuncia, y es el único síntoma que prueba que la cookie
> dejó de valer, es que algún componente haya muerto pidiendo código
> (`XTB_REQUIERE_CODIGO`). Ahí sí hacen falta los tres pasos de arriba.
También mantiene viva la caché donde vive la sesión — GitHub borra las cachés
que nadie usa en 7 días, y el Vigilante es el único que corre también los fines
de semana.

> Esa regla de los 7 días **no era la que mandaba**. El 2026-09-28 el
> repositorio estaba al 99 % de sus 10 GB de caché, con 74 cachés de precios de
> 135 MB, porque su clave llevaba el id del run y por tanto guardaba una nueva
> en cada ejecución. Al pasar de 10 GB, GitHub desaloja por orden de último uso
> — y lo que desalojaba era la sesión de XTB, que pesa 359 bytes. Ahora la clave
> de precios lleva la fecha (una al día, no ~17) y los jobs que solo leen la
> caché no la guardan.

## 🆘 Emergencia: cerrar las posiciones a mano desde xStation 5

Si el sistema falla con posiciones abiertas —el vigilante no late y no se
levanta solo, la página está en rojo y no sabes por qué—, **las posiciones no
tienen stop en XTB** (no lo acepta en acciones al contado). Ciérralas
tú:

1. **Entra en xStation 5** (web `xstation5.xtb.com` o la app móvil de XTB) y, en
   el selector de cuenta de arriba, elige la **cuenta DEMO** del sistema, no la
   real.
2. **Abre las posiciones abiertas** (panel inferior *Posiciones abiertas* en la
   web; *Cartera* en el móvil) y **cierra cada una entera**: botón **×** /
   *Cerrar posición* de la fila → confirma. Comprueba al final que la lista
   queda vacía.
3. **Deja constancia**: lanza *Actions → Auditar y diagnosticar XTB* sin marcar
   ninguna casilla. Debe decir `POSICIONES ABIERTAS EN XTB: 0`. La siguiente
   reconciliación saldrá **en rojo** con «una VENTA en XTB que el sistema no
   tiene registrada»: es a propósito —un cierre a mano nunca pasa
   desapercibido— y se apaga cuando la venta se anota en `bitacora_broker.csv`.

## 🔁 Si el vigilante se cae: se levanta solo y la página lo pinta en rojo

No hay emails. Si el vigilante de precios deja de latir con posiciones abiertas
y el mercado abierto, el sistema lo relanza por su cuenta, en tres capas de la
más rápida a la más lenta:

| Qué falla | Quién lo levanta | Cuánto tarda |
|---|---|---|
| El proceso muere o se cuelga (excepción del cliente de XTB, WebSocket que no vuelve) | el **supervisor**, en el mismo job (`scripts/supervisor_vigilante.py`): lo reinicia si termina mal o si su latido local lleva 6 min quieto | segundos |
| El runner entero muere (y el supervisor con él) | el **guardián**, un job paralelo en otra máquina (`scripts/guardian_vigilante.py`): si el latido publicado lleva más de 11 min parado, lanza un vigilante de **rescate** en su propio grupo de concurrencia | 11 min + lo que tarde el nuevo runner en arrancar (instalar Chromium ha llegado a 14 min) |
| Fallan los dos | la **escalera del pre-apertura**: cada peldaño llama al vigilante, que arranca si el latido está muerto o si hay posiciones en XTB | lo que tarde el siguiente peldaño (≈30-60 min en sesión) |

El umbral del guardián está a propósito por encima de los 10 minutos que tarda
un vigilante vivo pero sin poder publicar en retirarse solo: así nunca hay dos
vigilando la misma posición.

**En la página**: un latido de más de 10 minutos con posiciones que vigilar es
**rojo**, y un corte en el historial del día también, aunque ya se haya
recuperado. Los reinicios del supervisor salen en **ámbar** (no dejaron hueco,
pero el proceso se cayó), y el job termina en rojo para que conste.

## 🚨 Antes de pasar a dinero real

**Este sistema opera una cuenta DEMO y no está preparado para otra cosa.** Lo
que sigue no es una lista de buenas prácticas: son los pasos que faltan, y
ninguno de ellos debe poder darse por accidente.

El candado está deliberadamente repartido en **dos constantes versionadas** de
`centinela/config.py`:

```python
TIPO_CUENTA_BROKER = "demo"          # interruptor
CUENTA_DEMO_HUELLA = "faa853bb…"     # qué cuenta concreta (scrypt con sal)
```

Las dos viven en el código y no en los secrets, así que cambiarlas exige un
commit —con su diff y su historia— y no editar un campo en una página web.
Cambiar solo una no sirve de nada: el ejecutor comprueba las dos en cada
conexión y se para si no cuadran.

El número de la cuenta **no está en el repositorio**: solo su huella (scrypt
con sal). El número real llega por el secret `XTB_CUENTA`, y el candado exige
que la sesión conectada tenga esa huella. Para la huella de otra cuenta:

```
python -c "from centinela import config; print(config.huella_cuenta(NUMERO))"
```

### Los pasos, en orden

1. **Reactivar el segundo factor, y que sea TOTP.** Hoy la cuenta va sin 2FA
   para que el ejecutor pueda entrar solo. Con dinero delante eso no es
   aceptable. XTB no ofrecía TOTP cuando se montó esto (solo SMS, push y
   correo); si lo ofrece, se activa, se guarda el secreto en
   `centinela-xtb-totp` y el cliente lo usa solo. Si sigue sin ofrecerlo,
   **hay que replantear la automatización entera**, no seguir sin 2FA.

2. **Revisar dónde están las credenciales y quién las ve.** Están como secrets
   del repositorio, legibles por cualquier workflow y por quien tenga acceso de
   escritura. Para una demo vacía es asumible; para dinero real hay que decidir
   a conciencia si ese es el sitio.

3. **Apuntar el candado a la cuenta real concreta**, cambiando las dos
   constantes en el mismo commit y revisándolo como se revisa un cambio que
   mueve dinero.

4. **Y antes de nada: leer la auditoría.** Está en
   [`reportes/auditoria_fiabilidad.md`](reportes/auditoria_fiabilidad.md). El
   85 % del P&L viene de la cadena del silicio, el histórico son dos meses de
   mercado alcista, y el stop que la Cartera A lleva en el simulador **no
   existe en el broker** (XTB lo ignora en acciones al contado): lo vigila el
   ejecutor una vez al día, lo que medido lleva la cartera de +12,07 % con
   −10,44 % de drawdown a +9,32 % con −12,50 %.

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
  cuenta.py           contabilidad de la cuenta simulada (dinero, no % sueltos)
  riesgo.py           riesgo por operación y agregado, en $ y en %
  ordenes.py          órdenes del día, idempotencia y bitácora del broker
  broker_xtb.py       capa de aislamiento frente a XTB + candado demo
scripts/              entrenar_inicial, escaneo_preapertura, escaneo_postcierre,
                      reentrenar_mensual, generar_reporte, vigilante,
                      generar_ordenes, ejecutor_xtb, auditar_fiabilidad,
                      generar_dashboard, commit_y_push.sh,
                      verificar_persistencia.sh
mac/                  agentes launchd del ejecutor + despertares con pmset
.github/workflows/    preapertura.yml, postcierre.yml, vigilante.yml,
                      reentrenamiento.yml
tests/                pruebas (pytest)
```

### Cómo se garantiza que nada falla en silencio

Un workflow verde que no escribe nada es **peor** que uno rojo: disimula el
fallo. Cuatro capas independientes lo impiden:

1. **Vocabulario cerrado** ([`resultados.py`](centinela/resultados.py)): cada
   escaneo declara su desenlace. Solo cuatro motivos permiten terminar sin
   commit; cualquier otro es un fallo. Un motivo nuevo no hereda el permiso de
   callarse: añadirlo obliga a decidirlo a mano.
2. **Commit verificado** ([`commit_y_push.sh`](scripts/commit_y_push.sh)): si el
   escaneo dijo `procesado`, tiene que haber cambios, commit y push; luego relee
   el remoto y comprueba autor y marca del run.
3. **Verificación independiente** ([`verificar_persistencia.sh`](scripts/verificar_persistencia.sh)):
   un job aparte clona el repo de nuevo desde GitHub y comprueba contra el remoto
   de verdad que la cabeza de `main` la escribió el bot en ese run.
4. **Vigilante** ([`vigilante.py`](scripts/vigilante.py)): a diario, comprueba que
   ninguna sesión exigible se haya quedado sin sus **dos** escaneos. Es la única
   capa que detecta lo que ningún run puede detectar por sí mismo: que falte un
   run entero. Si encuentra algo, un job aparte lo manda por Telegram.

Y una quinta capa que no vigila el sistema sino **las cifras que publica**:
[`auditar_fiabilidad.py`](scripts/auditar_fiabilidad.py) vuelve a descargar los
precios y recalcula el rendimiento desde cero, buscando look-ahead y
concentración. Fue lo que encontró que las salidas por objetivo se estaban
ejecutando en el máximo exacto de la sesión.

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

Abre **[la página de Operativa](https://sebas1331.github.io/centinela-sp500/operativa.html)**
y mira el semáforo. Si está verde, no hay nada que hacer.

Si prefieres no depender de la página (por ejemplo, porque sospechas justo de
ella), el método de siempre sigue valiendo: abre el repo en el navegador o la
app de GitHub y mira **la fecha del último commit** en la portada.

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
pip install -r requirements.txt -r requirements-dev.txt   # dev = pytest + PyYAML

python scripts/entrenar_inicial.py           # entrenamiento + backtest (pesado)
python scripts/escaneo_preapertura.py --forzar --fecha 2026-07-17   # prueba
python scripts/escaneo_postcierre.py  --forzar --fecha 2026-07-17
python scripts/generar_reporte.py semanal
pytest -q                                    # tests
```

## 🤖 Ejecución automática en XTB (cuenta demo)

Desde el 25 de septiembre de 2026 el sistema no solo decide: **ejecuta**. La
Cartera A se opera en una cuenta **demo** de XTB; la B sigue solo simulada, y la
comparación entre las dos sigue siendo el experimento.

**Decidir y ejecutar están separados.** Los escaneos escriben
`ordenes/pendientes.json` y no saben que existe un broker. El ejecutor lo lee,
opera y anota en `bitacora_broker.csv` lo que pasó de verdad. Si XTB se cae, el
sistema sigue decidiendo y simulando igual.

### Por qué el ejecutor corre en un Mac y no en GitHub Actions

**XTB no da credenciales separadas para la demo**: el mismo email y la misma
contraseña abren también la cuenta real. Ponerlas en los secrets de un
repositorio remoto era un riesgo que no compensaba, así que viven en el Llavero
de macOS y no salen del ordenador. Además, el login de xStation5 pasa por un
WAF y las IPs de datacenter de GitHub son justo lo que ese WAF frena.

### El vigilante de precios

XTB no acepta stop loss ni take profit en acciones al contado, así que los
niveles los ejecuta el sistema. Hasta el 2026-09-29 los miraba el ejecutor **una
vez al día**, a las 15:45 ET, y eso está medido y es malo: la Cartera A pasaba de
+12,07 % con drawdown −10,44 % a **+9,32 % con −12,50 %**, peor en las dos
dimensiones que no tener stop, porque 7 de los 21 stops se dispararon por un
precio que el día cerró por encima.

Ahora hay un proceso mirando el **bid** tick a tick durante toda la sesión, por
el WebSocket de XTB, que dispara en el momento del cruce. Sus salidas llevan tipo
propio (`stop_intradia`, `objetivo_intradia`) para poder comparar unas con otras
y saber si de verdad acerca la ejecución al simulador.

| | |
|---|---|
| **Lo arranca** | el workflow de compras al terminar, no un cron: el scheduler de Actions se ha retrasado horas y un vigilante que llega a mediodía se perdió media sesión. Hay una escalera de crons de respaldo que solo arranca si no hay ninguno vivo. |
| **Dura** | toda la sesión. Un trabajo de GitHub muere a las 6 h y la sesión son 6 h 30, así que a las 5 h 45 llama a su sucesor y **no se va hasta verlo latir**: mientras espera sigue mirando precios. |
| **Si el WebSocket cae** | el cliente reconecta con backoff; mientras tanto se pregunta por petición cada 20 s y al volver se resuscribe todo. |
| **Cuesta** | ~6,5 h de runner por sesión. Este repositorio es **público**, y ahí los runners estándar son gratis y sin límite. En uno privado serían ~140 h al mes y habría que repensarlo. |

**También vende por tiempo** (desde el 2026-10-02). Las salidas del día 10 las
hace este proceso **15 minutos antes del cierre**. El workflow de ventas por
cron no llegó a su ventana ni una vez en la semana del 28/09 —los crons llegan
con 4 a 7 h de retraso— y queda como **respaldo**: mismo identificador de
orden, así que si el vigilante ya vendió, el respaldo lo ve y no repite. Si
ninguno llega, el plan B cierra en la apertura siguiente (`tiempo_diferido`).

**Cómo no se vende dos veces.** Al final de la sesión hay dos procesos que pueden
querer cerrar la misma posición: este y el respaldo de ventas por tiempo. Corren
en máquinas que no se ven. El candado no es un fichero —entre que uno lo escribe
y el otro lo lee caben segundos y un `push`— sino **XTB**: antes de vender, los
dos releen la posición, y el que llega segundo se la encuentra cerrada y se calla.

**Lo que anuncia es lo que hay.** Relee las posiciones de XTB cada 3 minutos y
tras cada venta, y lee la cuenta en cada latido: la página enseña esa foto (como
mucho de hace dos minutos) en vez de la del último commit. El latido guarda
además su **historial** (~240 latidos), así que se puede comprobar después que
la sesión estuvo cubierta sin cortes; un corte de más de 10 minutos pinta ámbar.

**Una venta no se pierde al publicar.** Todo lo que hace el broker se anota
primero en un diario local (`centinela/diario.py`) y se publica **aplicándolo
sobre el origin más nuevo**, nunca rebasando: no puede chocar. Y la
reconciliación cuadra las acciones de XTB contra `bitacora_broker.csv`, así que
una venta que existiera en XTB y no en el registro saldría en rojo.

**Confirmar una venta.** Una compra se confirma porque la posición CRECE; una
venta, porque BAJA o desaparece (hasta el 02/10 solo se miraba lo primero, y la
venta de CTVA se anotó «rechazada» estando ejecutada). Si XTB no devuelve el
precio, se toma el de apertura de la posición (compras) o se deduce del cambio
de saldo (ventas); la columna `precio_fuente` dice de dónde salió.

**El latido.** Cada 2 minutos publica una señal de vida en la rama `latido`, que
tiene siempre **un solo commit** reescrito por *force-push*: así `main` no se
llena de 195 commits al día y la página puede leerlo en vivo. La página lo pide a
`raw.githubusercontent`, que cachea 5 minutos (medido), así que lo que ves puede
tener hasta 7 minutos —nunca da falso rojo, pero un vigilante muerto tarda 10-15
minutos en verse ahí—. Quien lo comprueba **sin margen** es el Vigilante general,
que lee la rama directamente y es el que relanza.

**Si no está activo**, se relanza solo (ver «🔁 Si el vigilante se cae»), la
página se pone en rojo y el respaldo de ventas sigue evaluando objetivo y stop
cuando llega. Si hay que cerrar a mano, ver «🆘 Emergencia».

**Probado contra XTB con el mercado abierto** (2026-09-29): comprando 1 acción y
poniéndole un nivel pegado al precio, disparó por **objetivo** (bid 12,29 cruzó
12,27) y por **stop** (bid 12,30 cruzó 12,32), las dos veces en **menos de dos
segundos** entre ver el cruce y tener la orden en el broker.

> **Los niveles no se mandan a XTB.** No los acepta en acciones al contado: el
> 28/09 los ignoraba en silencio y el 02/10 pasó a **rechazar la orden entera**
> —CTVA × 134 con niveles, rechazada; las mismas 134 sin niveles, ejecutada—.
> Se siguen registrando en la bitácora, porque son la decisión del simulador,
> pero quien los vigila es el vigilante de precios.

> **Ojo con los CFD.** XTB ofrece muchos símbolos por partida doble: la acción al
> contado y su CFD, con el mismo nombre. El cliente no oficial resolvía el
> símbolo cogiendo «el primero que coincida», y el orden de esa lista cambia
> entre sesiones, así que unas veces mandaba la acción y otras el CFD — que el
> servicio de contado rechaza. Siete de ocho compras se perdieron por eso el
> 2026-09-29. Ahora `parche_instrumento.py` elige la de contado y, si solo queda
> el CFD, **falla en vez de operarlo**.

### Cuatro momentos al día

| Momento | Qué hace | Cuándo (ET) |
|---|---|---|
| `compras` | Manda las compras **después de abrir** (XTB descarta las encoladas con el mercado cerrado) y arranca el vigilante si hay posiciones | 0–60 min tras la apertura |
| `apertura` | **No decide nada**: comprueba que se ejecutó lo que se mandó | 30–90 min tras la apertura |
| `ventas` | Las hace el **vigilante de precios** 15 min antes del cierre; el cron es respaldo y dice «ok: N vendidas», «ok: nada que vender» o «fuera de ventana» | 30–5 min antes del cierre |
| `reconcilia` | Compara XTB con el simulador y rompe en rojo si difieren | ≥30 min tras el cierre |

El de la apertura nació del 2026-09-29: hasta entonces nadie miraba el resultado
de la apertura hasta el post-cierre, así que una orden colgada, una rechazada o
una decisión que no llegó a orden no se veían hasta la tarde. Compara las tres
listas que tienen que cuadrar —decidido, enviado, y lo que XTB tiene— y corrige
en la bitácora el precio de ejecución, que al enviar todavía no se conocía: una
compra a mercado encolada antes de abrir se ejecuta al open, no al precio de la
víspera. Cualquier hueco entre las tres listas es rojo.

Lo de cerrar antes del cierre y no a la apertura siguiente se midió: cerrar al cierre del día 10 se desvía
**0,03 pp** del simulador; hacerlo a la apertura del día siguiente, 0,40 pp con
**3,14 pp de dispersión** — gap overnight que la estrategia no contempla.

Instalación y ajustes de energía: [`mac/README.md`](mac/README.md).

### El candado demo

`centinela/broker_xtb.py` comprueba en **cada** conexión que el endpoint es el
de demo y que el número de cuenta es el esperado. Si algo no cuadra, o no se
puede determinar, no manda nada y falla en rojo. Importa porque la librería, por
defecto, se conecta a la cuenta **real**.

### Lo que el cliente no oficial NO puede hacer

XTB cerró su API oficial en marzo de 2025 y lo único que queda es ingeniería
inversa de xStation5. Tres limitaciones, todas medidas:

| Limitación | Impacto medido |
|---|---|
| **XTB ignora el stop y el take profit** en acciones al contado | La Cartera A pasa de +12,07 % / −10,44 % a **+9,32 % / −12,50 %** con el stop vigilado 1 vez al día |
| No se puede cerrar una posición por id | Se vende el mismo volumen; en acciones al contado eso netea (comprobado) |
| Sin acciones fraccionadas por la API | Con $30.000 y 20 slots, 4 de 141 entradas no caben. Desde $50.000, ninguna |

La primera se comprobó con una orden real el 28/09/2026: XTB aceptó la compra
pasando `stop_loss` y `take_profit`, la ejecutó, y la posición apareció con los
dos a `None` sin ningún error. Los niveles los vigila ahora el ejecutor, que
solo puede vender una vez al día con el mercado abierto — de ahí la pérdida de
rentabilidad y el aumento de drawdown. Es una **divergencia conocida** entre lo
que se ejecuta y lo que se simula, y el panel la dice.

## 🔍 Auditoría de fiabilidad

```bash
python scripts/auditar_fiabilidad.py          # regenera reportes/auditoria_fiabilidad.md
python scripts/generar_ordenes.py preapertura # escribe ordenes/pendientes.json
python scripts/ejecutor_xtb.py compras --forzar --sin-git   # prueba del ejecutor
```
