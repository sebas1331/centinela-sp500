"""Configuración central de Centinela SP500.

Aquí viven TODAS las constantes del sistema: rutas, umbrales de la estrategia,
parámetros del modelo y ventanas horarias. Un solo lugar para auditarlo todo.

Cualquier cambio de umbral/feature/stop debe registrarse en CHANGELOG.md con
evidencia estadística y solo tras >=30 operaciones cerradas nuevas (regla dura).
"""
from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

# --------------------------------------------------------------------------- #
# Rutas
# --------------------------------------------------------------------------- #
BASE_DIR = Path(__file__).resolve().parent.parent   # raíz del repositorio
DATOS_DIR = BASE_DIR / "datos"
MODELOS_DIR = BASE_DIR / "modelos"
ESTADO_DIR = BASE_DIR / "estado"
LOGS_DIR = BASE_DIR / "logs"
REPORTES_DIR = BASE_DIR / "reportes"

# La caché de precios (parquet, pesada) NO va a git. En Actions se guarda en
# actions/cache y se reconstruye desde yfinance si se pierde. Se puede
# sobreescribir la ruta con la variable de entorno CENTINELA_CACHE_DIR.
CACHE_DIR = Path(os.environ.get("CENTINELA_CACHE_DIR", BASE_DIR / ".cache"))
CACHE_PRECIOS_DIR = CACHE_DIR / "precios"

# Archivos concretos
ARCHIVO_UNIVERSO = DATOS_DIR / "sp500_respaldo.csv"     # respaldo local del S&P 500
ARCHIVO_ATH = DATOS_DIR / "ath.json"                    # ATHs guardados (incremental)
ARCHIVO_MODELO = MODELOS_DIR / "modelo_centinela.pkl"   # modelo + metadatos + umbral
ARCHIVO_ESTADO = ESTADO_DIR / "estado.json"             # posiciones abiertas, capital
ARCHIVO_BITACORA_CSV = BASE_DIR / "bitacora.csv"
ARCHIVO_BITACORA_SQLITE = BASE_DIR / "bitacora.sqlite"

for _d in (DATOS_DIR, MODELOS_DIR, ESTADO_DIR, LOGS_DIR, REPORTES_DIR,
           CACHE_PRECIOS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------- #
# Zonas horarias y calendario
# --------------------------------------------------------------------------- #
TZ_ET = ZoneInfo("America/New_York")   # hora del mercado (maneja DST solo)
TZ_UTC = ZoneInfo("UTC")
CALENDARIO_BOLSA = "XNYS"               # NYSE/Nasdaq en exchange_calendars

# Ventana pre-apertura: procesamos si faltan entre estos minutos para la
# apertura oficial (09:30 ET). Con doble cron UTC (cubre EDT y EST) siempre uno
# cae dentro de la ventana y el otro aborta. Tolerante a retrasos del cron.
PREAPERTURA_MIN_ANTES = 20     # no antes de 20 min previos a la apertura
PREAPERTURA_MAX_ANTES = 95     # no después de 95 min previos (deja margen DST)

# Ventana post-cierre: procesamos si ya pasó el cierre (16:00 ET) del día.
POSTCIERRE_MIN_DESPUES = 30    # al menos 30 min tras el cierre

# --------------------------------------------------------------------------- #
# Estrategia — filtro base y horizonte
# --------------------------------------------------------------------------- #
DRAWDOWN_MINIMO = 0.30         # solo acciones >=30% por debajo de su ATH
HORIZONTE_DIAS_HABILES = 10    # ~2 semanas: límite de tiempo de cada operación
OBJETIVO_MINIMO = 0.05         # objetivo de +5% mínimo sobre precio de entrada

# Objetivo técnico variable: se toma el MÁXIMO entre +5% y estos componentes.
ATR_OBJETIVO_MULT = 2.0        # objetivo técnico = entrada + 2.0*ATR(14)
VENTANA_RESISTENCIA = 20       # resistencia = máximo de los últimos 20 días
# Tope: no fijar objetivos absurdamente lejanos (en múltiplos de ATR sobre entrada)
ATR_OBJETIVO_TOPE_MULT = 6.0

# Stop loss — SOLO en la Cartera A. Basado en ATR (ver justificación en README).
ATR_STOP_MULT = 2.0            # stop = entrada - 2.0*ATR(14)
STOP_MAX_PORCENTAJE = 0.12     # tope de seguridad: el stop nunca peor que -12%
STOP_MIN_PORCENTAJE = 0.03     # ni más ajustado que -3% (evita stops absurdos)

# --------------------------------------------------------------------------- #
# Modelo ML
# --------------------------------------------------------------------------- #
# IMPORTANTE (decisión de honestidad): los fundamentales/analistas/sentimiento
# NO tienen historial gratuito point-in-time. Incluirlos como features del
# modelo histórico sería look-ahead bias. Por eso el modelo ML se entrena SOLO
# con features TÉCNICOS (con historial completo, sin leakage), y los
# fundamentales/analistas/sentimiento actúan como capa de filtro/veto y ajuste
# en el escaneo EN VIVO (ver screener.py y decisiones registradas en bitácora).
FEATURES_MODELO = [
    "rsi_14",          # RSI de 14 días
    "ret_5",           # retorno últimos 5 días hábiles
    "ret_20",          # retorno últimos 20 días hábiles
    "ret_60",          # retorno últimos 60 días hábiles
    "dist_sma20",      # (precio/SMA20 - 1)
    "dist_sma50",      # (precio/SMA50 - 1)
    "dist_sma200",     # (precio/SMA200 - 1)
    "atr_pct",         # ATR(14) / precio
    "vol_rel",         # volumen / media de volumen 20 días
    "drawdown",        # magnitud del drawdown vs ATH (0..1)
    "dias_desde_ath",  # días naturales desde el ATH
    "gap_overnight",   # (open_hoy/close_ayer - 1)
]

# Etiquetado: positivo si en HORIZONTE_DIAS_HABILES el high alcanza
# (1+OBJETIVO_MINIMO) sobre el OPEN del día siguiente (idéntico a la ejecución).
ETIQUETA_OBJETIVO = OBJETIVO_MINIMO

# Umbral de probabilidad. Se calibra en el entrenamiento para PRECISIÓN (pocas
# señales pero buenas). Este es solo el valor por defecto; el modelo entrenado
# guarda su propio umbral óptimo en los metadatos y ese manda.
UMBRAL_PROB_DEFECTO = 0.55
# "Señal excepcional": permite entrar aunque los fundamentales estén flojos.
UMBRAL_PROB_EXCEPCIONAL = 0.70

# Validación
ANIOS_BACKTEST = 10            # historial objetivo para el backtest (8-10 años)
ANIOS_HOLDOUT = 1             # último año, se usa UNA sola vez
MESES_BLOQUE_WALKFORWARD = 6  # tamaño del bloque de evaluación walk-forward

# --------------------------------------------------------------------------- #
# Fundamentales — criterio de deterioro grave (documentado)
# --------------------------------------------------------------------------- #
# Score de salud 0..100. Por debajo de este umbral la empresa se considera en
# deterioro grave y se DESCARTA, salvo señal de modelo excepcional.
SCORE_FUNDAMENTAL_MINIMO = 35
# Vetos duros (deterioro grave) — cualquiera de estos descarta salvo excepción:
VETO_MARGEN_NETO_MENOR = -0.10        # margen neto < -10%
VETO_DEUDA_EBITDA_MAYOR = 8.0         # deuda/EBITDA > 8x
VETO_CRECIMIENTO_INGRESOS_MENOR = -0.30  # ingresos cayendo > 30% interanual

# --------------------------------------------------------------------------- #
# Datos / yfinance
# --------------------------------------------------------------------------- #
DIAS_HISTORIAL_CACHE = 550     # ~1.5 años por ticker en la caché diaria
LOTE_DESCARGA = 40             # tickers por lote en descargas batch
REINTENTOS_MAX = 4            # reintentos con backoff ante fallos de red
BACKOFF_BASE_SEG = 2.0        # segundos base para el backoff exponencial

# --------------------------------------------------------------------------- #
# Simulación / capital
# --------------------------------------------------------------------------- #
CAPITAL_POR_OPERACION = 1000.0   # tamaño nominal simulado por operación (USD)
MAX_POSICIONES_ABIERTAS = 20     # tope de posiciones simultáneas por cartera
MAX_ENTRADAS_POR_DIA = 5         # tope de entradas nuevas por día (prudencia)

# --------------------------------------------------------------------------- #
# Autoaprendizaje — regla dura
# --------------------------------------------------------------------------- #
MIN_OPERACIONES_PARA_CAMBIO = 30  # sin <30 cierres nuevos, no se cambia nada

# --------------------------------------------------------------------------- #
# Notificaciones (Telegram) — DESACTIVADO por defecto
# --------------------------------------------------------------------------- #
NOTIFICACIONES_ACTIVAS = os.environ.get("CENTINELA_NOTIF", "off").lower() == "on"
TELEGRAM_TOKEN = os.environ.get("CENTINELA_TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("CENTINELA_TELEGRAM_CHAT_ID", "")

# --------------------------------------------------------------------------- #
# Fuente del universo
# --------------------------------------------------------------------------- #
WIKIPEDIA_SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
