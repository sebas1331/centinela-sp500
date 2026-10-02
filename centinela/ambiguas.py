"""Qué hacer cuando el broker no sabe decir si ejecutó la orden.

EL PROBLEMA, CON NÚMEROS
------------------------
El 2026-09-29 el endpoint de trading de XTB devolvió cuerpo vacío en 7 de 8
compras. El cliente lo traduce a `ambigua`, que es la traducción honesta: el
POST llegó, la respuesta vino vacía, y la orden **pudo o no** haberse colocado.

Tratar eso como "rechazada" y reintentar compraría dos veces. Tratarlo como
"ejecutada" apuntaría una compra que quizá no existe. Las dos opciones son
peores que la tercera, que es la que hace este módulo: **preguntar**.

CÓMO SE PREGUNTA
----------------
Una orden que entró aparece en XTB, pero no instantáneamente: el libro tarda un
momento en reflejarla. Así que se espera y se mira varias veces, no una:

  * ¿Hay posición del símbolo que no había antes? -> se ejecutó.
  * ¿Hay una orden en cola de ese símbolo? -> se colocó y espera al mercado.
  * ¿Ni una cosa ni la otra, después de mirar varias veces? -> no entró.

Solo en el tercer caso se reintenta, **con el mismo identificador**: el id es
determinista (`fecha|cartera|ticker|tipo`), así que un reintento no crea una
orden nueva a ojos del sistema, es la misma orden intentándolo otra vez. Eso es
lo que impide que dos intentos se cuenten como dos compras.

POR QUÉ NO BASTA CON MIRAR UNA VEZ
----------------------------------
Porque el fallo es asimétrico. Si se mira demasiado pronto y la orden todavía no
figura, se concluye "no entró" y se reintenta: dos compras. El coste de esperar
de más es un retraso de segundos; el de esperar de menos, una posición doble. Por
eso se mira varias veces con pausa, y solo un "no" repetido cuenta como no.
"""
from __future__ import annotations

import time

#: Cuántas veces se le pregunta a XTB antes de dar una orden por no ejecutada.
INTENTOS_DE_COMPROBACION = 4
#: Cuánto se espera entre preguntas. Cuatro por tres segundos son doce segundos
#: de margen, bastante más de lo que XTB tarda en reflejar una orden aceptada.
ESPERA_ENTRE_COMPROBACIONES = 3.0

#: Los tres desenlaces posibles. Vocabulario cerrado: añadir uno obliga a
#: decidir también cómo se registra y cómo se pinta.
EJECUTADA_TRAS_AMBIGUA = "ejecutada-tras-ambigua"
EN_COLA_TRAS_AMBIGUA = "en-cola-tras-ambigua"
NO_EJECUTADA = "no-ejecutada"


def resolver(broker, simbolo: str, acciones_antes: float,
             ordenes_antes: set, dormir=time.sleep) -> tuple[str, dict]:
    """¿Entró la orden? Devuelve (desenlace, detalle).

    `acciones_antes` y `ordenes_antes` son la foto de ANTES de mandar la orden.
    Sin esa foto no se puede distinguir una posición que acaba de abrirse de una
    que ya estaba: mirar solo "hay posición de X" daría un falso "sí" cada vez
    que se opera sobre algo que ya se tenía.
    """
    for intento in range(1, INTENTOS_DE_COMPROBACION + 1):
        dormir(ESPERA_ENTRE_COMPROBACIONES)

        try:
            posiciones = broker.posiciones()
            cola = broker.ordenes_pendientes()
        except Exception as exc:  # noqa: BLE001 — sin respuesta no se concluye
            # No poder preguntar NO es lo mismo que una respuesta negativa. Si
            # se tratara igual, un corte de red acabaría en una compra doble.
            if intento == INTENTOS_DE_COMPROBACION:
                return NO_EJECUTADA, {
                    "concluyente": False,
                    "detalle": (f"No se pudo comprobar si la orden entró "
                                f"({exc!r}). NO se reintenta: preguntar falló, "
                                f"que no es lo mismo que un no."),
                }
            continue

        ahora_acciones = sum(
            float(p["acciones"]) for p in posiciones
            if p.get("lado") == "buy" and p.get("ticker") == simbolo)
        if ahora_acciones > acciones_antes:
            return EJECUTADA_TRAS_AMBIGUA, {
                "concluyente": True,
                "acciones": ahora_acciones - acciones_antes,
                "detalle": (f"La orden ambigua SÍ entró: {simbolo} pasó de "
                            f"{acciones_antes:g} a {ahora_acciones:g} acciones "
                            f"(visto en el intento {intento})."),
            }

        nuevas = {o.get("orden") for o in cola
                  if o.get("ticker") == simbolo} - ordenes_antes
        if nuevas:
            return EN_COLA_TRAS_AMBIGUA, {
                "concluyente": True,
                "ordenes": sorted(str(n) for n in nuevas),
                "detalle": (f"La orden ambigua se colocó y espera al mercado: "
                            f"orden {', '.join(str(n) for n in sorted(nuevas, key=str))} "
                            f"de {simbolo} (intento {intento})."),
            }

    return NO_EJECUTADA, {
        "concluyente": True,
        "detalle": (f"La orden ambigua NO entró: {simbolo} sigue con "
                    f"{acciones_antes:g} acciones y sin órdenes nuevas en cola "
                    f"después de {INTENTOS_DE_COMPROBACION} comprobaciones."),
    }


def foto(broker, simbolo: str) -> tuple[float | None, set | None]:
    """Lo que hay ANTES de mandar la orden, para poder comparar después.

    Devuelve (None, None) si no se puede leer. No revienta a propósito: una
    orden que hay que mandar se manda aunque no se haya podido mirar antes. Lo
    que NO se puede hacer sin esa foto es reintentar, y de eso se encarga quien
    llama.
    """
    try:
        acciones = sum(
            float(p["acciones"]) for p in broker.posiciones()
            if p.get("lado") == "buy" and p.get("ticker") == simbolo)
        cola = {o.get("orden") for o in broker.ordenes_pendientes()
                if o.get("ticker") == simbolo}
        return acciones, cola
    except Exception as exc:  # noqa: BLE001 — sin foto se sigue, sin reintentar
        print(f"[ambigua] no se pudo mirar {simbolo} antes de mandar ({exc!r}); "
              f"se manda igual, pero sin red para reintentar.", flush=True)
        return None, None


#: Cuántas veces se vuelve a intentar una orden que se CONFIRMÓ no ejecutada.
#: Dos: con el endpoint fallando 7 de cada 8 veces, un solo intento deja la
#: sesión a suerte; más de tres alarga tanto la ventana que la compra dejaría
#: de ser "al open", que es la premisa de la estrategia.
REINTENTOS = 2


def enviar_resolviendo(broker, simbolo: str, tipo: str, mandar,
                       dormir=time.sleep):
    """Manda una orden y, si el broker no sabe qué pasó, lo averigua.

    `mandar` es una función sin argumentos que devuelve una `Ejecucion`: así
    esta lógica vale igual para una compra que para una venta, y no necesita
    saber cuál de las dos está haciendo.

    Devuelve la `Ejecucion` final. Cuando una ambigua resulta haber entrado, se
    devuelve como ejecutada —porque lo está— pero con el estado anotado en el
    diario de fiabilidad, que es donde se mide lo que cuesta este broker.
    """
    from . import fiabilidad

    ultimo = None
    for intento in range(1, REINTENTOS + 2):
        antes_acciones, antes_cola = foto(broker, simbolo)
        e = ultimo = mandar()

        if e.ok:
            # "Aceptada" no es "ejecutada". XTB devuelve QUEUED con el mercado
            # cerrado y luego descarta la orden en silencio, así que se le
            # pregunta por el estado DEFINITIVO antes de dar nada por hecho.
            # Sin esto, el 2026-09-30 se anotaron dos compras "en_cola" que en
            # xStation 5 figuran como rechazadas.
            if antes_acciones is None:
                fiabilidad.anotar(fiabilidad.CONFIRMADA, simbolo, tipo,
                                  f"{e.estado}; no se pudo confirmar en XTB")
                return e
            real, porque = confirmar(broker, simbolo, antes_acciones,
                                     dormir=dormir)
            print(f"[confirmación] {simbolo} {tipo}: {real} — {porque}",
                  flush=True)
            e.estado = real
            if real == "rechazada":
                e.error = porque
                fiabilidad.anotar(fiabilidad.RECHAZADA, simbolo, tipo, porque)
            elif real == "ejecutada":
                fiabilidad.anotar(fiabilidad.EJECUTADA, simbolo, tipo, porque)
            else:
                fiabilidad.anotar(fiabilidad.EN_COLA, simbolo, tipo, porque)
            return e

        if e.estado != "ambigua":
            fiabilidad.anotar(fiabilidad.FALLIDA, simbolo, tipo,
                              str(e.error or "sin motivo"))
            return e

        if antes_acciones is None:
            # Sin la foto de antes no hay forma de saber si esta orden abrió
            # algo o si ya estaba: reintentar sería jugársela a comprar dos
            # veces, así que se para y se dice.
            fiabilidad.anotar(
                fiabilidad.AMBIGUA_SIN_RESOLVER, simbolo, tipo,
                "No se pudo leer la posición antes de mandar, así que no hay "
                "con qué comparar. No se reintenta.")
            return e

        print(f"[ambigua] {simbolo} {tipo}: respuesta vacía en el intento "
              f"{intento}; preguntando a XTB si entró...", flush=True)
        desenlace, detalle = resolver(broker, simbolo, antes_acciones,
                                      antes_cola, dormir=dormir)
        print(f"[ambigua] {detalle['detalle']}", flush=True)

        if desenlace in (EJECUTADA_TRAS_AMBIGUA, EN_COLA_TRAS_AMBIGUA):
            fiabilidad.anotar(fiabilidad.AMBIGUA_RESUELTA_SI, simbolo, tipo,
                              detalle["detalle"])
            # La orden existe: se devuelve como tal. El estado lo dice para que
            # quien lo lea sepa que hubo que averiguarlo.
            e.estado = ("ejecutada" if desenlace == EJECUTADA_TRAS_AMBIGUA
                        else "en_cola")
            e.error = None
            return e

        if not detalle.get("concluyente"):
            # No se pudo comprobar. Reintentar a ciegas podría comprar dos
            # veces, así que se para y se dice por qué.
            fiabilidad.anotar(fiabilidad.AMBIGUA_SIN_RESOLVER, simbolo, tipo,
                              detalle["detalle"])
            return e

        fiabilidad.anotar(fiabilidad.AMBIGUA_RESUELTA_NO, simbolo, tipo,
                          detalle["detalle"])
        if intento <= REINTENTOS:
            print(f"[ambigua] no entró: se reintenta ({intento}/{REINTENTOS}).",
                  flush=True)

    return ultimo


#: Cuántas veces se le pregunta a XTB por el estado DEFINITIVO de una orden que
#: dijo aceptar, y cada cuánto.
CONFIRMACIONES = 5
ESPERA_CONFIRMACION = 3.0

#: Los estados en que una orden ya no va a cambiar. "en_cola" NO está aquí: es
#: justo el que engañó el 2026-09-30.
DEFINITIVOS = ("ejecutada", "rechazada")


def confirmar(broker, simbolo: str, acciones_antes: float,
              dormir=time.sleep) -> tuple[str, str]:
    """El estado DEFINITIVO de una orden recién mandada, según XTB.

    POR QUÉ ESTO EXISTE (fallo del 2026-09-30)
    -------------------------------------------
    "Aceptada por el servidor" no es "ejecutada". XTB acepta una orden de
    mercado con el mercado cerrado, devuelve `QUEUED`, y luego la descarta en
    silencio. El 30/09 el sistema mandó MRNA y FICO 43 minutos antes de abrir,
    anotó "en_cola" para las dos, no volvió a preguntar, y la página dijo "todo
    en orden" con cero compras. En xStation 5 las dos figuran como RECHAZADO.

    Comprobado a propósito el 01/10 con el mercado cerrado: XTB aceptó una
    compra de 1 acción (orden 916785162) y tres minutos después no había ni
    posición ni orden.

    Así que después de mandar se pregunta, y se pregunta VARIAS veces: una
    orden recién aceptada tarda un momento en aparecer como posición, y
    concluir "rechazada" demasiado pronto sería tan falso como lo contrario.

    Devuelve ('ejecutada'|'rechazada'|'en_cola', explicación). `en_cola` solo
    sale cuando XTB sigue diciendo que la orden existe y espera: eso es un
    estado real, no una suposición.
    """
    ultima = "en_cola", "sin respuesta concluyente de XTB"
    for intento in range(1, CONFIRMACIONES + 1):
        dormir(ESPERA_CONFIRMACION)
        try:
            posiciones = broker.posiciones()
            cola = broker.ordenes_pendientes()
        except Exception as exc:  # noqa: BLE001
            ultima = "en_cola", f"no se pudo preguntar a XTB ({exc!r})"
            continue

        ahora = sum(float(p["acciones"]) for p in posiciones
                    if p.get("lado") == "buy" and p.get("ticker") == simbolo)
        if ahora > acciones_antes:
            return "ejecutada", (
                f"XTB tiene la posición: {simbolo} pasó de {acciones_antes:g} a "
                f"{ahora:g} acciones (intento {intento})")

        if any(o.get("ticker") == simbolo for o in cola):
            # Sigue viva y esperando. Puede acabar ejecutándose o no, pero
            # ahora mismo existe: no se puede llamar rechazada.
            ultima = "en_cola", (f"la orden sigue en cola en XTB "
                                 f"(intento {intento})")
            continue


        # NO se concluye "rechazada" al primer vistazo. Una orden de mercado
        # recién enviada tarda unos segundos en aparecer como posición, y
        # declararla muerta a los tres segundos es el mismo error que darla por
        # ejecutada sin mirar, solo que al revés. Pasó el 2026-10-02: CTVA se
        # dio por rechazada seis segundos después de mandarla, "tras 1
        # comprobación".
        ultima = "rechazada", (
            f"XTB no tiene ni posición ni orden de {simbolo} tras "
            f"{intento} de {CONFIRMACIONES} comprobaciones")
        if intento < CONFIRMACIONES:
            continue
        return ultima
    return ultima
