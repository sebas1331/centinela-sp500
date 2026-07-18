#!/usr/bin/env python
"""Escaneo PRE-APERTURA (~08:45 ET): DECIDE las entradas del día.

No ejecuta entradas (eso lo hace el post-cierre al open oficial); solo decide
qué acciones entrar hoy y las deja pendientes, registrando en decisiones.log
TODOS los candidatos y por qué se entra o no.

Verifica con el calendario si hoy hay mercado y si estamos en la ventana
pre-apertura (tolerante a retrasos del cron y al horario de verano). Idempotente:
si ya se procesó hoy, no repite.
"""
from _comun import parse_args, contexto, log

from centinela import (calendario, estado as est_mod, screener, simulador,
                       bitacora, runtime, notificaciones)
from centinela.modelo import Modelo


def main():
    args = parse_args("Escaneo pre-apertura de Centinela SP500")
    ahora, hoy, hoy_iso = contexto(args.fecha)

    if not args.forzar:
        if not calendario.es_dia_de_mercado(hoy):
            log("hoy no hay mercado; termino sin hacer nada."); return
        if not calendario.en_ventana_preapertura(ahora):
            log("fuera de la ventana pre-apertura; termino sin hacer nada."); return

    estado = est_mod.cargar()
    if not args.forzar and est_mod.ya_proceso_preapertura(estado, hoy_iso):
        log(f"pre-apertura ya procesada para {hoy_iso}; idempotente, termino."); return

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
    bitacora.log_decisiones(hoy_iso, lineas)

    estado["ultima_preapertura"] = hoy_iso
    est_mod.guardar(estado)

    log(f"resumen: universo={resumen['universo']} drawdown>=30%={resumen['en_drawdown']} "
        f"con_senal={resumen['con_senal']} decididas={len(nuevas)}")
    if nuevas:
        notificaciones.enviar(
            f"🛰️ Centinela pre-apertura {hoy_iso}: {len(nuevas)} entradas decididas: "
            + ", ".join(n["ticker"] for n in nuevas))


if __name__ == "__main__":
    main()
