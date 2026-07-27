#!/usr/bin/env python
"""Escaneo PRE-APERTURA (~08:45 ET): DECIDE las entradas del día.

No ejecuta entradas (eso lo hace el post-cierre al open oficial); solo decide
qué acciones entrar hoy y las deja pendientes, registrando en decisiones.log
TODOS los candidatos y por qué se entra o no.

Verifica con el calendario si hoy hay mercado y si estamos en la ventana
pre-apertura (tolerante a retrasos del cron y al horario de verano). Idempotente:
si ya se procesó hoy, no repite.
"""
from _comun import parse_args, contexto, log, finalizar, esperar_a_ventana

from centinela import (calendario, estado as est_mod, screener, simulador,
                       bitacora, runtime, notificaciones, resultados as res)
from centinela.modelo import Modelo


def _control_de_ventana(args, ahora, hoy, hoy_iso):
    """Decide si este disparo debe trabajar, esperar, callarse o fallar.

    Devuelve None para "sigue adelante" o un resultado con el que terminar.
    Va antes que cualquier descarga de datos porque los tres desenlaces de
    salida son baratos y no necesitan red.
    """
    if args.forzar:
        return None

    if not calendario.es_dia_de_mercado(hoy):
        log("hoy no hay mercado; termino sin hacer nada.")
        return res.OMITIDO_SIN_MERCADO

    # La idempotencia se comprueba ANTES que la ventana: si otro disparo de hoy
    # ya hizo el trabajo, este sobra y no tiene sentido ni que espere ni que se
    # queje de haber llegado tarde.
    if est_mod.ya_proceso_preapertura(est_mod.cargar(), hoy_iso):
        log(f"pre-apertura ya procesada para {hoy_iso}; idempotente, termino.")
        return res.OMITIDO_YA_PROCESADO

    fase = calendario.fase_preapertura(ahora)

    # Estar dentro de la ventana no basta: se apunta a las 08:45 ET, que es el
    # horario con el que se diseñó el sistema. Un disparo que llega a las 05:40
    # está técnicamente en ventana, pero decidir tres horas antes de lo previsto
    # cambiaría el carácter de las entradas, así que espera o le cede el turno a
    # un peldaño más cercano. Lo que NUNCA hace es esperar más allá del techo.
    preferido = calendario.momento_preferido_preapertura(hoy)
    if preferido is not None and ahora < preferido:
        if not esperar_a_ventana(preferido, "pre-apertura", ahora):
            return res.OMITIDO_ANTES_DE_VENTANA
        # Durante la espera pudo entrar otro disparo y hacer el trabajo.
        if est_mod.ya_proceso_preapertura(est_mod.cargar(), hoy_iso):
            log(f"otro disparo procesó {hoy_iso} mientras esperaba; termino.")
            return res.OMITIDO_YA_PROCESADO
        fase = calendario.fase_preapertura()

    if fase == calendario.DESPUES:
        # Irrecuperable: pasada la apertura, decidir la entrada sería mirar el
        # precio al que luego se simula la compra. Se pierde el día, y eso TIENE
        # que verse rojo.
        log("arranqué pasado el techo de la ventana pre-apertura y hoy nadie la "
            "había procesado: la sesión se pierde.")
        return res.FALLO_VENTANA_PERDIDA

    if fase != calendario.DENTRO:
        log(f"fase inesperada '{fase}' para la ventana pre-apertura.")
        return res.OMITIDO_SIN_MERCADO

    return None


def main():
    args = parse_args("Escaneo pre-apertura de Centinela SP500")
    ahora, hoy, hoy_iso = contexto(args.fecha)

    desenlace = _control_de_ventana(args, ahora, hoy, hoy_iso)
    if desenlace is not None:
        return desenlace

    # El control pudo dormir hasta que abriera la ventana, así que `ahora` puede
    # estar rancio y es el que sella el timestamp_escaneo de la bitácora.
    ahora, _, _ = contexto(args.fecha)
    estado = est_mod.cargar()

    log(f"pre-apertura {hoy_iso}: preparando datos...")
    precios, ath_dict, sectores = runtime.preparar_datos()
    modelo = Modelo.cargar()
    log(f"datos listos ({len(precios)} tickers). Escaneando con modelo "
        f"{modelo.tipo} (umbral {modelo.umbral})...")

    decisiones, lineas, resumen = screener.escanear(precios, modelo, ath_dict, sectores)
    ts = ahora.isoformat()
    nuevas = simulador.registrar_decisiones_entrada(estado, decisiones, hoy_iso, ts)

    lineas.append(f"DECIDIDAS PARA ENTRAR HOY ({len(nuevas)}): "
                  f"{[n['ticker'] for n in nuevas]}")
    # Se escribe SIEMPRE, aunque no se decida ninguna entrada: es la prueba de
    # vida del día. Un día bursátil sin línea aquí significa que el sistema no
    # corrió, y el vigilante lo denuncia.
    bitacora.log_decisiones(hoy_iso, lineas, escaneo="preapertura")

    estado["ultima_preapertura"] = hoy_iso
    est_mod.sellar_ejecucion(estado, "preapertura")
    est_mod.guardar(estado)

    log(f"resumen: universo={resumen['universo']} drawdown>=30%={resumen['en_drawdown']} "
        f"con_senal={resumen['con_senal']} decididas={len(nuevas)}")
    if nuevas:
        notificaciones.enviar(
            f"🛰️ Centinela pre-apertura {hoy_iso}: {len(nuevas)} entradas decididas: "
            + ", ".join(n["ticker"] for n in nuevas))

    return res.PROCESADO


if __name__ == "__main__":
    finalizar(main())
