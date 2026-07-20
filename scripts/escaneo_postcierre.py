#!/usr/bin/env python
"""Escaneo POST-CIERRE (~18:00 ET): ejecuta entradas, gestiona salidas y reporta.

Pasos:
  1. Ejecuta las entradas pendientes (decididas en la pre-apertura) al OPEN
     oficial de su sesión.
  2. Gestiona las posiciones abiertas: recalcula el objetivo de cada una con la
     info nueva y evalúa salidas (objetivo/tiempo/stop) con la barra de hoy.
  3. Actualiza el ATH incrementalmente.
  4. Genera reportes si toca (semanal los viernes; mensual el primer día de mes).

Verifica calendario/ventana e idempotencia igual que la pre-apertura.
"""
from _comun import parse_args, contexto, log, finalizar

from centinela import (calendario, estado as est_mod, simulador, bitacora,
                       runtime, reportes, notificaciones, ath as ath_mod)


def main():
    args = parse_args("Escaneo post-cierre de Centinela SP500")
    ahora, hoy, hoy_iso = contexto(args.fecha)

    if not args.forzar:
        if not calendario.es_dia_de_mercado(hoy):
            log("hoy no hay mercado; termino sin hacer nada."); return "omitido:sin-mercado"
        if not calendario.en_ventana_postcierre(ahora):
            log("aún no es la ventana post-cierre; termino sin hacer nada."); return "omitido:fuera-de-ventana"

    estado = est_mod.cargar()
    if not args.forzar and est_mod.ya_proceso_postcierre(estado, hoy_iso):
        log(f"post-cierre ya procesado para {hoy_iso}; idempotente, termino."); return "omitido:ya-procesado"

    mes_previo = (estado.get("ultima_postcierre") or "")[:7]

    log(f"post-cierre {hoy_iso}: preparando datos...")
    precios, ath_dict, sectores = runtime.preparar_datos()

    abiertas = simulador.ejecutar_entradas_pendientes(estado, precios, hoy_iso)
    log(f"entradas ejecutadas al open: {len(abiertas)//2} "
        f"({[p['ticker'] for p in abiertas if p['portafolio']=='A']})")

    cerradas, cambios = simulador.gestionar_posiciones(estado, precios, hoy_iso)
    log(f"objetivos recalculados: {len(cambios)} cambios | "
        f"posiciones cerradas hoy: {len(cerradas)}")
    for c in cerradas:
        log(f"  CIERRE {c['portafolio']} {c['ticker']}: {c['motivo_salida']} "
            f"P&L={c['pnl_pct']:.2%}")

    ath_mod.actualizar_ath(precios)

    lineas = [f"POST-CIERRE {hoy_iso}",
              f"entradas ejecutadas: {[p['ticker'] for p in abiertas if p['portafolio']=='A']}",
              f"cambios de objetivo: {len(cambios)}",
              f"cierres: {[(c['ticker'], c['portafolio'], c['motivo_salida'], round(c['pnl_pct'],4)) for c in cerradas]}"]
    bitacora.log_decisiones(hoy_iso, lineas)

    estado["ultima_postcierre"] = hoy_iso
    est_mod.guardar(estado)

    # Reportes
    if hoy.weekday() == 4:  # viernes -> reporte semanal
        r = reportes.generar_semanal(hoy_iso); log(f"reporte semanal -> {r}")
    if mes_previo and mes_previo != hoy_iso[:7]:  # primer día de mercado del mes
        r = reportes.generar_mensual(hoy_iso); log(f"reporte mensual -> {r}")

    if cerradas:
        notificaciones.enviar(
            f"🛰️ Centinela post-cierre {hoy_iso}: {len(cerradas)} cierres. "
            + ", ".join(f"{c['ticker']}/{c['portafolio']} {c['pnl_pct']:.1%}" for c in cerradas))

    return "procesado"


if __name__ == "__main__":
    finalizar(main())
