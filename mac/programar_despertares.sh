#!/usr/bin/env bash
#
# Programa los despertares automáticos del Mac para que el ejecutor encuentre el
# ordenador encendido a sus horas.
#
# POR QUÉ HACE FALTA: launchd NO despierta el ordenador. Si el Mac está dormido
# cuando toca un disparo, ese disparo se pierde sin más — y con él, las compras
# del día. `pmset` sí lo despierta, pero se programa aparte y por eso existe
# este script.
#
# Se programa unos minutos ANTES de cada agente para que al ordenador le dé
# tiempo a despertar, levantar la wifi y montar lo que tenga que montar.
#
# Requiere sudo: cambiar la programación de energía es un ajuste del sistema.
#
set -euo pipefail

echo "Programando despertares para el ejecutor de Centinela..."
echo "(se te pedirá la contraseña de administrador)"
echo

# `repeat` sustituye TODA la programación repetitiva anterior, así que los tres
# horarios van en una sola orden. Se usa el más temprano de los dos husos
# (verano) y se deja el Mac despierto el rato suficiente para cubrir el otro.
#
#   08:00  -> agente `compras`     (dispara 08:05 en verano, 09:05 en invierno)
#   14:40  -> agente `ventas`      (14:45 / 15:45)
#   15:40  -> agente `reconcilia`  (15:45 / 16:45)
#
# Cada despertar cubre su par de horas porque el Mac se queda despierto: lo que
# importa es que no esté dormido cuando launchd dispare.
sudo pmset repeat wakeorpoweron MTWRF 08:00:00

echo
echo "Hecho. Comprobación:"
pmset -g sched
echo
cat <<'AVISO'
IMPORTANTE — dos ajustes que tienes que hacer A MANO (System Settings):

1. Ajustes del Sistema -> Batería -> Opciones:
   · "Evitar que el Mac se duerma automáticamente con la pantalla apagada" ON
     (si es un portátil, esto solo aplica con el cargador enchufado)
   · "Activar Power Nap" ON

2. Si es un MacBook y lo usas con la tapa CERRADA:
   macOS ignora los despertares programados con la tapa cerrada salvo que esté
   conectado a la corriente Y a una pantalla externa. Sin pantalla externa, la
   única forma fiable es dejar la tapa abierta (puedes apagar la pantalla).

3. Deja el Mac ENCHUFADO a la corriente. Con batería, macOS ignora buena parte
   de los despertares programados para ahorrar energía.

Un despertar perdido no rompe nada: el ejecutor no manda las órdenes de ese día
y el Vigilante lo denuncia al día siguiente. Pero ese día no se opera.
AVISO
