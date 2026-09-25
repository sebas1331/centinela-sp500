#!/usr/bin/env python
"""Envía por Telegram las órdenes y los avisos de la sesión.

Corre en un JOB APARTE de los workflows, después de que la bitácora ya esté
persistida y verificada. Esa separación es deliberada y es la regla más
importante de todo el módulo: si Telegram está caído, este script se pone ROJO
y no pasa nada más — el trabajo de trading ya está a salvo varios jobs antes.

Eventos:
  preapertura  Órdenes de compra de hoy (una por ticker decidido).
  postcierre   Entradas confirmadas, cambios de objetivo, ventas, avisos de
               salida por tiempo de mañana y resumen del día.
  alerta       Avería detectada por el Vigilante.
  prueba       Un mensaje de cada tipo con datos de ejemplo, marcados [PRUEBA].

Los mensajes se construyen en `centinela/notificaciones.py` (funciones puras) y
el tamaño de cada posición sale de `centinela/cuenta.py`. Aquí solo se reúnen
los datos y se decide qué toca enviar hoy.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from centinela import (config, cuenta, calendario, datos, objetivos,  # noqa: E402
                       notificaciones as notif, estado as est_mod,
                       resultados as res)

RUTA_BITACORA = config.BASE_DIR / "bitacora.csv"


def log(msg: str) -> None:
    print(f"[notificar] {msg}", flush=True)


# --------------------------------------------------------------------------- #
# Datos de apoyo
# --------------------------------------------------------------------------- #
def carteras_activas() -> list[str]:
    return [c for c in ("A", "B") if c in config.CARTERAS_NOTIFICADAS]


def cargar_bitacora() -> pd.DataFrame:
    bit = pd.read_csv(RUTA_BITACORA)
    bit["duplicada"] = cuenta.marcar_duplicadas(bit)
    # Igual que el dashboard y la auditoría: las entradas del bug del
    # 2026-08-06 no son la estrategia y no deben mover el tamaño de las
    # posiciones nuevas.
    return bit[~bit["duplicada"]].copy()


def cuentas(bit: pd.DataFrame) -> dict:
    """Cuenta simulada de cada cartera, neta de fricciones."""
    return {c: cuenta.simular(bit[bit["portafolio"] == c], fricciones=True)
            for c in ("A", "B")}


def valor_de_slot(cta: dict) -> float:
    """Lo que invertiría hoy una entrada nueva en esa cartera."""
    return cta["equity_contable"] / cta["slots"]


def precios_de(tickers) -> dict:
    """OHLC reciente de unos pocos tickers, apoyándose en la caché del repo.

    `actualizar_precios` y no `descargar`: la caché de parquet ya tiene el
    historial y solo hace falta el incremento desde la última sesión guardada.
    Con `descargar` a pelo, cada job pediría 45 días de datos de cero y tres
    jobs del mismo día (escaneo, notificación y dashboard) irían a yfinance por
    separado a por lo mismo, que es la receta para que empiece a limitar.
    """
    tickers = sorted(set(t for t in tickers if isinstance(t, str)))
    if not tickers:
        return {}
    return datos.actualizar_precios(tickers)


def ultimo_cierre(precios: dict, ticker: str, antes_de: str | None = None):
    df = precios.get(ticker)
    if df is None or df.empty:
        return None
    if antes_de:
        df = df[df.index < pd.Timestamp(antes_de)]
        if df.empty:
            return None
    return float(df["Close"].iloc[-1])


def dia_limite(fecha_entrada_iso: str) -> str:
    """Décima sesión hábil contando la de entrada (misma regla que el simulador)."""
    s = calendario.sesion_n_despues(fecha_entrada_iso,
                                    config.HORIZONTE_DIAS_HABILES - 1)
    return pd.Timestamp(s).date().isoformat()


# --------------------------------------------------------------------------- #
# Evento: pre-apertura
# --------------------------------------------------------------------------- #
def evento_preapertura(fecha_iso: str, registro: dict, prueba=False) -> int:
    """Una orden de compra por cada entrada decidida hoy y aún sin ejecutar.

    El objetivo y el stop se calculan sobre el ÚLTIMO CIERRE, porque el precio
    de apertura todavía no existe. Cuando el post-cierre ejecute la entrada al
    open real, el mensaje de ENTRADA CONFIRMADA trae los niveles definitivos.
    Esa diferencia es inherente a operar con órdenes market-on-open y el texto
    lo dice llamando "referencia" al precio de partida.
    """
    estado = est_mod.cargar()
    pendientes = [e for e in estado.get("entradas_pendientes", [])
                  if e.get("fecha_decision") == fecha_iso]
    if not pendientes:
        log("no hay entradas decididas hoy; nada que notificar.")
        return 0

    bit = cargar_bitacora()
    ctas = cuentas(bit)
    precios = precios_de([e["ticker"] for e in pendientes])
    limite = dia_limite(fecha_iso)

    enviadas = 0
    for e in pendientes:
        ref = ultimo_cierre(precios, e["ticker"], antes_de=fecha_iso)
        if ref is None:
            raise RuntimeError(
                f"Sin precio de referencia para {e['ticker']}: no se puede "
                f"notificar una orden de compra sin decir a qué precio está.")
        atr = e.get("atr")
        objetivo = objetivos.objetivo_inicial(ref, atr, e.get("resistencia"),
                                              e.get("target_analista"))
        for cart in e.get("carteras", ("A", "B")):
            if cart not in carteras_activas():
                continue
            ident = notif.identificador(fecha_iso, "orden_compra", cart,
                                        e["ticker"], prueba)
            importe = valor_de_slot(ctas[cart])
            texto = notif.msg_orden_compra({
                "cartera": cart, "ticker": e["ticker"], "referencia": ref,
                "importe": importe, "acciones": importe / ref,
                "objetivo": objetivo,
                "stop": objetivos.stop_inicial(ref, atr) if cart == "A" else None,
                "fecha_limite": limite,
                "proba": e.get("proba") or 0.0,
                "score_fundamental": e.get("score_fundamental"),
            }, prueba)
            if notif.enviar_una(registro, ident, texto):
                enviadas += 1
    log(f"órdenes de compra enviadas: {enviadas}")
    return enviadas


# --------------------------------------------------------------------------- #
# Evento: post-cierre
# --------------------------------------------------------------------------- #
def evento_postcierre(fecha_iso: str, registro: dict, prueba=False) -> int:
    bit = cargar_bitacora()
    ctas = cuentas(bit)
    activas = carteras_activas()
    enviadas = 0

    trades = {t["id"]: t for c in ("A", "B") for t in ctas[c]["trades"]}

    # --- 1) entradas confirmadas ------------------------------------------
    nuevas = bit[(bit["fecha_entrada"] == fecha_iso)]
    for _, r in nuevas.iterrows():
        if r["portafolio"] not in activas:
            continue
        t = trades.get(int(r["id"]), {})
        ident = notif.identificador(fecha_iso, "entrada", r["portafolio"],
                                    r["ticker"], prueba)
        texto = notif.msg_entrada_confirmada({
            "cartera": r["portafolio"], "ticker": r["ticker"],
            "precio": r["precio_entrada"], "hora": r["hora_entrada_et"],
            "importe": t.get("coste"), "acciones": t.get("acciones") or 0.0,
            "objetivo": r["objetivo_actual"],
            "stop": None if pd.isna(r["stop"]) else r["stop"],
            "fecha_limite": dia_limite(str(r["fecha_entrada"])),
        }, prueba)
        if notif.enviar_una(registro, ident, texto):
            enviadas += 1

    # --- 2) cambios de objetivo -------------------------------------------
    # Solo de posiciones que siguen ABIERTAS: si la posición se cerró hoy, el
    # cambio de objetivo del día es ruido —lo que importa es la venta— y pedir
    # que se mueva una orden límite de algo ya vendido sería un error.
    for _, r in bit[bit["estado"] == "abierta"].iterrows():
        if r["portafolio"] not in activas:
            continue
        historial = json.loads(r["historial_objetivos"])
        de_hoy = [h for h in historial if h["fecha"] == fecha_iso
                  and h.get("motivo") != "objetivo inicial"]
        if not de_hoy:
            continue
        previos = [h["objetivo"] for h in historial
                   if h["fecha"] < fecha_iso or h.get("motivo") == "objetivo inicial"]
        if not previos:
            continue
        ident = notif.identificador(fecha_iso, "objetivo", r["portafolio"],
                                    r["ticker"], prueba)
        texto = notif.msg_objetivo_actualizado({
            "cartera": r["portafolio"], "ticker": r["ticker"],
            "anterior": previos[-1], "nuevo": de_hoy[-1]["objetivo"],
            "motivo": de_hoy[-1].get("motivo", "recálculo"),
        }, prueba)
        if notif.enviar_una(registro, ident, texto):
            enviadas += 1

    # --- 3) ventas ejecutadas ---------------------------------------------
    cerradas_hoy = bit[bit["fecha_salida"] == fecha_iso]
    for _, r in cerradas_hoy.iterrows():
        if r["portafolio"] not in activas:
            continue
        t = trades.get(int(r["id"]), {})
        ident = notif.identificador(fecha_iso, "venta", r["portafolio"],
                                    r["ticker"], prueba)
        texto = notif.msg_venta({
            "cartera": r["portafolio"], "ticker": r["ticker"],
            "motivo": r["motivo_salida"], "entrada": r["precio_entrada"],
            "salida": r["precio_salida"], "pnl_pct": r["pnl_pct"],
            "pnl_dinero": t.get("pnl_dinero"),
        }, prueba)
        if notif.enviar_una(registro, ident, texto):
            enviadas += 1

    # --- 4) salidas por tiempo de MAÑANA ----------------------------------
    manana = calendario.sesion_n_despues(fecha_iso, 1)
    manana_iso = pd.Timestamp(manana).date().isoformat()
    for _, r in bit[bit["estado"] == "abierta"].iterrows():
        if r["portafolio"] not in activas:
            continue
        if dia_limite(str(r["fecha_entrada"])) != manana_iso:
            continue
        ident = notif.identificador(fecha_iso, "tiempo_manana", r["portafolio"],
                                    r["ticker"], prueba)
        texto = notif.msg_salida_por_tiempo_manana({
            "cartera": r["portafolio"], "ticker": r["ticker"],
            "fecha_limite": manana_iso, "entrada": r["precio_entrada"],
            "objetivo": r["objetivo_actual"],
        }, prueba)
        if notif.enviar_una(registro, ident, texto):
            enviadas += 1

    # --- 5) resumen del día ------------------------------------------------
    ident = notif.identificador(fecha_iso, "resumen", prueba=prueba)
    if not notif.ya_enviada(registro, ident):
        if notif.enviar_una(registro, ident,
                            notif.msg_resumen_diario(
                                resumen_del_dia(bit, ctas, fecha_iso), prueba)):
            enviadas += 1

    log(f"mensajes de post-cierre enviados: {enviadas}")
    return enviadas


def resumen_del_dia(bit: pd.DataFrame, ctas: dict, fecha_iso: str) -> dict:
    """Foto de la cuenta al cierre: abiertas, cierres de hoy y P&L.

    El P&L del día es la diferencia de EQUITY entre hoy y la sesión anterior,
    marcado a mercado. No es la suma del P&L de lo cerrado hoy: eso ignoraría
    cómo se movieron las posiciones que siguen abiertas, que es la mayor parte
    de la cuenta.
    """
    abiertos = [t for c in ("A", "B") for t in ctas[c]["abiertas"]]
    precios = precios_de([t["ticker"] for t in abiertos])
    sesiones = sesiones_hasta(fecha_iso, precios, n=2)

    # Sin precios de las abiertas no hay marca a mercado posible. No es motivo
    # para no mandar el resumen —la mayoría de sus cifras no dependen de eso—
    # pero sí para que el mensaje lo diga en vez de dar el valor contable por
    # valor de mercado sin avisar.
    hay_marca = all(t["ticker"] in precios for t in abiertos) and len(sesiones) >= 2

    carteras = []
    for c in carteras_activas():
        cta = ctas[c]
        curva = cuenta.curva_diaria(cta, precios, sesiones) if sesiones else pd.DataFrame()
        if hay_marca and len(curva) >= 2:
            equity = float(curva["equity"].iloc[-1])
            pnl_dia = equity - float(curva["equity"].iloc[-2])
        else:
            equity, pnl_dia = cta["equity_contable"], None
        carteras.append({
            "cartera": c,
            "abiertas": len(cta["abiertas"]),
            "cerradas_hoy": int(((bit["portafolio"] == c)
                                 & (bit["fecha_salida"] == fecha_iso)).sum()),
            "pnl_dia": pnl_dia,
            "equity": equity,
            "rentabilidad": equity / cta["capital_inicial"] - 1.0,
        })
    return {"fecha": fecha_iso, "carteras": carteras,
            "marcado_a_mercado": hay_marca}


def sesiones_hasta(fecha_iso: str, precios: dict, n: int = 2) -> list[str]:
    """Las `n` últimas sesiones con datos hasta `fecha_iso`, inclusive."""
    for df in precios.values():
        fechas = [d.date().isoformat() for d in df.index
                  if d.date().isoformat() <= fecha_iso]
        if len(fechas) >= n:
            return fechas[-n:]
    return [fecha_iso]


# --------------------------------------------------------------------------- #
# Evento: alerta del vigilante
# --------------------------------------------------------------------------- #
def evento_alerta(fecha_iso: str, registro: dict, problema: str, url: str,
                  prueba=False) -> int:
    ident = notif.identificador(fecha_iso, "alerta", prueba=prueba)
    texto = notif.msg_alerta_sistema({"problema": problema, "url": url}, prueba)
    return 1 if notif.enviar_una(registro, ident, texto) else 0


# --------------------------------------------------------------------------- #
# Evento: prueba (un mensaje de cada tipo)
# --------------------------------------------------------------------------- #
def ejemplos(fecha_iso: str) -> list[tuple[str, str]]:
    """Un mensaje de cada tipo con datos inventados y evidentes.

    Los números son deliberadamente redondos y el ticker no existe: nadie debe
    poder confundir una prueba con una orden real, ni siquiera leyendo deprisa.
    """
    limite = dia_limite(fecha_iso)
    return [
        ("orden_compra", notif.msg_orden_compra({
            "cartera": "A", "ticker": "TEST", "referencia": 100.00,
            "importe": 500.00, "acciones": 5.0, "objetivo": 110.00,
            "stop": 92.00, "fecha_limite": limite, "proba": 0.850,
            "score_fundamental": 70.0}, prueba=True)),
        ("entrada", notif.msg_entrada_confirmada({
            "cartera": "A", "ticker": "TEST", "precio": 101.00,
            "hora": f"{fecha_iso} 09:30:00 EDT", "importe": 500.00,
            "acciones": 4.9505, "objetivo": 111.10, "stop": 92.92,
            "fecha_limite": limite}, prueba=True)),
        ("objetivo", notif.msg_objetivo_actualizado({
            "cartera": "A", "ticker": "TEST", "anterior": 111.10,
            "nuevo": 115.00, "motivo": "recálculo: ATR=3.20, resistencia=115.00"},
            prueba=True)),
        ("venta", notif.msg_venta({
            "cartera": "A", "ticker": "TEST", "motivo": "objetivo",
            "entrada": 101.00, "salida": 115.00, "pnl_pct": 0.1386,
            "pnl_dinero": 69.31}, prueba=True)),
        ("tiempo_manana", notif.msg_salida_por_tiempo_manana({
            "cartera": "B", "ticker": "TEST", "fecha_limite": limite,
            "entrada": 101.00, "objetivo": 115.00}, prueba=True)),
        ("resumen", notif.msg_resumen_diario({
            "fecha": fecha_iso,
            "carteras": [{"cartera": "A", "abiertas": 8, "cerradas_hoy": 2,
                          "pnl_dia": 123.45, "equity": 11207.18,
                          "rentabilidad": 0.1207},
                         {"cartera": "B", "abiertas": 8, "cerradas_hoy": 1,
                          "pnl_dia": -45.67, "equity": 11172.66,
                          "rentabilidad": 0.1173}]}, prueba=True)),
        ("alerta", notif.msg_alerta_sistema({
            "problema": "EMERGENCIA: 3 runs rojos seguidos de 'Escaneo "
                        "pre-apertura' en las últimas 24 h.",
            "url": "https://github.com/sebas1331/centinela-sp500/actions/runs/0"},
            prueba=True)),
    ]


def evento_prueba(fecha_iso: str, registro: dict) -> int:
    enviados = 0
    for tipo, texto in ejemplos(fecha_iso):
        ident = notif.identificador(fecha_iso, tipo, prueba=True)
        # Una prueba se puede repetir a voluntad: se limpia su id antes.
        registro.get("enviadas", {}).pop(ident, None)
        if notif.enviar_una(registro, ident, texto):
            enviados += 1
            log(f"prueba enviada: {tipo}")
    return enviados


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="Notificaciones de Centinela SP500")
    ap.add_argument("evento", choices=["preapertura", "postcierre", "alerta", "prueba"])
    ap.add_argument("--fecha", default=None, help="fecha de sesión YYYY-MM-DD")
    ap.add_argument("--problema", default="", help="texto de la alerta")
    ap.add_argument("--url", default="", help="enlace al run de Actions")
    ap.add_argument("--prueba", action="store_true",
                    help="marca los mensajes como [PRUEBA]")
    args = ap.parse_args()

    if not notif.activas():
        # No es un fallo: es que el canal está apagado a propósito. Se dice en
        # voz alta para que nadie crea que se enviaron avisos que no salieron.
        log("notificaciones DESACTIVADAS (falta CENTINELA_NOTIF=on o los "
            "secretos). No se envía nada.")
        publicar_resultado(res.OMITIDO_SIN_NOTIFICACIONES)
        return 0

    fecha = args.fecha or fecha_de_la_sesion(args.evento)
    registro = notif.cargar_registro()
    enviadas = 0
    try:
        if args.evento == "preapertura":
            enviadas = evento_preapertura(fecha, registro, args.prueba)
        elif args.evento == "postcierre":
            enviadas = evento_postcierre(fecha, registro, args.prueba)
        elif args.evento == "alerta":
            if not args.problema:
                raise SystemExit("--problema es obligatorio para una alerta")
            enviadas = evento_alerta(fecha, registro, args.problema, args.url,
                                     args.prueba)
        else:
            enviadas = evento_prueba(fecha, registro)
    finally:
        # El registro se guarda y el resultado se publica PASE LO QUE PASE: si
        # el envío número siete revienta, los seis que sí salieron tienen que
        # quedar marcados —y commiteados— o se repetirán en el próximo peldaño
        # de la escalera de crons. El job se verá rojo igualmente, porque la
        # excepción sigue subiendo; lo que no se pierde es lo ya hecho.
        notif.guardar_registro(registro)
        publicar_resultado(res.PROCESADO if enviadas else
                           res.OMITIDO_SIN_NOTIFICACIONES)
    return 0


def fecha_de_la_sesion(evento: str) -> str:
    """La sesión que el ESCANEO acaba de procesar, no la del reloj.

    Importa de verdad. El post-cierre puede llegar tardísimo —el 2026-08-27 el
    cron de Actions se fue casi once horas— y este job corre después de él. Si
    el escaneo procesa la sesión a las 23:50 ET y el job de notificación
    arranca a las 00:05, `datetime.now()` ya dice "mañana" y no se enviaría ni
    un aviso, en verde y sin que nada lo delatara.

    El estado guarda qué sesión procesó cada escaneo, así que se lee de ahí. Si
    no hubiera marca (repositorio recién estrenado), se cae al reloj.
    """
    estado = est_mod.cargar()
    clave = {"preapertura": "ultima_preapertura",
             "postcierre": "ultima_postcierre"}.get(evento)
    sesion = estado.get(clave) if clave else None
    return sesion or pd.Timestamp.now(tz=config.TZ_ET).date().isoformat()


def publicar_resultado(resultado: str) -> None:
    """Deja el desenlace donde el workflow pueda leerlo, igual que los escaneos.

    Sin esto, el paso de commit no puede distinguir "no había nada que enviar"
    (correcto terminar sin cambios) de "se enviaron avisos pero el registro no
    llegó al repositorio" (que reenviaría todo mañana).
    """
    log(f"RESULTADO={resultado}")
    destino = os.environ.get("GITHUB_OUTPUT")
    if destino:
        with open(destino, "a", encoding="utf-8") as fh:
            fh.write(f"resultado={resultado}\n")


if __name__ == "__main__":
    sys.exit(main())
