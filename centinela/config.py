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
# apertura oficial (09:30 ET). Hay varios crons UTC escalonados (cubren EDT/EST
# y los retrasos del cron de Actions); el primero que caiga dentro hace el
# trabajo y los demás abortan por idempotencia.
#
# El LÍMITE SUPERIOR es ancho a propósito: el 2026-07-20 el cron de las 12:45
# UTC arrancó a las 14:57 UTC (2h12m tarde) y el escaneo murió por ventana con
# el workflow en verde. Correr "demasiado temprano" es inofensivo (los datos son
# del cierre anterior); lo que NO se puede es correr DESPUÉS de la apertura,
# porque entonces la decisión vería el precio de apertura al que luego se simula
# la compra -> look-ahead bias. Por eso el mínimo se queda en 20 min.
PREAPERTURA_MIN_ANTES = 20     # no antes de 20 min previos a la apertura
PREAPERTURA_MAX_ANTES = 240    # hasta 4 h antes: absorbe retrasos del cron

# Ventana post-cierre: procesamos si ya pasó el cierre (16:00 ET) del día.
POSTCIERRE_MIN_DESPUES = 30    # al menos 30 min tras el cierre

# Momento PREFERIDO de cada escaneo dentro de su ventana: 08:45 ET para la
# pre-apertura y 18:00 ET para el post-cierre, que es el horario con el que se
# diseñó el sistema. Solo lo usa el run que llega ANTES de tiempo para saber
# hasta cuándo dormir; el que ya llega dentro de la ventana trabaja de
# inmediato, porque con el cron de Actions retrasándose horas lo último que
# conviene es regalar minutos esperando un horario bonito.
PREAPERTURA_OBJETIVO_ANTES = 45     # 45 min antes de la apertura -> 08:45 ET
POSTCIERRE_OBJETIVO_DESPUES = 120   # 2 h después del cierre -> 18:00 ET

# Cuánto puede ESPERAR un escaneo que arrancó antes de que abriera su ventana.
#
# El 2026-07-27 los cinco disparos de la pre-apertura llegaron entre 2h14m y
# 2h55m tarde, todos pasada la apertura, y el día se perdió en verde. La
# asimetría del problema es la clave: llegar tarde es irrecuperable, llegar
# pronto no cuesta nada. Así que ahora los crons se lanzan MUY por delante y el
# que llega pronto duerme hasta que su ventana abre, en vez de morir.
#
# El tope existe para no retener el turno de concurrencia indefinidamente: si al
# agotarlo la ventana sigue sin abrir, el run termina con "omitido:antes-de-
# ventana" (verde, inofensivo) y el siguiente peldaño de la escalera lo recoge.
ESPERA_VENTANA_MAX_MIN = 120

# Margen del vigilante antes de dar por perdida una sesión: cuántas horas después
# de que TOCABA hacer el post-cierre se empieza a exigir que esté commiteado.
# Tiene que superar el peor retraso observado del cron de Actions, que el
# 2026-08-27 saltó de ~3 h a ~11 h. Con 14 h el vigilante de las 14:37 UTC sigue
# exigiendo la sesión del día anterior (pasa a serlo a las 10:30 UTC) sin dar
# falsos rojos cuando el post-cierre simplemente llegó tardísimo pero llegó.
VIGILANTE_MARGEN_HORAS = 14

# --------------------------------------------------------------------------- #
# Vigilante — detección de rachas de runs rojos
#
# El vigilante nació para detectar SILENCIOS (una sesión que nadie procesó). El
# 2026-08-27 apareció el problema simétrico: siete "Escaneo pre-apertura" rojos
# seguidos en una tarde. Cada run gritó por su cuenta, pero nadie sumaba: no
# había ninguna señal que dijera "esto no es un fallo suelto, es una racha".
# Ahora el vigilante también mira los runs recientes y, si encuentra una racha de
# rojos consecutivos del mismo workflow, la denuncia como EMERGENCIA.
# --------------------------------------------------------------------------- #
VIGILANTE_RACHA_MINIMA = 3        # a partir de cuántos rojos seguidos: emergencia
VIGILANTE_RACHA_HORAS = 24        # ventana hacia atrás en la que se buscan

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
# Cuenta simulada — de "suma de retornos" a dinero
#
# La suma de retornos responde "cuánto gana la estrategia por operación"; una
# cuenta responde "cuánto habría ganado mi dinero", que es otra pregunta y otra
# cifra: compone al cerrar y tiene un número finito de slots. Estos parámetros
# son de CONTABILIDAD (ver centinela/cuenta.py); no entran en ninguna decisión
# de trading ni en el backtest.
# --------------------------------------------------------------------------- #
#: Capital de partida de cada cartera. Es el SALDO REAL de la cuenta demo de
#: XTB, leído el 2026-09-25, para que el simulador y el broker
#: partan del mismo dinero y la comparación entre los dos signifique algo.
#:
#: Las DOS carteras usan la misma cifra aunque solo la A se opere en el broker:
#: B es puramente simulada y no consume capital real, y darle otro tamaño
#: rompería la comparación A vs B, que es el experimento.
#:
#: Con $30.000 en 20 slots ($1.500 por posición) quedan fuera 4 de las 141
#: entradas del histórico — las de SNDK por encima de $1.500 la acción, porque
#: XTB no admite fracciones por la API. Con los $10.000 anteriores quedaban
#: fuera 23. Desde $50.000 no quedaría ninguna.
CAPITAL_INICIAL_CUENTA = 30000.0
SLOTS_CUENTA = MAX_POSICIONES_ABIERTAS   # el capital se divide en tantos slots
                                         # como posiciones simultáneas admite

# Fricciones de ejecución. Se aplican SIEMPRE por lado.
COMISION_SPREAD_POR_LADO = 0.0010   # 0,10% de comisión + spread, en cada lado
# Slippage: solo en órdenes a MERCADO (compra market-on-open, stop disparado y
# venta market-on-close por tiempo). Una salida por objetivo es una orden LÍMITE
# y se ejecuta a su precio o mejor, así que no paga slippage.
SLIPPAGE_MERCADO = 0.0015           # 0,15%

# --------------------------------------------------------------------------- #
# Ejecución en el broker (XTB, cuenta DEMO)
#
# La decisión y la ejecución están separadas a propósito: los escaneos deciden
# y no saben que existe un broker; el ejecutor lee `ordenes/pendientes.json` y
# opera. Si XTB se cae, el sistema sigue decidiendo y simulando igual.
# --------------------------------------------------------------------------- #
EJECUCION_BROKER = True

#: Cartera que se opera de verdad en XTB. La otra se sigue simulando, y la
#: comparación entre las dos sigue siendo el experimento. Se eligió la A porque
#: tiene stop: su riesgo por operación está acotado por diseño, y el de la B no
#: está acotado por nada (ver centinela/riesgo.py).
CARTERA_BROKER = "A"

#: El stop de cada posición va como "Orden Stop de venta" en el servidor de
#: XTB (centinela/proteccion.py), además de vigilado. El objetivo NO: XTB solo
#: admite una orden pendiente de venta por acción (medido el 2026-10-05) y la
#: stop es la protección que importa cuando nadie mira.
STOP_EN_XTB = True

#: NÚMERO DE LA CUENTA DEMO QUE SE OPERA. El candado exige que la sesión
#: conectada sea EXACTAMENTE esta, y vive aquí —en el código versionado— y no
#: solo en las credenciales a propósito.
#:
#: La diferencia importa: las credenciales están en secrets que se pueden
#: cambiar desde la web de GitHub sin dejar rastro en ningún diff. Si el número
#: viviera solo ahí, apuntar el sistema a otra cuenta sería cuestión de editar
#: un campo. Estando aquí, hace falta un commit, con su revisión y su historia.
#:
#: PARA PASAR A DINERO REAL NO BASTA CON CAMBIAR ESTO. Ver el apartado "Antes de
#: pasar a dinero real" del README: son varios pasos deliberados y ninguno de
#: ellos debe poder hacerse por accidente.
#:
#: UNA HUELLA, NO EL NÚMERO (2026-10-02). El repositorio es público y el número
#: de la cuenta no tiene por qué estarlo. Aquí vive su huella —scrypt con sal—
#: y el número real llega por el secret `XTB_CUENTA`, que ya se usaba para el
#: login. El candado sigue anclado a un commit: cambiar el secret a otra cuenta
#: no sirve de nada sin cambiar también esta huella en el código.
#:
#: Por qué scrypt y no un hash rápido: un número de 8 cifras son 10^8
#: candidatos, y con SHA-256 se recorren en segundos. Con este coste (n=2^17,
#: ~0,2 s y 128 MB por intento) son del orden de 800 días de CPU. No es secreto
#: absoluto —nada que se pueda enumerar lo es—, pero deja de ser gratis.
#:
#: Para calcular la huella de otra cuenta:
#:   python -c "from centinela import config; print(config.huella_cuenta(N))"
SAL_CUENTA = b"centinela-sp500/candado-de-cuenta/v1"
CUENTA_DEMO_HUELLA = "faa853bbda9d5f5a52b68a70ae80f0f0a85bfd3383925c35603da7f41a5df99b"


def huella_cuenta(numero) -> str:
    """La huella de un número de cuenta, para compararla con CUENTA_DEMO_HUELLA."""
    import hashlib
    return hashlib.scrypt(str(int(numero)).encode("ascii"), salt=SAL_CUENTA,
                          n=2 ** 17, r=8, p=1, dklen=32,
                          maxmem=2 ** 28).hex()


def es_cuenta_demo(numero) -> bool:
    """¿Es este número la cuenta demo permitida? Comparación en tiempo constante."""
    import hmac
    try:
        return hmac.compare_digest(huella_cuenta(numero), CUENTA_DEMO_HUELLA)
    except (TypeError, ValueError):
        return False

#: Interruptor separado del número. Que el sistema opere en real exige cambiar
#: LAS DOS cosas, en el mismo commit y a conciencia: un despiste con una sola
#: variable no puede sacar órdenes a una cuenta con dinero.
TIPO_CUENTA_BROKER = "demo"

#: Primera sesión en la que el ejecutor pudo comprar de verdad. Las posiciones
#: que el simulador abrió ANTES de esta fecha nunca existieron en el broker,
#: porque no había ejecutor: son herencia del paper trading y se quedan solo en
#: el simulador hasta que cierren por su cuenta.
#:
#: Sin este corte, la reconciliación denuncia esas posiciones cada día como
#: "abierta en el simulador y NO en XTB" —lo hizo la primera vez que corrió, con
#: cinco— y un rojo que sale todos los días por algo que no es un fallo enseña a
#: ignorar los rojos, que es la peor avería posible en este repositorio.
EJECUCION_DESDE = "2026-09-29"

#: Con cuántas horas de margen avisa el Vigilante de que la sesión de XTB está
#: a punto de caducar. El TGT dura 8 h y renovarlo necesita a una persona
#: leyendo un código del correo, así que avisar cuando ya caducó llega tarde:
#: el aviso tiene que dar tiempo a actuar antes de que un escaneo se lo
#: encuentre cerrado.
SESION_AVISO_HORAS = 3

#: Ventanas del ejecutor, en minutos respecto de la apertura (09:30 ET) o del
#: cierre (16:00 ET) del mercado. Los tres momentos del día:
#:
#:   compras       JUSTO DESPUÉS de la apertura, nunca antes. Ver abajo.
#:   ventas        antes del cierre, para las salidas por tiempo del día 10.
#:                 Medido sobre las 89 salidas por tiempo del histórico: cerrar
#:                 al cierre se desvía 0,03 pp del simulador y hacerlo a la
#:                 apertura del día siguiente, 0,40 pp con 3,14 de dispersión
#:                 (gap overnight que la estrategia no contempla);
#:   reconcilia    después del cierre, cuando ya no puede moverse nada.
#: LAS COMPRAS SE MANDAN DESPUÉS DE ABRIR, NUNCA ANTES (fallo del 2026-09-30).
#:
#: Antes se mandaban entre 60 y 5 minutos ANTES de la apertura, con la idea de
#: que XTB las dejara en cola y las ejecutara al abrir — que es lo que simula la
#: estrategia. XTB sí las encola… y luego las descarta. El 30/09 se mandaron
#: MRNA y FICO a las 08:47 ET, XTB devolvió "en cola" para las dos, y en
#: xStation 5 las dos figuran como RECHAZADO. El sistema anotó "en_cola" y no
#: volvió a preguntar, así que la página dijo "todo en orden" con cero compras.
#:
#: Comprobado a propósito el 01/10 a las 23:15 ET, con el mercado cerrado: XTB
#: aceptó una compra de 1 acción y devolvió "en cola" (orden 916785162); tres
#: minutos después no había ni posición ni orden. Acepta, encola y descarta.
#:
#: Así que la ventana pasa a ser DESPUÉS de abrir. Se pierden los segundos que
#: van de la apertura al envío —y eso se mide, por eso las entradas tardías
#: tienen tipo propio— pero una orden que se ejecuta de verdad vale más que una
#: que encaja perfecto con el simulador y no existe.
#:
#: Y NO EN LOS PRIMEROS SEGUNDOS (decisión del 2026-10-05). Las dos compras
#: mandadas nada más abrir —CTVA a las 09:30:19 del 02/10 y WDC a las 09:30:31
#: del 05/10— XTB las aceptó y las descartó; las mandadas de 09:52 a 10:06
#: entraron. Con dos casos es una hipótesis, no una ley: se espera 5 minutos y,
#: si una compra a las 09:35 vuelve a perderse, la hipótesis queda descartada.
EJECUTOR_COMPRAS_MIN_TRAS_APERTURA = (5, 60)       # de 5 a 60 min tras abrir
#: Cuánto puede dormir el ejecutor de compras esperando a que abra. El
#: pre-apertura procesa hasta 4 h antes de la apertura (PREAPERTURA_MAX_ANTES),
#: y su ejecutor es el ÚNICO que compra ese día: los peldaños siguientes de la
#: escalera salen "ya procesado" y no lo relanzan. Con los 120 min de los
#: escaneos, un pre-apertura de las 06:00 ET agotaba la espera a las 08:00 y el
#: día se quedaba sin compras — en verde. 250 cubre las 4 h con margen; el job
#: tiene un timeout por encima.
EJECUTOR_COMPRAS_ESPERA_MAX_MIN = 250
EJECUTOR_VENTAS_MIN_ANTES_CIERRE = (5, 30)         # entre 30 y 5 min antes
EJECUTOR_RECONCILIA_MIN_DESPUES_CIERRE = 30        # al menos 30 min después

#: LAS VENTAS POR TIEMPO LAS HACE EL VIGILANTE DE PRECIOS (2026-10-02). El
#: workflow de ventas por cron no llegó NI UNA VEZ a su ventana en la semana
#: del 28/09: los crons de este repositorio llegan con 4 a 7 horas de retraso.
#: El vigilante está vivo toda la sesión, así que no depende de ningún cron:
#: vende a esta distancia del cierre, dentro de la ventana de arriba. El cron
#: queda de respaldo y solo actúa si el vigilante no lo hizo.
VIGILANTE_TIEMPO_MIN_ANTES_CIERRE = 15
#: Cada cuánto relee el vigilante las posiciones de XTB, además de tras cada
#: venta. El 2026-10-02 el latido siguió anunciando CTVA seis minutos después
#: de venderla porque la lista solo se leía al arrancar.
VIGILANTE_REFRESCO_SEG = 180
#: Un vigilante que arranca antes de la apertura espera a que abra solo si
#: falta menos que esto; si falta más, se va en reposo y lo arranca el job de
#: compras, que corre después de abrir. Vigilar el pre-mercado no sirve: XTB
#: encola las órdenes de mercado con la sesión cerrada y luego las descarta.
VIGILANTE_ESPERA_APERTURA_MAX_MIN = 45

#: Ventana de la verificación posterior a la apertura, en minutos DESPUÉS de
#: abrir. No decide nada: mira si se ejecutó lo que se mandó y a qué precio.
#:
#: El suelo son 30 minutos porque una compra a mercado encolada antes de abrir
#: no tiene precio real hasta que el libro se estabiliza, y preguntarlo a los
#: cinco minutos da una foto que cambia sola. El techo son 90 porque a partir
#: de ahí el precio de ejecución ya no se distingue del movimiento del día y la
#: comparación con el open deja de medir lo que se quiere medir.
VERIFICACION_MIN_TRAS_APERTURA = (30, 90)

# --------------------------------------------------------------------------- #
# Fuente del universo
# --------------------------------------------------------------------------- #
WIKIPEDIA_SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
