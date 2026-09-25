# El ejecutor en el Mac

Aquí vive todo lo que hace falta para que este Mac mande las órdenes a XTB tres
veces por día de mercado, se despierte solo y publique lo que hizo.

## Por qué en el Mac y no en GitHub Actions

Dos razones, y la primera pesa más:

1. **Las credenciales de XTB abren también la cuenta real.** XTB no da
   credenciales separadas para la demo: el mismo email y la misma contraseña
   entran en las dos. Ponerlas en los secrets de un repositorio remoto era un
   riesgo que no compensaba por correr en la nube. Aquí viven en el **Llavero
   de macOS** y no salen de este ordenador.
2. **El login de xStation5 pasa por un WAF** que bloquea el acceso
   automatizado; las IPs de datacenter de GitHub son justo lo que ese WAF
   existe para frenar.

## Los tres momentos del día

| Agente | Qué hace | Cuándo (ET) |
|---|---|---|
| `compras` | Manda las compras decididas hoy. XTB las deja **en cola** y las ejecuta al abrir. | 60–5 min antes de la apertura |
| `ventas` | Cierra las posiciones que cumplen su décima sesión. | 30–5 min antes del cierre |
| `reconcilia` | Compara XTB con el simulador y rompe en rojo si difieren. | ≥30 min tras el cierre |

**Por qué las ventas van pegadas al cierre y no al día siguiente:** medido sobre
las 89 salidas por tiempo del histórico, cerrar al cierre del día 10 se desvía
0,03 pp del simulador; hacerlo a la apertura del día 11 se desvía 0,40 pp pero
con 3,14 pp de dispersión — ruido de gap overnight que la estrategia no
contempla y que no compensa ahorrarse un despertar.

## Por qué cada agente tiene DOS horas

`launchd` programa en **hora local y no entiende de husos**: si le dices "a las
08:25" seguirá disparando a las 08:25 de Guayaquil cuando Nueva York cambie al
horario de invierno y la apertura se mueva una hora. Así que cada agente
dispara a las dos horas posibles (verano y invierno) y el ejecutor comprueba
por su cuenta, en hora ET, si está dentro de su ventana. El disparo que caiga
fuera se calla en verde.

Es exactamente la escalera de crons que ya usan los escaneos en Actions, y por
la misma razón.

## Credenciales en el Llavero

Cuatro secretos, todos bajo la cuenta `centinela`:

| Servicio | Qué es |
|---|---|
| `centinela-xtb-email` | El email de la cuenta de XTB |
| `centinela-xtb-cuenta` | El número de la cuenta **demo** (solo dígitos) |
| `centinela-xtb-password` | La contraseña |
| `centinela-xtb-totp` | El secreto TOTP en base32, si hay segundo factor |

El último **no es opcional si la cuenta tiene 2FA**: sin él el login muere en
`CASError: 2FA is required but no totp_secret was provided`. Se obtiene
reconfigurando el segundo factor en XTB y copiando la clave que aparece junto
al código QR (la opción de "introducir manualmente"). Reconfigurarlo invalida
la app de autenticación anterior, así que hay que volver a escanear el QR
también ahí.

```bash
security add-generic-password -U -s "centinela-xtb-totp" -a centinela -w
```

Sin `-w` con valor: así la pide por teclado y no queda en el historial.

## Instalación

```bash
# 1. Dependencias del broker (una vez)
cd ~/centinela-sp500
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium          # el login de XTB necesita un navegador

# 2. Instalar los tres agentes
cp mac/com.centinela.*.plist ~/Library/LaunchAgents/
for a in compras ventas reconcilia; do
  launchctl unload ~/Library/LaunchAgents/com.centinela.$a.plist 2>&1 | true
  launchctl load ~/Library/LaunchAgents/com.centinela.$a.plist
done
launchctl list | grep centinela      # deben aparecer los tres
```

## Despertar el Mac solo

`launchd` no despierta el ordenador por su cuenta: si está dormido, el disparo
se pierde. `pmset` sí lo despierta, y hay que programarlo aparte. Ver
`mac/programar_despertares.sh`.

## Logs

Cada agente escribe en `mac/logs/`. Un fallo del ejecutor sale ahí y, además,
el Vigilante de GitHub lo detecta al día siguiente porque falta el commit del
Mac.
