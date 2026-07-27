#!/usr/bin/env bash
#
# Persiste en el repositorio lo que produjo un escaneo, y ROMPE EN ROJO si no lo
# consigue.
#
# Uso: commit_y_push.sh <mensaje> <resultado> <ruta>...
#
# El <resultado> lo publica el propio escaneo (ver centinela/resultados.py) y es
# lo único que distingue "no tocaba trabajar" de "el sistema perdió su trabajo":
#
#   procesado                 -> el escaneo hizo trabajo real: TIENE que haber
#                                cambios, commit y push. Si falta alguno, ROJO.
#   omitido:<motivo legítimo> -> no tocaba trabajar: no haber cambios es normal.
#   cualquier otra cosa       -> ROJO.
#
# Esa última línea es la lección del 2026-07-27: antes bastaba con que el
# resultado empezara por "omitido:" para dar el día por bueno, así que perder la
# ventana de decisión de una sesión entera salía en verde. Ahora la lista de
# motivos legítimos es CERRADA, vive en un solo sitio (resultados.py) y se lee
# de ahí, así que no puede desincronizarse ni crecer por descuido.
#
set -euo pipefail

if [ "$#" -lt 3 ]; then
  echo "::error::Uso: commit_y_push.sh <mensaje> <resultado> <ruta>..." >&2
  exit 1
fi

mensaje="$1"
resultado="$2"
shift 2

rama="${GITHUB_REF_NAME:-main}"
autor_nombre="centinela-bot"
autor_email="actions@users.noreply.github.com"
raiz="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

legitimos="$(python -c 'import sys
sys.path.insert(0, sys.argv[1])
from centinela import resultados
print(" ".join(resultados.LEGITIMOS_SIN_COMMIT))' "$raiz")"

if [ -z "$resultado" ]; then
  echo "::error::El escaneo no publicó ningún resultado. Sin él no se puede saber si terminar sin commit es legítimo, así que se asume lo peor."
  exit 1
fi

es_legitimo="no"
for ok in $legitimos; do
  [ "$resultado" = "$ok" ] && es_legitimo="si"
done

if [ "$resultado" != "procesado" ] && [ "$es_legitimo" = "no" ]; then
  echo "::error::Resultado '$resultado' desconocido. Los únicos que permiten terminar sin commit son: $legitimos"
  exit 1
fi

git config user.name "$autor_nombre"
git config user.email "$autor_email"

# Sin "|| true": si una de estas rutas desapareciera queremos enterarnos, no
# acabar con un índice vacío que se lee igual que "no hubo cambios".
git add -- "$@"

if git diff --cached --quiet; then
  if [ "$resultado" = "procesado" ]; then
    echo "::error::El escaneo procesó la sesión (resultado=procesado) pero no dejó ningún cambio en disco. Nada que commitear = el sistema perdió su trabajo."
    exit 1
  fi
  echo "Sin cambios que commitear, y es legítimo (resultado=$resultado)."
  exit 0
fi

git commit -m "$mensaje" \
           -m "run: ${GITHUB_RUN_ID:-local} | workflow: ${GITHUB_WORKFLOW:-local} | resultado: $resultado"

# El push puede chocar con otro workflow que haya escrito entretanto (el grupo
# de concurrencia lo hace improbable, no imposible). Rebase y reintento.
publicado=""
for intento in 1 2 3; do
  if git push origin "HEAD:$rama"; then
    publicado="si"
    break
  fi
  echo "push rechazado (intento $intento/3); rebase sobre origin/$rama y reintento..."
  # Sin "|| true" estos comandos abortarían el script bajo `set -e` en cuanto la
  # red fallase, saltándose los reintentos y el mensaje de error de abajo: el
  # job moriría igualmente en rojo, pero con un `fatal:` de git en vez de una
  # explicación. Se dejan fallar y que decida el bucle.
  git fetch origin "$rama" || true
  git rebase "origin/$rama" || git rebase --abort || true
  sleep 5
done

if [ -z "$publicado" ]; then
  echo "::error::git push falló tras 3 intentos. Los cambios de esta sesión NO están en el repositorio."
  exit 1
fi

# ---------------------------------------------------------------------------
# Verificación dura contra el REMOTO. No basta con que el push devuelva 0: se
# relee origin y se comprueba que el commit está ahí, que lo firma el bot y que
# pertenece a ESTE run. Es lo que convierte "creo que se guardó" en evidencia.
# ---------------------------------------------------------------------------
sha="$(git rev-parse HEAD)"
git fetch origin "$rama"

if ! git merge-base --is-ancestor "$sha" "origin/$rama"; then
  echo "::error::El commit $sha no aparece en origin/$rama tras el push. Persistencia fallida."
  exit 1
fi

autor_real="$(git log -1 --format='%an' "$sha")"
if [ "$autor_real" != "$autor_nombre" ]; then
  echo "::error::El commit $sha figura a nombre de '$autor_real' y no de '$autor_nombre'. Escribió en el repo algo que no era el bot."
  exit 1
fi

if [ -n "${GITHUB_RUN_ID:-}" ]; then
  if ! git log -1 --format='%B' "$sha" | grep -q "run: ${GITHUB_RUN_ID}"; then
    echo "::error::El commit $sha no lleva la marca del run ${GITHUB_RUN_ID}: la cabeza del remoto no es la que publicó este run."
    exit 1
  fi
fi

echo "✅ commit $sha publicado y VERIFICADO en origin/$rama (autor=$autor_real, run=${GITHUB_RUN_ID:-local})"
echo "   ${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY:-sebas1331/centinela-sp500}/commit/$sha"
git --no-pager show --stat --oneline -s "$sha"
