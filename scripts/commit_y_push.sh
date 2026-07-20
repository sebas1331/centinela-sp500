#!/usr/bin/env bash
#
# Persiste en el repositorio lo que produjo un escaneo, y ROMPE EN ROJO si no lo
# consigue. Antes esto era un bloque suelto en cada workflow que terminaba en
# "sin cambios que commitear" y salía en verde tanto si no tocaba trabajar como
# si el sistema había trabajado y perdido su salida. Los dos casos ahora se
# distinguen con el resultado que publica el propio escaneo.
#
# Uso: commit_y_push.sh <mensaje> <resultado> <ruta>...
#
#   resultado = "procesado"   -> el escaneo hizo trabajo real: TIENE que haber
#                                cambios, commit y push. Si falta alguno, error.
#   resultado = "omitido:..." -> no tocaba trabajar: no haber cambios es normal.
#
set -euo pipefail

mensaje="$1"
resultado="$2"
shift 2

rama="${GITHUB_REF_NAME:-main}"

git config user.name "centinela-bot"
git config user.email "actions@users.noreply.github.com"

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

git commit -m "$mensaje"

# El push puede chocar con otro workflow que haya escrito entretanto (el grupo
# de concurrencia lo hace improbable, no imposible). Rebase y reintento.
publicado=""
for intento in 1 2 3; do
  if git push origin "HEAD:$rama"; then
    publicado="si"
    break
  fi
  echo "push rechazado (intento $intento/3); rebase sobre origin/$rama y reintento..."
  git fetch origin "$rama"
  git rebase "origin/$rama"
  sleep 5
done

if [ -z "$publicado" ]; then
  echo "::error::git push falló tras 3 intentos. Los cambios de esta sesión NO están en el repositorio."
  exit 1
fi

# Verificación dura: no basta con que el push devuelva 0, el commit tiene que
# estar realmente en el remoto.
sha="$(git rev-parse HEAD)"
git fetch origin "$rama"
if ! git merge-base --is-ancestor "$sha" "origin/$rama"; then
  echo "::error::El commit $sha no aparece en origin/$rama tras el push. Persistencia fallida."
  exit 1
fi

echo "✅ commit $sha publicado en origin/$rama"
git --no-pager show --stat --oneline -s "$sha"
