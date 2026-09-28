# El Mac ya no pinta nada aquí

**Desde el 28 de septiembre de 2026 el ejecutor corre en GitHub Actions.** Este
ordenador puede estar apagado, sin red o en otro país: el sistema decide,
ejecuta en la demo de XTB y publica el panel igual.

## Por qué estuvo aquí, y por qué ya no

El ejecutor nació en un Mac con `launchd` por una razón concreta: las
credenciales de XTB abren **también las cuentas reales** del titular, y ponerlas
en los secrets de un repositorio remoto no compensaba. Cuando quedó claro que
esas cuentas reales están vacías, ese motivo desapareció.

Quedaba la otra duda —si el login de xStation5 funciona desde una IP de
datacenter, que es justo lo que su WAF existe para frenar— y se comprobó con
`probar_broker.yml`:

| Prueba | Resultado |
|---|---|
| Solo lectura, mercado recién cerrado | login en 11,5 s · saldo leído |
| Compra y cierre reales | **2,1 s** reutilizando sesión · orden 915795796 · cuenta sin residuos |

Los 2,1 segundos son el dato que lo decide: la sesión guardada en
`actions/cache` evita que XTB pida el código por correo en cada ejecución.

## Si queda algo instalado

Doble clic en **`Desinstalar-Centinela.command`** (hay una copia en el
Escritorio). Quita los agentes de `launchd`, las credenciales del Llavero, la
sesión de XTB y los despertares programados. Solo pide la contraseña de
administrador para lo último, porque cambiar la programación de energía es un
ajuste del sistema.

## Si algún día hay que volver

El historial de `mac/` tiene los agentes de `launchd` y el script de `pmset`
que funcionaban, con sus horarios dobles para el cambio de horario de EE.UU.
Se recuperan con `git log -- mac/`.
