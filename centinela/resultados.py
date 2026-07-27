"""Vocabulario CERRADO de desenlaces de un escaneo.

Cada escaneo termina publicando un RESULTADO que el workflow lee para decidir si
la ausencia de commit es normal o es un fallo que debe verse rojo.

Este módulo existe por el fallo del 2026-07-27. Ese día el cron de Actions se
retrasó entre 2h14m y 2h55m, los cinco disparos de la pre-apertura aterrizaron
DESPUÉS de la apertura, los cinco devolvieron "omitido:fuera-de-ventana" y el
paso de commit trataba cualquier "omitido:*" como legítimo. Resultado: cinco
workflows en verde, cero escritura, y un día bursátil entero perdido sin que
nada lo delatara.

Dos reglas para que eso no pueda repetirse:

  1. "llegué pronto" y "llegué tarde" son desenlaces DISTINTOS. Llegar pronto es
     inofensivo (los datos son del cierre anterior y otro disparo vendrá
     después); llegar tarde es perder el día, porque la decisión ya no puede
     tomarse antes del open sin caer en look-ahead bias.
  2. La lista de resultados legítimos es CERRADA. Cualquier resultado que no
     esté aquí se trata como fallo. Añadir un motivo nuevo obliga a decidir
     explícitamente si puede terminar sin commit; no se hereda el silencio.
"""
from __future__ import annotations

# El escaneo hizo trabajo real: TIENE que haber cambios en disco, commit y push.
PROCESADO = "procesado"

# No tocaba trabajar. Terminar sin cambios que commitear es correcto.
OMITIDO_SIN_MERCADO = "omitido:sin-mercado"            # hoy la bolsa no abre
OMITIDO_YA_PROCESADO = "omitido:ya-procesado"          # otro disparo de hoy ya lo hizo
OMITIDO_ANTES_DE_VENTANA = "omitido:antes-de-ventana"  # aún no toca; vendrá otro disparo

#: Únicos resultados que permiten terminar en verde sin commit.
LEGITIMOS_SIN_COMMIT = (
    OMITIDO_SIN_MERCADO,
    OMITIDO_YA_PROCESADO,
    OMITIDO_ANTES_DE_VENTANA,
)

# Fallos. El escaneo sale con código 1 y el workflow se ve ROJO.
FALLO_VENTANA_PERDIDA = "fallo:ventana-perdida"    # se pasó el techo de la ventana
FALLO_SESION_PENDIENTE = "fallo:sesion-pendiente"  # quedó una sesión anterior sin procesar

#: Explicación que se imprime como ::error:: al fallar.
MOTIVOS_FALLO = {
    FALLO_VENTANA_PERDIDA: (
        "El escaneo arrancó DESPUÉS del techo de su ventana y hoy nadie lo había "
        "procesado todavía: la sesión se pierde. Casi siempre es retraso del cron "
        "de GitHub Actions; hay que adelantar o densificar la escalera de crons."
    ),
    FALLO_SESION_PENDIENTE: (
        "Quedó una sesión bursátil anterior sin procesar. El sistema se saltó un "
        "día y su bitácora tiene un agujero."
    ),
}


def es_fallo(resultado: str) -> bool:
    return resultado.startswith("fallo:")


def es_legitimo_sin_commit(resultado: str) -> bool:
    return resultado in LEGITIMOS_SIN_COMMIT
