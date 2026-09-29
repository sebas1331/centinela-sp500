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
2. Volver a aplicar los dos parches de arriba (`_resolve_instrument_id` y
   `_grpc_call`) y anotar aquí qué cambió.
3. `pytest` entero en verde, y una compra real de una acción en la demo: los dos
   parches existen porque los tests no bastaban para verlos.
