"""Score ligero de sentimiento sobre titulares recientes (yfinance .news + VADER).

LIMITACIÓN HONESTA (documentada también en el README): esto es una señal
SECUNDARIA y ruidosa. VADER está pensado para lenguaje de redes sociales, no
para titulares financieros; no entiende contexto de mercado ni sarcasmo, y las
noticias de yfinance son limitadas e irregulares. Se usa solo como matiz, nunca
como razón principal para entrar o salir. Su peso en la decisión es bajo.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

import yfinance as yf
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from . import config

_analizador = SentimentIntensityAnalyzer()
_ETIQUETAS_HTML = re.compile(r"<[^>]+>")


def _limpiar(texto: str) -> str:
    if not texto:
        return ""
    return _ETIQUETAS_HTML.sub(" ", texto).strip()


def _extraer_titulares(noticias, max_noticias: int) -> list[dict]:
    """Normaliza la lista .news de yfinance (formato anidado en 'content')."""
    out = []
    for n in (noticias or [])[:max_noticias]:
        cont = n.get("content", n) if isinstance(n, dict) else {}
        titulo = _limpiar(cont.get("title", ""))
        resumen = _limpiar(cont.get("summary") or cont.get("description") or "")
        if not titulo:
            continue
        fecha = cont.get("pubDate") or cont.get("displayTime")
        out.append({"titulo": titulo, "resumen": resumen, "fecha": fecha})
    return out


def sentimiento_titulares(ticker: str, max_noticias: int = 10) -> dict:
    """Devuelve el sentimiento medio de los titulares recientes de un ticker.

    Resultado: {score (-1..1), n (nº titulares), titulares [...]}. Si no hay
    noticias, score=0.0 y n=0 (neutral).
    """
    from .datos import a_simbolo_yf
    try:
        noticias = yf.Ticker(a_simbolo_yf(ticker)).news
    except Exception:  # noqa: BLE001
        noticias = []
    titulares = _extraer_titulares(noticias, max_noticias)
    if not titulares:
        return {"score": 0.0, "n": 0, "titulares": []}

    compuestos = []
    for t in titulares:
        texto = (t["titulo"] + ". " + t["resumen"]).strip()
        c = _analizador.polarity_scores(texto)["compound"]
        t["compound"] = round(c, 3)
        compuestos.append(c)
    score = sum(compuestos) / len(compuestos)
    return {"score": round(score, 3), "n": len(titulares), "titulares": titulares}
