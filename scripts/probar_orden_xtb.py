#!/usr/bin/env python
"""Prueba REAL en la demo: una acción barata con TP y SL, y su cierre.

Responde la pregunta que decide el diseño de la Cartera A: ¿acepta XTB take
profit y stop loss en acciones reales de EE.UU. por la API, o solo en CFD? La
documentación de XTB dice que en acciones al contado hay que usar órdenes
pendientes independientes (sell stop / sell limit) en vez de niveles sobre la
posición — pero la documentación describe la interfaz web, no la API, y eso hay
que comprobarlo mandando una orden.

SE EJECUTA A MANO Y CON EL MERCADO ABIERTO. La primera vez se lanzó un sábado
(2026-09-26) y el endpoint de trading devolvió una respuesta VACÍA:

    estado=ambigua  error=gRPC trade endpoint returned an empty response

Verificado después: 0 posiciones, 0 órdenes pendientes y el saldo intacto, así
que no quedó nada colgando. Pero tampoco se pudo distinguir "XTB rechaza
órdenes fuera de horario sin decirlo" de "el canal de trading no funciona",
porque todo lo demás de la cadena SÍ respondía: TGT reutilizado, JWT de trading
obtenido con ámbito de cuenta, instrumento resuelto (F.US -> 335) y cotización
disponible. De ahí que haya que repetirla en sesión.
"""
import sys, time
sys.path.insert(0, '/Users/sebastiansaltoshotmail.com/centinela-sp500')
from centinela import broker_xtb as bx

TICKER = "F.US"          # Ford: de las más baratas del universo
ACCIONES = 1

t0 = time.time()
def m(x): print(f"[{time.time()-t0:5.1f}s] {x}", flush=True)

cred = bx.Credenciales.del_llavero()
with bx.BrokerXTB(cred, demo=True) as b:
    s = b.saldo()
    m(f"ANTES — saldo {s['saldo']:,.2f} {s['divisa']} | posiciones {len(b.posiciones())}")

    q = None
    try:
        q = b._ejecutar(b._cliente.get_quote(TICKER))
    except Exception as e:
        m(f"(sin cotización en vivo: {e})")
    if q:
        m(f"cotización {TICKER}: bid {q.bid} / ask {q.ask}")
        ref = float(q.ask)
    else:
        ref = 12.0
        m(f"sin cotización (mercado cerrado); se usa referencia {ref}")

    objetivo = round(ref * 1.10, 2)
    stop = round(ref * 0.90, 2)
    m(f"COMPRANDO {ACCIONES} x {TICKER} | objetivo {objetivo} | stop {stop}")

    e = b.comprar(TICKER, ACCIONES, objetivo=objetivo, stop=stop)
    m(f"RESULTADO: estado={e.estado} precio={e.precio} orden={e.orden} error={e.error}")

    time.sleep(3)
    pos = b.posiciones()
    m(f"posiciones tras la compra: {len(pos)}")
    for p in pos:
        m(f"   {p['ticker']} x{p['acciones']} @ {p['precio_entrada']} "
          f"| STOP={p['stop']} | OBJETIVO={p['objetivo']}")
        if p['stop'] is not None or p['objetivo'] is not None:
            m("   >>> XTB SÍ ACEPTA TP/SL en esta acción real")
        else:
            m("   >>> XTB IGNORÓ el TP/SL: la posición está SIN niveles")

    ords_p = b.ordenes_pendientes()
    m(f"órdenes pendientes: {len(ords_p)}")
    for o in ords_p:
        m(f"   {o}")

    # Limpieza: cerrar la posición o cancelar la orden en cola.
    if pos:
        m(f"CERRANDO la posición de prueba...")
        c = b.vender(TICKER, ACCIONES)
        m(f"   cierre: estado={c.estado} precio={c.precio} error={c.error}")
        time.sleep(3)
        m(f"posiciones tras el cierre: {len(b.posiciones())}")
    elif e.orden:
        m(f"CANCELANDO la orden en cola {e.orden}...")
        m(f"   cancelación: {b.cancelar(e.orden)}")

    s2 = b.saldo()
    m(f"DESPUÉS — saldo {s2['saldo']:,.2f} (variación {s2['saldo']-s['saldo']:+.2f})")
