#!/bin/bash
#
# Desinstalador de Centinela SP500 del Mac. Doble clic y listo.
#
# El ejecutor pasó a GitHub Actions el 28/09/2026, así que este ordenador ya no
# pinta nada en el sistema: puede estar apagado y el trading sigue.
#
# Casi todo se limpió sin pedir nada. Lo único que queda es el despertar
# automático, porque cambiar la programación de energía es un ajuste del
# sistema y macOS exige la contraseña de administrador. Te la pedirá abajo.
#
set -uo pipefail

cd "$(dirname "$0")"
printf '\n  Centinela SP500 — desinstalación del Mac\n'
printf '  =========================================\n\n'

hecho=0

# 1. Agentes de launchd
for a in compras ventas reconcilia; do
  plist="$HOME/Library/LaunchAgents/com.centinela.$a.plist"
  if [ -f "$plist" ]; then
    launchctl unload "$plist" 2>/dev/null
    rm -f "$plist"
    printf '  ✓ agente %s eliminado\n' "$a"
    hecho=1
  fi
done

# 2. Credenciales del Llavero
for k in centinela-xtb-email centinela-xtb-cuenta centinela-xtb-password centinela-xtb-totp; do
  if security find-generic-password -s "$k" -a centinela >/dev/null 2>&1; then
    security delete-generic-password -s "$k" -a centinela >/dev/null 2>&1
    printf '  ✓ credencial %s borrada del Llavero\n' "$k"
    hecho=1
  fi
done

# 3. Sesión de XTB
for f in "$HOME/.centinela_xtb_session" "$HOME/.centinela_xtb_cookies.json"; do
  if [ -f "$f" ]; then rm -f "$f"; printf '  ✓ %s borrado\n' "$(basename "$f")"; hecho=1; fi
done

# 4. Despertares programados — LO ÚNICO que pide contraseña
if pmset -g sched | grep -qi "repeating power events" && \
   pmset -g sched | grep -qiE "wake|poweron"; then
  printf '\n  Queda el despertar automático. macOS pide tu contraseña de\n'
  printf '  administrador para cambiarlo (no se ve mientras la escribes):\n\n'
  if sudo pmset repeat cancel; then
    printf '  ✓ despertares automáticos cancelados\n'
  else
    printf '  ✗ no se pudo cancelar. Puedes hacerlo a mano en:\n'
    printf '    Ajustes del Sistema → Batería → Programar\n'
  fi
  hecho=1
fi

printf '\n  ---------------------------------------------\n'
if [ "$hecho" -eq 0 ]; then
  printf '  No había nada instalado. El Mac ya estaba limpio.\n'
else
  printf '  Listo. Comprobación:\n\n'
fi
printf '    agentes activos : %s\n' "$(launchctl list 2>/dev/null | grep -ci centinela)"
printf '    en el Llavero   : %s\n' "$(for k in centinela-xtb-email centinela-xtb-cuenta centinela-xtb-password centinela-xtb-totp; do security find-generic-password -s "$k" -a centinela >/dev/null 2>&1 && echo x; done | wc -l | tr -d ' ')"
printf '    despertares     :\n'
pmset -g sched | sed 's/^/      /'
printf '\n  El trading sigue corriendo solo en GitHub Actions.\n'
printf '  Puedes cerrar esta ventana.\n\n'
