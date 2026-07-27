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
from _comun import parse_args, contexto, log, finalizar, esperar_a_ventana

from centinela import (calendario, estado as est_mod, simulador, bitacora,
                       runtime, reportes, notificaciones, ath as ath_mod,
                       resultados as res)


def _control_de_ventana(args, ahora, hoy, hoy_iso):
    """Decide si este disparo debe trabajar, esperar, callarse o fallar.

    Devuelve None para "sigue adelante" o un resultado con el que terminar.
    A diferencia de la pre-apertura, esta ventana no tiene techo: una vez
    cerrada la sesión, procesarla más tarde da el mismo resultado. El fallo
    posible aquí es otro: que el cron se retrase tanto que cruce la medianoche
    ET y la sesión de ayer se quede sin procesar para siempre.
    """
    if args.forzar:
        return None

    if not calendario.es_dia_de_mercado(hoy):
        log("hoy no hay mercado; termino sin hacer nada.")
        return res.OMITIDO_SIN_MERCADO

    if est_mod.ya_proceso_postcierre(est_mod.cargar(), hoy_iso):
        log(f"post-cierre ya procesado para {hoy_iso}; idempotente, termino.")
        return res.OMITIDO_YA_PROCESADO

    fase = calendario.fase_postcierre(ahora)

    # Mismo criterio que la pre-apertura: se apunta a las 18:00 ET.
    preferido = calendario.momento_preferido_postcierre(hoy)
    if preferido is not None and ahora < preferido:
        if not esperar_a_ventana(preferido, "post-cierre", ahora):
            return res.OMITIDO_ANTES_DE_VENTANA
        if est_mod.ya_proceso_postcierre(est_mod.cargar(), hoy_iso):
            log(f"otro disparo procesó {hoy_iso} mientras esperaba; termino.")
            return res.OMITIDO_YA_PROCESADO
        fase = calendario.fase_postcierre()

    if fase != calendario.DENTRO:
        log(f"fase inesperada '{fase}' para la ventana post-cierre.")
        return res.OMITIDO_SIN_MERCADO

    return None


def main():
    args = parse_args("Escaneo post-cierre de Centinela SP500")
    ahora, hoy, hoy_iso = contexto(args.fecha)

    desenlace = _control_de_ventana(args, ahora, hoy, hoy_iso)
    if desenlace is not None:
        return desenlace

    estado = est_mod.cargar()
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
    # Se escribe SIEMPRE, aunque no haya habido ni entradas ni cierres: es la
    # prueba de vida del día que vigila el vigilante.
    bitacora.log_decisiones(hoy_iso, lineas, escaneo="postcierre")

    estado["ultima_postcierre"] = hoy_iso
    est_mod.sellar_ejecucion(estado, "postcierre")
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

    return res.PROCESADO


if __name__ == "__main__":
    finalizar(main())
