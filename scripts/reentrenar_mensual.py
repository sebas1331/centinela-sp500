#!/usr/bin/env python
"""Reentrenamiento mensual walk-forward (workflow largo programado).

Incorpora datos nuevos (actualiza el histórico incrementalmente), recalcula el
ATH, reconstruye el dataset y re-ajusta el modelo FINAL con el MISMO tipo y
features. Respeta la REGLA DURA de autoaprendizaje:

  - El umbral NO se cambia salvo que haya >= MIN_OPERACIONES_PARA_CAMBIO (30)
    operaciones cerradas nuevas desde el último (re)entrenamiento; en ese caso
    se recalcula el umbral con la evidencia walk-forward y el cambio se registra
    en CHANGELOG.md. Features y stop no se tocan aquí.
  - El holdout nunca se reutiliza para tunear.

Uso: python scripts/reentrenar_mensual.py [--anios 11]
"""
import sys
import argparse
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # para importar entrenar_inicial

from centinela import config, universo, datos, etiquetado, bitacora  # noqa: E402
from centinela.modelo import Modelo, entrenar_calibrado  # noqa: E402
import entrenar_inicial as ei  # reutiliza helpers ya probados  # noqa: E402


def actualizar_historico_incremental(tickers):
    """Actualiza el histórico completo por ticker (append de datos recientes)."""
    hist = {}
    hoy = pd.Timestamp(datetime.now(config.TZ_ET).date())
    for i in range(0, len(tickers), config.LOTE_DESCARGA):
        grupo = tickers[i:i + config.LOTE_DESCARGA]
        # descargamos solo lo reciente (últimos ~30 días) y lo anexamos
        recientes = datos.descargar(grupo, start=(hoy - pd.Timedelta(days=30)).strftime("%Y-%m-%d"),
                                    end=(hoy + pd.Timedelta(days=1)).strftime("%Y-%m-%d"))
        for t in grupo:
            ruta = ei.DIR_HIST / f"{t}.parquet"
            base = None
            if ruta.exists():
                try:
                    base = pd.read_parquet(ruta); base.index = pd.to_datetime(base.index)
                except Exception:  # noqa: BLE001
                    base = None
            fresco = recientes.get(t)
            if base is not None and fresco is not None:
                comb = pd.concat([base, fresco])
                comb = comb[~comb.index.duplicated(keep="last")].sort_index()
            elif fresco is not None:
                comb = fresco
            elif base is not None:
                comb = base
            else:
                continue
            comb.to_parquet(ruta)
            hist[t] = comb
        ei._log(f"  histórico actualizado {min(i+config.LOTE_DESCARGA, len(tickers))}/{len(tickers)}")
    return hist


def registrar_changelog(lineas):
    ruta = config.BASE_DIR / "CHANGELOG.md"
    cab = "" if ruta.exists() else "# CHANGELOG — Centinela SP500\n\n"
    with open(ruta, "a", encoding="utf-8") as f:
        if cab:
            f.write(cab)
        f.write("\n".join(lineas) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--anios", type=int, default=11)
    args = ap.parse_args()
    t0 = datetime.now()

    tickers = universo.tickers_sp500()
    ei._log(f"reentreno mensual: {len(tickers)} tickers")
    hist = actualizar_historico_incremental(tickers)
    # completa con lo que hubiera solo en caché (por si algún lote falló)
    hist = {**ei.descargar_historico(tickers), **hist}
    ei.calcular_ath_desde_hist(hist)

    corte = pd.Timestamp(datetime.now(config.TZ_ET).date()) - pd.DateOffset(years=args.anios)
    hist_rec = {t: df[df.index >= corte] for t, df in hist.items()}
    ds = etiquetado.construir_dataset(hist_rec, solo_drawdown=True)
    ei._log(f"dataset: {len(ds)} filas | positivos={ds['y'].mean():.1%}")

    modelo_actual = Modelo.cargar()
    tipo = modelo_actual.tipo
    umbral_vigente = modelo_actual.umbral
    ref = modelo_actual.metadatos.get("ultimo_reentreno") or modelo_actual.metadatos.get("entrenado", "")[:10]

    # métricas walk-forward frescas (para reporte y posible cambio de umbral)
    res, dsp, _ = ei.evaluar_tipo(ds, hist, tipo)
    umbral_wf = res["umbral"]

    n_nuevas = bitacora.n_cerradas_desde(ref[:10] if ref else None)
    cambio_umbral = False
    if n_nuevas >= config.MIN_OPERACIONES_PARA_CAMBIO and abs(umbral_wf - umbral_vigente) >= 0.02:
        umbral_final = umbral_wf
        cambio_umbral = True
    else:
        umbral_final = umbral_vigente

    ei._log(f"re-ajustando modelo final tipo={tipo} (umbral {'CAMBIA a '+str(umbral_final) if cambio_umbral else 'se mantiene '+str(umbral_final)})")
    est = entrenar_calibrado(ds[config.FEATURES_MODELO], ds["y"], tipo=tipo)
    hoy_iso = datetime.now(config.TZ_ET).date().isoformat()
    modelo = Modelo(
        estimador=est, features=config.FEATURES_MODELO, umbral=umbral_final, tipo=tipo,
        metadatos={**modelo_actual.metadatos,
                   "ultimo_reentreno": hoy_iso,
                   "n_filas_train": int(len(ds)),
                   "rango_datos": [ds["fecha"].min().date().isoformat(),
                                    ds["fecha"].max().date().isoformat()],
                   "senal_wf": res["senal_wf"], "senal_holdout": res["senal_ho"],
                   "umbral": umbral_final})
    modelo.guardar()

    lineas = [f"## {hoy_iso} — Reentrenamiento mensual",
              f"- Datos: {len(ds):,} eventos, hasta {ds['fecha'].max().date()}.",
              f"- Precisión walk-forward (fresca): {ei._p(res['senal_wf'].get('precision'))} "
              f"(señales={res['senal_wf'].get('n_senales')}).",
              f"- Operaciones cerradas nuevas desde {ref[:10] or 'inicio'}: {n_nuevas}."]
    if cambio_umbral:
        lineas.append(f"- **Cambio de umbral:** {umbral_vigente} → {umbral_final} "
                      f"(≥{config.MIN_OPERACIONES_PARA_CAMBIO} cierres nuevos + evidencia WF).")
    else:
        lineas.append(f"- Umbral SIN cambios ({umbral_final}). Regla dura: se requieren "
                      f"≥{config.MIN_OPERACIONES_PARA_CAMBIO} cierres nuevos y evidencia. "
                      f"Solo se re-ajustaron los pesos con datos nuevos.")
    registrar_changelog(lineas)
    ei._log(f"CHANGELOG actualizado. LISTO en {datetime.now()-t0}")


if __name__ == "__main__":
    main()
