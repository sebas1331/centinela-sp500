"""Preparación de datos compartida por los escaneos (pre-apertura y post-cierre)."""
from __future__ import annotations

from . import universo, datos, ath as ath_mod


def preparar_datos(actualizar: bool = True):
    """Devuelve (precios, ath_dict, sectores) con la caché de precios al día.

    - Refresca el universo S&P 500 (Wikipedia, semanal, con respaldo local).
    - Actualiza incrementalmente la caché de precios (~1.5 años por ticker).
    - Carga el ATH guardado; si no existe, lo inicializa desde la caché.
    """
    df_univ = universo.obtener_sp500()
    tickers = df_univ["ticker"].tolist()
    sectores = (dict(zip(df_univ["ticker"], df_univ["sector"]))
                if "sector" in df_univ.columns else {})

    precios = datos.actualizar_precios(tickers) if actualizar else {}

    ath_dict = ath_mod.cargar_ath()
    if not ath_dict:
        ath_dict = ath_mod.actualizar_ath(precios)

    return precios, ath_dict, sectores
