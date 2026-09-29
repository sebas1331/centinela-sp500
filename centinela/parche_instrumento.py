"""Elegir la ACCIÓN y no el CFD cuando XTB ofrece los dos con el mismo símbolo.

EL FALLO, Y CÓMO SE VEÍA
------------------------
El 2026-09-29, siete de cada ocho compras volvieron «ambiguas» con el cuerpo
vacío. Leyendo las cabeceras gRPC que el cliente descarta, el motivo era:

    grpc-status  : 3   (INVALID_ARGUMENT)
    grpc-message : Could not find instrument for id: 335

Y buscando el símbolo, la razón: XTB tiene DOS instrumentos llamados `F.US`.

    id=7813   clave=9_F.US_US_STC        Ford Motor Co
    id=335    clave=4_F.US_US_STC CFD    CLOSE ONLY / Ford Motor Co CFD

Uno es la acción al contado y el otro es su CFD, que además está en *close
only*. El cliente resuelve el símbolo así:

    for r in results:
        if r.symbol.upper() == symbol.upper():
            return r.instrument_id

es decir, **el primero que coincida**. Y el orden de esa lista cambia entre
sesiones: unas veces sale primero la acción y otras el CFD. Cuando salía el
CFD, se mandaba su id al servicio de acciones al contado —
`CashTradingNewOrderService`—, que con toda la razón contesta que no conoce ese
instrumento.

De ahí la intermitencia de 1 de cada 8, el mensaje que no encajaba con nada y
una tarde entera buscando el fallo en el sitio equivocado.

LO QUE HACE ESTE PARCHE
-----------------------
Resolver el símbolo a conciencia en vez de a suertes: entre las coincidencias
exactas, se queda con la de contado y descarta los CFD. Si no hay ninguna de
contado, **falla con un mensaje claro** en vez de mandar el CFD y esperar a ver
qué pasa: operar el instrumento equivocado es peor que no operar.

CÓMO SE DISTINGUE UNA DE OTRA
-----------------------------
Por la clave, que es lo que XTB usa de verdad: la acción es `9_SÍMBOLO_...` y
el CFD es `4_SÍMBOLO_... CFD`. El nombre también lo dice ("CFD", "CLOSE ONLY"),
pero el nombre es texto para humanos y puede cambiar de un día para otro; el
prefijo de la clave es estructura. Se miran los dos, y basta con que uno diga
que es un CFD para descartarlo.
"""
from __future__ import annotations

#: Prefijo de clave de las acciones al contado, que es lo que este sistema
#: opera. El 4 es el de los CFD.
PREFIJO_CONTADO = "9_"
#: Señales de que un instrumento no es una acción al contado.
SENALES_CFD = ("cfd", "close only")


class InstrumentoAmbiguo(Exception):
    """XTB ofrece el símbolo, pero ninguno es la acción al contado."""


def es_contado(resultado) -> bool:
    """¿Es la acción al contado y no su CFD?"""
    clave = str(getattr(resultado, "symbol_key", "") or "")
    if clave.startswith(PREFIJO_CONTADO):
        return True
    if clave and not clave.startswith(PREFIJO_CONTADO):
        return False
    # Sin clave, se cae al nombre. Peor señal —es texto para humanos— pero es
    # mejor que rendirse.
    texto = f"{getattr(resultado, 'name', '')} {getattr(resultado, 'description', '')}".lower()
    return not any(s in texto for s in SENALES_CFD)


def elegir(resultados, simbolo: str):
    """El instrumento de contado que corresponde al símbolo, o revienta.

    Se exige coincidencia EXACTA del símbolo. El cliente, cuando no encuentra
    ninguna, se conforma con el primer resultado de la búsqueda —que puede ser
    cualquier cosa que contenga esas letras— y eso es una orden sobre un
    instrumento que nadie pidió.
    """
    exactos = [r for r in resultados
               if str(getattr(r, "symbol", "")).upper() == simbolo.upper()]
    if not exactos:
        raise InstrumentoAmbiguo(
            f"XTB no tiene ningún instrumento llamado exactamente {simbolo}. "
            f"La búsqueda devolvió {len(resultados)} resultado(s) parecidos, y "
            f"operar 'el más parecido' es operar otra cosa.")

    contado = [r for r in exactos if es_contado(r)]
    if not contado:
        detalle = ", ".join(
            f"{getattr(r, 'symbol_key', '?')} (id {getattr(r, 'instrument_id', '?')})"
            for r in exactos)
        raise InstrumentoAmbiguo(
            f"De los {len(exactos)} instrumentos que XTB llama {simbolo}, "
            f"ninguno es la acción al contado: {detalle}. Probablemente solo "
            f"quede el CFD, que este sistema no opera.")
    return contado[0]


def instalar() -> bool:
    """Hace que el cliente resuelva el símbolo con criterio. True si lo hizo."""
    try:
        from xtb_api.client import XTBClient
    except ImportError:
        return False
    if getattr(XTBClient._resolve_instrument_id, "_centinela", False):
        return False

    async def resolver(self, symbol: str) -> int:
        resultados = await self._ws.search_instrument(symbol)
        elegido = elegir(resultados, symbol)
        otros = [r for r in resultados
                 if str(getattr(r, "symbol", "")).upper() == symbol.upper()
                 and r is not elegido]
        if otros:
            print(f"[instrumento] {symbol}: {len(otros) + 1} coincidencias; se "
                  f"opera id={elegido.instrument_id} "
                  f"({getattr(elegido, 'symbol_key', '?')}) y se descarta "
                  + ", ".join(f"id={getattr(r, 'instrument_id', '?')} "
                              f"({getattr(r, 'symbol_key', '?')})" for r in otros),
                  flush=True)
        return elegido.instrument_id

    resolver._centinela = True
    XTBClient._resolve_instrument_id = resolver
    return True
