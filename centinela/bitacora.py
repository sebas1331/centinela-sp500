"""Bitácora auditable de operaciones simuladas.

Fuente de verdad: SQLite (bitacora.sqlite). En cada escritura se regenera un
espejo ordenado en CSV (bitacora.csv) para consultarlo cómodo desde el celular.
Además, un decisiones.log diario registra TODOS los candidatos evaluados y por
qué se entró o no (auditabilidad total).

Cada DECISIÓN de entrada genera DOS filas (una por cartera: A con stop, B sin),
enlazadas por el campo 'grupo'.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

import pandas as pd

from . import config

# Orden y nombres de columnas (español) para el espejo CSV.
COLUMNAS = [
    "id", "grupo", "ticker", "portafolio", "sector",
    "fecha_entrada", "hora_entrada_et", "hora_entrada_utc", "timestamp_escaneo",
    "precio_entrada", "probabilidad", "score_fundamental",
    "objetivo_inicial", "objetivo_actual", "historial_objetivos", "stop",
    "fecha_salida", "hora_salida_et", "precio_salida", "motivo_salida",
    "pnl_pct", "dias_habiles", "estado", "notas",
]

_DDL = """
CREATE TABLE IF NOT EXISTS operaciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    grupo INTEGER,
    ticker TEXT,
    portafolio TEXT,
    sector TEXT,
    fecha_entrada TEXT,
    hora_entrada_et TEXT,
    hora_entrada_utc TEXT,
    timestamp_escaneo TEXT,
    precio_entrada REAL,
    probabilidad REAL,
    score_fundamental REAL,
    objetivo_inicial REAL,
    objetivo_actual REAL,
    historial_objetivos TEXT,
    stop REAL,
    fecha_salida TEXT,
    hora_salida_et TEXT,
    precio_salida REAL,
    motivo_salida TEXT,
    pnl_pct REAL,
    dias_habiles INTEGER,
    estado TEXT,
    notas TEXT
);
"""


def _conectar() -> sqlite3.Connection:
    con = sqlite3.connect(config.ARCHIVO_BITACORA_SQLITE)
    con.execute(_DDL)
    return con


def registrar_entrada(datos: dict) -> int:
    """Inserta una fila de operación ABIERTA. Devuelve el id asignado."""
    campos = [
        "grupo", "ticker", "portafolio", "sector", "fecha_entrada",
        "hora_entrada_et", "hora_entrada_utc", "timestamp_escaneo",
        "precio_entrada", "probabilidad", "score_fundamental",
        "objetivo_inicial", "objetivo_actual", "historial_objetivos", "stop",
        "estado", "notas",
    ]
    hist = datos.get("historial_objetivos")
    if not isinstance(hist, str):
        hist = json.dumps(hist or [], ensure_ascii=False)
    valores = [
        datos.get("grupo"), datos.get("ticker"), datos.get("portafolio"),
        datos.get("sector"), datos.get("fecha_entrada"),
        datos.get("hora_entrada_et"), datos.get("hora_entrada_utc"),
        datos.get("timestamp_escaneo"), datos.get("precio_entrada"),
        datos.get("probabilidad"), datos.get("score_fundamental"),
        datos.get("objetivo_inicial"), datos.get("objetivo_actual", datos.get("objetivo_inicial")),
        hist, datos.get("stop"), "abierta", datos.get("notas", ""),
    ]
    con = _conectar()
    with con:
        cur = con.execute(
            f"INSERT INTO operaciones ({','.join(campos)}) "
            f"VALUES ({','.join('?' * len(campos))})", valores)
        oid = cur.lastrowid
    con.close()
    exportar_csv()
    return oid


def actualizar_objetivo(oid: int, objetivo_actual: float, historial: list) -> None:
    con = _conectar()
    with con:
        con.execute(
            "UPDATE operaciones SET objetivo_actual=?, historial_objetivos=? WHERE id=?",
            (objetivo_actual, json.dumps(historial, ensure_ascii=False), oid))
    con.close()
    exportar_csv()


def registrar_salida(oid: int, fecha_salida: str, hora_salida_et: str,
                     precio_salida: float, motivo: str, pnl_pct: float,
                     dias_habiles: int, notas_extra: str = "") -> None:
    con = _conectar()
    with con:
        con.execute(
            "UPDATE operaciones SET fecha_salida=?, hora_salida_et=?, "
            "precio_salida=?, motivo_salida=?, pnl_pct=?, dias_habiles=?, "
            "estado='cerrada', notas=TRIM(COALESCE(notas,'')||' '||?) WHERE id=?",
            (fecha_salida, hora_salida_et, precio_salida, motivo, pnl_pct,
             dias_habiles, notas_extra, oid))
    con.close()
    exportar_csv()


def cargar_df() -> pd.DataFrame:
    con = _conectar()
    df = pd.read_sql_query(
        "SELECT * FROM operaciones ORDER BY fecha_entrada, grupo, portafolio", con)
    con.close()
    return df


def exportar_csv() -> None:
    df = cargar_df()
    for c in COLUMNAS:
        if c not in df.columns:
            df[c] = None
    df[COLUMNAS].to_csv(config.ARCHIVO_BITACORA_CSV, index=False)


def operaciones_cerradas() -> pd.DataFrame:
    df = cargar_df()
    return df[df["estado"] == "cerrada"].copy()


def operaciones_abiertas() -> pd.DataFrame:
    df = cargar_df()
    return df[df["estado"] == "abierta"].copy()


def n_cerradas_desde(fecha_iso: str | None) -> int:
    """Nº de operaciones cerradas cuya salida es posterior a fecha_iso (regla
    dura de autoaprendizaje: >=30 cierres nuevos para cambiar algo)."""
    df = operaciones_cerradas()
    if fecha_iso is None:
        return len(df)
    return int((df["fecha_salida"] > fecha_iso).sum())


# --------------------------------------------------------------------------- #
# decisiones.log — auditoría diaria de candidatos
# --------------------------------------------------------------------------- #
def log_decisiones(fecha_iso: str, lineas: list[str]) -> None:
    ruta = config.LOGS_DIR / f"decisiones-{fecha_iso}.log"
    marca = datetime.now(config.TZ_ET).strftime("%Y-%m-%d %H:%M:%S ET")
    with open(ruta, "a", encoding="utf-8") as f:
        f.write(f"\n===== escaneo {marca} =====\n")
        for ln in lineas:
            f.write(ln + "\n")
