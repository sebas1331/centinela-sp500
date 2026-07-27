#!/usr/bin/env bash
#
# Segunda opinión INDEPENDIENTE sobre si el trabajo de este run llegó al repo.
#
# Corre en un job aparte, sobre un clon recién traído de GitHub, así que no
# comprueba lo que el job del escaneo creía haber hecho sino lo que de verdad
# hay en el remoto. Existe porque un workflow verde que no escribe nada es peor
# que uno rojo: disimula el fallo.
#
# Espera en el entorno:
#   RESULTADO   - el resultado que publicó el escaneo (ver resultados.py)
#   ESTADO_JOB  - result del job del escaneo (success/failure/cancelled/skipped)
#
set -euo pipefail

resultado="${RESULTADO:-}"
estado_job="${ESTADO_JOB:-}"
autor_esperado="centinela-bot"
raiz="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "job del escaneo: '$estado_job' | resultado publicado: '$resultado'"

if [ "$estado_job" != "success" ]; then
  echo "::error::El job del escaneo terminó en '$estado_job'. No hay nada que verificar: la sesión no se procesó."
  exit 1
fi

if [ -z "$resultado" ]; then
  echo "::error::El escaneo terminó en verde pero no publicó ningún resultado. No se puede distinguir 'no tocaba trabajar' de 'se perdió el trabajo', que es exactamente el silencio que este sistema debe hacer imposible."
  exit 1
fi

legitimos="$(python3 -c 'import sys
sys.path.insert(0, sys.argv[1])
from centinela import resultados
print(" ".join(resultados.LEGITIMOS_SIN_COMMIT))' "$raiz")"

for ok in $legitimos; do
  if [ "$resultado" = "$ok" ]; then
    echo "✅ resultado '$resultado': no tocaba trabajar, no se esperaba commit. Nada que verificar."
    exit 0
  fi
done

if [ "$resultado" != "procesado" ]; then
  echo "::error::Resultado '$resultado' desconocido. Legítimos sin commit: $legitimos"
  exit 1
fi

# --- resultado = procesado: el remoto TIENE que llevar el commit de este run ---
sha="$(git rev-parse HEAD)"
autor="$(git log -1 --format='%an' "$sha")"
asunto="$(git log -1 --format='%s' "$sha")"

echo "cabeza de ${GITHUB_REF_NAME:-main} en el remoto: $sha"
echo "  autor:  $autor"
echo "  asunto: $asunto"

if [ "$autor" != "$autor_esperado" ]; then
  echo "::error::El escaneo procesó la sesión pero la cabeza de ${GITHUB_REF_NAME:-main} la firma '$autor', no '$autor_esperado'. El trabajo de este run no está en el repositorio."
  exit 1
fi

if ! git log -1 --format='%B' "$sha" | grep -q "run: ${GITHUB_RUN_ID}"; then
  echo "::error::El escaneo procesó la sesión pero la cabeza de ${GITHUB_REF_NAME:-main} ($sha) no lleva la marca del run ${GITHUB_RUN_ID}. El trabajo de este run no llegó al repositorio."
  exit 1
fi

echo "✅ verificado en el remoto: $sha lo escribió $autor en el run ${GITHUB_RUN_ID}."
echo "   ${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY:-sebas1331/centinela-sp500}/commit/$sha"
