# Qué se cambió de `xtb-api-python` 0.10.0, y por qué

Esto es una copia literal de [`xtb-api-python`](https://pypi.org/project/xtb-api-python/)
**0.10.0** (21/04/2026), MIT, © 2025-2026 Łukasz Lis. La licencia va al lado, en
[`LICENSE`](LICENSE), sin tocar.

## Por qué está copiada aquí y no instalada

El repositorio que el paquete declara —`github.com/liskeee/xtb-api-python`—
**devuelve 404** desde al menos el 2026-09-29: el usuario existe, el repositorio
ya no. El paquete sigue publicado en PyPI y sin retirar, pero:

- no hay dónde consultar incidencias ni a quién reportar nada;
- nada garantiza que siga publicado mañana;
- y este sistema **manda órdenes a un broker** con él.

Un sistema que opera dinero no puede depender de que un paquete siga estando. La
versión estaba fijada por la misma razón (una actualización automática de un
cliente no oficial es justo lo que no queremos); copiarla es el paso siguiente.

`centinela/__init__.py` mete `vendor/` al principio de `sys.path`, así que esta
copia gana incluso si alguien instala el paquete de PyPI.

---

## Parche 1 — Elegir la ACCIÓN y no su CFD

**Fichero:** `client.py` · `XTBClient._resolve_instrument_id`, más el ayudante
`_is_cash_instrument` y las constantes `CENTINELA_*`.

**Qué hacía el original:**

```python
for r in results:
    if r.symbol.upper() == symbol.upper():
        return r.instrument_id
if results:
    return results[0].instrument_id          # ← ni siquiera coincide
raise InstrumentNotFoundError(...)
```

**El problema.** XTB ofrece muchos símbolos por partida doble: la acción al
contado y su CFD, con el mismo nombre. Buscando `F.US` salen dos:

```
id=7813   clave=9_F.US_US_STC        Ford Motor Co
id=335    clave=4_F.US_US_STC CFD    CLOSE ONLY / Ford Motor Co CFD
```

El original devuelve **el primero que coincida**, y el orden de esa lista cambia
entre sesiones. Cuando salía primero el CFD se mandaba su id al servicio de
acciones al contado (`CashTradingNewOrderService`), que con toda la razón
contesta `Could not find instrument for id: 335`.

**Lo que costó:** el 2026-09-29, **siete de ocho compras** se perdieron por esto.
Parecía que el endpoint de trading de XTB estaba caído e intermitente; era una
moneda al aire. Se confirmó con INTC, que fallaba igual con su propio CFD (277).

**Lo que hace ahora:** entre las coincidencias exactas se queda con la de
contado y descarta los CFD. Si no hay ninguna de contado, **falla con un mensaje
claro** en vez de mandar el CFD: operar el instrumento equivocado es peor que no
operar. Y sin coincidencia exacta también falla, en vez de mandar una orden
sobre el primer instrumento que contenga esas letras.

Se distingue por la **clave** (`9_` contado, `4_` CFD), que es estructura. El
nombre también lo dice ("CFD", "CLOSE ONLY") pero es texto para humanos y puede
cambiar de un día para otro, así que solo se usa de reserva cuando no hay clave.

**Verificado en vivo** (2026-09-29, mercado abierto): con el parche, la compra
entró a la primera (orden 916134765) tras ocho intentos fallidos sin él.

**Tests:** `tests/test_instrumento.py`.

---

## Parche 2 — Leer el motivo del error en las cabeceras gRPC

**Fichero:** `grpc/client.py` · `GrpcClient._grpc_call`, más `_centinela_motivo`,
`CENTINELA_ULTIMO_ERROR` y `CENTINELA_CODIGOS`.

**Qué hacía el original:**

```python
resp = await client.post(endpoint, content=body_b64, headers=headers)
resp.raise_for_status()
if not resp.text:
    return b""                               # ← y el motivo se pierde
```

**El problema.** En gRPC-web **los errores llegan con HTTP 200** y el motivo en
las cabeceras; una respuesta de error "trailers-only" tiene exactamente eso:
cuerpo vacío y el porqué arriba. Como el original no las mira, **todo** error de
trading subía convertido en «respuesta vacía; resultado ambiguo», que no dice
nada.

**Lo que costó:** una tarde entera de diagnóstico a ciegas. Al leer las
cabeceras, el motivo apareció al primer intento:

```
HTTP         : 200 OK
grpc-status  : 3                                  (INVALID_ARGUMENT)
grpc-message : Could not find instrument for id: 335
```

**Lo que hace ahora:** cuando el cuerpo viene vacío, lee `grpc-status` y
`grpc-message`, los traduce a algo legible y los deja en
`CENTINELA_ULTIMO_ERROR`. `centinela/broker_xtb.py` los antepone al error de la
ejecución, así que el motivo sale en el log del ejecutor y en la página de
Operativa en vez de en un diagnóstico que haya que acordarse de lanzar.

Sigue devolviendo `b""`: el parche **no cambia el comportamiento**, solo deja de
tirar la información.

**Tests:** `tests/test_ambiguas.py`, sección 5.

---

## Parche 3 — Órdenes pendientes de venta y la lista de órdenes de contado

**Ficheros:** `grpc/proto.py` (bloque `CENTINELA (CAMBIOS.md, parche 3)`),
`grpc/client.py` (`_pending_call`, `new_limit_order`, `new_stop_order`,
`modify_limit_order`, `modify_stop_order`, `delete_orders_checked`,
`cash_orders_snapshot`), `grpc/types.py` (`GrpcPendingOrderResult`) y
`client.py` (`place_pending_order`, `modify_pending_order`,
`cancel_pending_orders`, `get_cash_orders`).

**De dónde sale.** La web de xStation 5 lleva embebidos los descriptores
protobuf de sus servicios (base64 dentro de los microfrontends `trading-web-cmp`
y `portfolio-web-cmp`, en `/mfe/apps/<nombre>-web-cmp/`). Decodificados dan el
esquema exacto, sin adivinar números de campo:

* `pl.xtb.ipax.pub.grpc.cashtradingneworder.v1.CashTradingNewOrderService`
  — el MISMO servicio de `NewMarketOrder`: `NewLimitOrder`, `NewStopOrder`,
  `ModifyLimitOrder`, `ModifyStopOrder`, `DeleteOrders`.
* `pl.xtb.ipax.pub.grpc.order.v1.OrderService/SubscribeOrderGroups` — la lista
  de órdenes que enseña la web. Stream: el primer mensaje es la foto (SNAPSHOT).

Los mensajes que construye el parche se validaron decodificándolos con esos
mismos descriptores. Sin `expirationDate` la orden no vence (el interruptor
"Vencimiento de la orden" apagado de la web).

**Dos fallos del original que esto destapó:**

1. `getAllOrders` (WebSocket, lo que usaba `get_orders()`) **no ve las órdenes
   de contado**: con una limitada y una stop aceptadas devolvió cero. La lista
   buena es `get_cash_orders()`, que además trae el ESTADO de cada orden.
2. `cancel_orders` daba por cancelada una orden con que su número volviera en la
   respuesta, y vuelve también en la rama de error (`ERROR_CODE_CANNOT_FIND_ORDER`).
   `delete_orders_checked` lee la rama `success`/`error` de cada orden.

**Lo medido en la demo** (2026-10-05, mercado abierto, F.US; runs 37338861588 y
37359224699 del workflow "Probar órdenes pendientes XTB"):

* XTB solo admite **una** orden pendiente de venta por acción. La segunda se
  ACEPTA (devuelve número) y a los pocos segundos ya no existe: no está en la
  lista, y modificarla o cancelarla devuelve "Order modification not allowed"
  (código 2029). Pasa en los dos órdenes (limitada→stop y stop→limitada).
  **Aceptada no es viva**: hay que releer la lista.
* Modificar el precio funciona y conserva el número. Cancelar funciona.
* Las órdenes pendientes aparecen también en la lista de POSICIONES del
  WebSocket, como fila `sell`. Todo el repo filtra `lado == "buy"`.

**Hallazgo de paso, sin aplicar todavía:** `build_new_market_order` del original
mete el stop loss y el take profit DENTRO del mensaje `Size` (campos 3/4), donde
el esquema no tiene nada; el servidor los recibe como campos desconocidos. Según
el esquema van en `NewMarketOrderRequest.stopLossValue = 6` /
`takeProfitValue = 7`. Eso explica que "XTB ignorara" los niveles en contado el
28/09 y los rechazara el 02/10. También existe `ModifyPosition` (stop y objetivo
sobre una posición abierta). No se ha probado: el sistema usa órdenes
pendientes.

**Tests:** `tests/test_proteccion.py` (el uso) y la prueba real del workflow.

---

## Lo que NO se tocó

**El parche del selector del segundo factor** (`centinela/parche_otp.py`) sigue
aplicándose en tiempo de ejecución, sobre esta copia. Es deliberado: el selector
del OTP depende del HTML de la página de login de XTB, que es lo más probable
que cambie de un día para otro, y tenerlo fuera del árbol copiado hace más
rápido corregirlo cuando pase. Los dos parches de aquí arriba son correcciones
de lógica, no de un selector frágil, y por eso viven en el código.

## Cómo actualizar esta copia

Si algún día reaparece el proyecto o sale una versión nueva:

1. Copiar el árbol nuevo sobre `vendor/xtb_api/`, conservando `LICENSE` y este
   fichero.
2. Volver a aplicar los tres parches de arriba (`_resolve_instrument_id`,
   `_grpc_call` y el bloque de órdenes pendientes) y anotar aquí qué cambió.
3. `pytest` entero en verde, y una compra real de una acción en la demo: los dos
   parches existen porque los tests no bastaban para verlos.
