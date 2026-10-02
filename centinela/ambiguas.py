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
             ordenes_antes: set, dormir=time.sleep,
             lado: str = "compra") -> tuple[str, dict]:
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
        # Una compra que entró hace CRECER la posición; una venta la hace BAJAR
        # o desaparecer. Mirar solo el primer caso dio por rechazada la venta
        # de CTVA del 2026-10-02, que XTB sí ejecutó.
        movidas = movimiento(lado, acciones_antes, ahora_acciones)
        if movidas > 0:
            return EJECUTADA_TRAS_AMBIGUA, {
                "concluyente": True,
                "acciones": movidas,
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


def movimiento(lado: str, antes: float, ahora: float) -> float:
    """Cuántas acciones movió la orden, en el sentido que le toca.

    Positivo si la orden hizo lo que se le pidió: una compra que hizo crecer la
    posición, o una venta que la hizo bajar. Cero o negativo, si no.
    """
    if lado == "compra":
        return ahora - antes
    if lado == "venta":
        return antes - ahora
    raise ValueError(f"Lado desconocido: {lado!r} (compra | venta).")


def _saldo(broker) -> float | None:
    """El saldo de la cuenta, o None si este broker no lo da o falla al leerlo.

    No revienta: el saldo solo se usa para DEDUCIR un precio que XTB no dio, y
    no poder deducirlo deja un hueco honesto en la bitácora, no una orden mal
    juzgada. Lo que sí hace es decirlo.
    """
    leer = getattr(broker, "saldo", None)
    if leer is None:
        return None
    try:
        return float(leer()["saldo"])
    except Exception as exc:  # noqa: BLE001 — se dice y se deja el hueco
        print(f"[precio] no se pudo leer el saldo ({exc!r}); el precio no se "
              f"podrá deducir de él.", flush=True)
        return None


#: Cuánto puede alejarse un precio deducido del de referencia (el bid o el
#: nivel que se vio al decidir) para darlo por bueno. Un 5 % separa sin duda
#: los dos modelos de saldo posibles, que difieren en el precio de entrada
#: entero, y deja margen de sobra para un deslizamiento real.
TOLERANCIA_PRECIO = 0.05


def deducir_precio_venta(saldo_antes: float, saldo_despues: float,
                         acciones: float, entrada: float | None,
                         referencia: float | None) -> tuple[float | None, str]:
    """El precio de una venta, deducido del cambio de saldo. (precio, método).

    XTB no siempre devuelve el precio al ejecutar, y su API no tiene historial
    de operaciones. Lo que sí da es el saldo, y de cómo cambia se puede sacar el
    precio. Pero hay dos formas en que un broker puede contabilizar una acción
    al contado, y con las lecturas que hay (2026-10-02) no se puede saber cuál
    usa XTB — las dos dieron 11,97 para CTVA:

      caja  el saldo BAJA al comprar y SUBE al vender por el importe entero:
            precio = Δsaldo / acciones
      pnl   el saldo solo se mueve al cerrar, por el resultado:
            precio = entrada + Δsaldo / acciones

    Así que se calculan los dos y se queda el que cuadra con la referencia (el
    bid que se vio justo antes de mandar). Los dos candidatos se separan por el
    precio de entrada entero, así que confundirlos no es posible si hay
    referencia. Sin referencia, no se adivina: se deja vacío.
    """
    if not acciones or acciones <= 0:
        return None, "sin acciones"
    delta = saldo_despues - saldo_antes
    candidatos = [(round(delta / acciones, 4), "saldo-caja")]
    if entrada:
        candidatos.append((round(entrada + delta / acciones, 4), "saldo-pnl"))
    candidatos = [(p, m) for p, m in candidatos if p > 0]
    if not candidatos:
        return None, f"ningún modelo da un precio positivo (Δsaldo {delta:+.2f})"
    if not referencia:
        return None, "sin precio de referencia para elegir el modelo de saldo"
    p, m = min(candidatos, key=lambda c: abs(c[0] - referencia))
    if abs(p / referencia - 1.0) > TOLERANCIA_PRECIO:
        return None, (f"el precio deducido ({p}, {m}) se aleja más de un "
                      f"{TOLERANCIA_PRECIO:.0%} de la referencia {referencia}")
    return p, m


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
                       dormir=time.sleep, id_orden: str = "",
                       referencia: float | None = None):
    """Manda una orden y, si el broker no sabe qué pasó, lo averigua.

    `mandar` es una función sin argumentos que devuelve una `Ejecucion`: así
    esta lógica vale igual para una compra que para una venta, y no necesita
    saber cuál de las dos está haciendo.

    Devuelve la `Ejecucion` final. Cuando una ambigua resulta haber entrado, se
    devuelve como ejecutada —porque lo está— pero con el estado anotado en el
    diario de fiabilidad, que es donde se mide lo que cuesta este broker.
    """
    from . import fiabilidad, ordenes as ords

    lado = ords.lado_de(tipo)
    ultimo = None
    for intento in range(1, REINTENTOS + 2):
        antes_acciones, antes_cola = foto(broker, simbolo)
        entrada_antes = _precio_entrada(broker, simbolo) if lado == "venta" else None
        saldo_antes = _saldo(broker)
        e = ultimo = mandar()

        if e.ok:
            # "Aceptada" no es "ejecutada". XTB devuelve QUEUED con el mercado
            # cerrado y luego descarta la orden en silencio, así que se le
            # pregunta por el estado DEFINITIVO antes de dar nada por hecho.
            # Sin esto, el 2026-09-30 se anotaron dos compras "en_cola" que en
            # xStation 5 figuran como rechazadas.
            if antes_acciones is None:
                fiabilidad.anotar(fiabilidad.CONFIRMADA, simbolo, tipo,
                                  f"{e.estado}; no se pudo confirmar en XTB", id_orden=id_orden)
                return e
            real, porque, movidas = confirmar(
                broker, simbolo, antes_acciones, dormir=dormir, lado=lado,
                acciones=getattr(e, "acciones", None))
            print(f"[confirmación] {simbolo} {tipo}: {real} — {porque}",
                  flush=True)
            e.estado = real
            if real == "ejecutada":
                e.acciones_hechas = int(round(movidas))
                completar_precio(broker, simbolo, lado, e, saldo_antes,
                                 entrada_antes, referencia, dormir=dormir)
                if e.acciones_hechas < int(getattr(e, "acciones", 0) or 0):
                    e.error = (f"PARCIAL: XTB movió {e.acciones_hechas} de "
                               f"{e.acciones} acciones")
            if real == "rechazada":
                e.error = porque
                fiabilidad.anotar(fiabilidad.RECHAZADA, simbolo, tipo, porque,
                                  id_orden=id_orden)
            elif real == "ejecutada":
                fiabilidad.anotar(fiabilidad.EJECUTADA, simbolo, tipo, porque,
                                  id_orden=id_orden)
            else:
                fiabilidad.anotar(fiabilidad.EN_COLA, simbolo, tipo, porque,
                                  id_orden=id_orden)
            return e

        if e.estado != "ambigua":
            fiabilidad.anotar(fiabilidad.FALLIDA, simbolo, tipo,
                              str(e.error or "sin motivo"),
                              id_orden=id_orden)
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
                                      antes_cola, dormir=dormir, lado=lado)
        print(f"[ambigua] {detalle['detalle']}", flush=True)

        if desenlace in (EJECUTADA_TRAS_AMBIGUA, EN_COLA_TRAS_AMBIGUA):
            fiabilidad.anotar(fiabilidad.AMBIGUA_RESUELTA_SI, simbolo, tipo,
                              detalle["detalle"], id_orden=id_orden)
            # La orden existe: se devuelve como tal. El estado lo dice para que
            # quien lo lea sepa que hubo que averiguarlo.
            e.estado = ("ejecutada" if desenlace == EJECUTADA_TRAS_AMBIGUA
                        else "en_cola")
            e.error = None
            if e.estado == "ejecutada":
                e.acciones_hechas = int(round(detalle.get("acciones", 0)))
                completar_precio(broker, simbolo, lado, e, saldo_antes,
                                 entrada_antes, referencia, dormir=dormir)
            return e

        if not detalle.get("concluyente"):
            # No se pudo comprobar. Reintentar a ciegas podría comprar dos
            # veces, así que se para y se dice por qué.
            fiabilidad.anotar(fiabilidad.AMBIGUA_SIN_RESOLVER, simbolo, tipo,
                              detalle["detalle"], id_orden=id_orden)
            return e

        fiabilidad.anotar(fiabilidad.AMBIGUA_RESUELTA_NO, simbolo, tipo,
                          detalle["detalle"], id_orden=id_orden)
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
              dormir=time.sleep, lado: str = "compra",
              acciones: float | None = None) -> tuple[str, str, float]:
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

    COMPRAS Y VENTAS (fallo del 2026-10-02). Una compra se confirma porque la
    posición CRECE; una venta, porque BAJA o desaparece. Hasta ese día solo se
    miraba lo primero, y la venta de CTVA —ejecutada a 11,97— se anotó como
    rechazada. Una venta PARCIAL (la posición baja, pero menos de lo pedido)
    es ejecutada, con las acciones que de verdad se movieron.

    Devuelve ('ejecutada'|'rechazada'|'en_cola', explicación, acciones
    movidas). `en_cola` solo sale cuando XTB sigue diciendo que la orden existe
    y espera: eso es un estado real, no una suposición.
    """
    ultima = "en_cola", "sin respuesta concluyente de XTB", 0.0
    parcial = None
    for intento in range(1, CONFIRMACIONES + 1):
        dormir(ESPERA_CONFIRMACION)
        try:
            posiciones = broker.posiciones()
            cola = broker.ordenes_pendientes()
        except Exception as exc:  # noqa: BLE001
            ultima = "en_cola", f"no se pudo preguntar a XTB ({exc!r})", 0.0
            continue

        ahora = sum(float(p["acciones"]) for p in posiciones
                    if p.get("lado") == "buy" and p.get("ticker") == simbolo)
        movidas = movimiento(lado, acciones_antes, ahora)
        verbo = "pasó" if lado == "compra" else "bajó"
        if movidas > 0 and (not acciones or movidas >= float(acciones)):
            return "ejecutada", (
                f"XTB lo refleja: {simbolo} {verbo} de {acciones_antes:g} a "
                f"{ahora:g} acciones (intento {intento})"), movidas
        if movidas > 0:
            # Se movió, pero menos de lo pedido. Puede que el resto esté
            # entrando: se sigue mirando, y si al final no se completa, es una
            # ejecución PARCIAL — ejecutada, con lo que de verdad se movió.
            parcial = ("ejecutada", (
                f"PARCIAL: {simbolo} {verbo} de {acciones_antes:g} a {ahora:g} "
                f"acciones ({movidas:g} de {float(acciones):g}; intento "
                f"{intento})"), movidas)
            ultima = parcial
            continue

        if any(o.get("ticker") == simbolo for o in cola):
            # Sigue viva y esperando. Puede acabar ejecutándose o no, pero
            # ahora mismo existe: no se puede llamar rechazada.
            ultima = "en_cola", (f"la orden sigue en cola en XTB "
                                 f"(intento {intento})"), 0.0
            continue


        # NO se concluye "rechazada" al primer vistazo. Una orden de mercado
        # recién enviada tarda unos segundos en aparecer como posición, y
        # declararla muerta a los tres segundos es el mismo error que darla por
        # ejecutada sin mirar, solo que al revés. Pasó el 2026-10-02: CTVA se
        # dio por rechazada seis segundos después de mandarla, "tras 1
        # comprobación".
        if lado == "compra":
            ultima = "rechazada", (
                f"XTB no tiene ni posición ni orden de {simbolo} tras "
                f"{intento} de {CONFIRMACIONES} comprobaciones"), 0.0
        else:
            ultima = "rechazada", (
                f"la posición de {simbolo} sigue con {ahora:g} acciones y sin "
                f"orden en cola tras {intento} de {CONFIRMACIONES} "
                f"comprobaciones: la venta no entró"), 0.0
        if intento < CONFIRMACIONES:
            continue
        return ultima
    return parcial or ultima


def _precio_entrada(broker, simbolo: str) -> float | None:
    """El precio de entrada de la posición antes de vender (para el modelo pnl)."""
    try:
        pos = [p for p in broker.posiciones()
               if p.get("lado") == "buy" and p.get("ticker") == simbolo]
    except Exception as exc:  # noqa: BLE001 — sin él solo se pierde un modelo
        print(f"[precio] no se pudo leer la entrada de {simbolo} ({exc!r}).",
              flush=True)
        return None
    total = sum(float(p["acciones"]) for p in pos)
    if not total:
        return None
    return sum(float(p["acciones"]) * float(p.get("precio_entrada") or 0)
               for p in pos) / total


def completar_precio(broker, simbolo: str, lado: str, e, saldo_antes,
                     entrada_antes, referencia, dormir=time.sleep) -> None:
    """Rellena `e.precio` si XTB no lo dio. Nunca lo inventa.

    Orden de preferencia, de más directo a más deducido:
      1. el que devolvió XTB en la respuesta (`xtb`);
      2. en una compra, el precio de apertura de la posición nueva que XTB
         enseña en sus posiciones (`posicion`);
      3. en una venta, el que sale del cambio de saldo (`saldo-caja` o
         `saldo-pnl`, ver `deducir_precio_venta`).
    Si nada de eso da un número fiable, el precio se queda vacío y se dice
    por qué: un hueco honesto vale más que un número inventado.
    """
    if e.precio:
        e.precio_fuente = "xtb"
        return
    if lado == "compra":
        try:
            pos = [p for p in broker.posiciones()
                   if p.get("lado") == "buy" and p.get("ticker") == simbolo]
        except Exception as exc:  # noqa: BLE001
            print(f"[precio] {simbolo}: no se pudo leer la posición nueva "
                  f"({exc!r}); precio sin rellenar.", flush=True)
            return
        propias = [p for p in pos if e.orden and p.get("orden") == e.orden]
        candidatas = propias or (pos if len(pos) == 1 else [])
        if len(candidatas) == 1 and candidatas[0].get("precio_entrada"):
            e.precio = float(candidatas[0]["precio_entrada"])
            e.precio_fuente = "posicion"
            print(f"[precio] {simbolo}: {e.precio} (apertura de la posición en "
                  f"XTB)", flush=True)
        else:
            print(f"[precio] {simbolo}: {len(pos)} entradas en XTB y ninguna "
                  f"es inequívocamente la nueva; precio sin rellenar.",
                  flush=True)
        return

    if saldo_antes is None:
        print(f"[precio] {simbolo}: sin saldo previo; no se puede deducir el "
              f"precio de la venta.", flush=True)
        return
    # El saldo puede tardar un instante en reflejar la venta. Se le dan unas
    # vueltas antes de calcular sobre un saldo que todavía no se ha movido.
    saldo_despues = _saldo(broker)
    for _ in range(3):
        if saldo_despues is None or saldo_despues != saldo_antes:
            break
        dormir(ESPERA_CONFIRMACION)
        saldo_despues = _saldo(broker)
    if saldo_despues is None:
        return
    precio, metodo = deducir_precio_venta(
        saldo_antes, saldo_despues, float(e.acciones_hechas or e.acciones),
        entrada_antes, referencia)
    if precio is None:
        print(f"[precio] {simbolo}: no se pudo deducir del saldo — {metodo}.",
              flush=True)
        return
    e.precio = precio
    e.precio_fuente = metodo
    print(f"[precio] {simbolo}: {precio} deducido del saldo ({metodo}: "
          f"{saldo_antes:,.2f} -> {saldo_despues:,.2f})", flush=True)
