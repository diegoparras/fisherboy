#!/usr/bin/env bash
# Levanta el stack standalone y corre las verificaciones, todo de una.
#
# Pensado para probar local (WSL/Ubuntu con el daemon de Docker): buildea la imagen, arranca
# API + worker + Redis + el sidecar bgutil, espera a que la API responda, y llama a
# check-pot.sh para el veredicto OK/FALLO. Al terminar te deja el stack ARRIBA para que pruebes
# un MP3 real en la UI; abajo te dice cómo bajarlo.
#
# Uso, desde cualquier lado (encuentra el repo solo):
#   bash scripts/up-and-check.sh
#
# La primera vez tarda varios minutos: compila la imagen e instala Chromium (~1 GB).
set -uo pipefail

# --- Ubicación y herramientas ------------------------------------------------
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || { echo "No pude entrar al repo en $ROOT"; exit 1; }

PROJECT="fisherboy"
COMPOSE_FILE="docker-compose.standalone.yml"
BASE_URL="http://localhost:8000"

# `docker compose` (nuevo) o `docker-compose` (viejo), lo que haya.
if docker compose version >/dev/null 2>&1; then DC=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then DC=(docker-compose)
else echo "No encuentro 'docker compose' ni 'docker-compose'. ¿Está el daemon corriendo?"; exit 1; fi

if ! docker info >/dev/null 2>&1; then
  echo "El daemon de Docker no responde. Arrancalo y volvé a intentar."; exit 1
fi

# --- .env: crear uno de prueba solo si no existe (no piso el tuyo) -----------
if [ ! -f .env ]; then
  echo "→ No hay .env: creo uno de prueba (FISHERBOY_OPEN_GOD=1, solo para local)."
  cat > .env <<'EOF'
APP_MODE=standalone
FISHERBOY_OPEN_GOD=1
SECRET_KEY=localtest
COOKIE_SECURE=0
EOF
else
  echo "→ Ya tenés un .env: lo respeto. Fuerzo FISHERBOY_OPEN_GOD solo para esta prueba."
fi

# Override efímero: garantiza que el chequeo de salud pueda entrar sin login, sin tocar tu .env.
# `environment` pisa a `env_file`, así que esto gana aunque tu .env no tenga el flag.
OVERRIDE="$(mktemp --suffix=.yml)"
trap 'rm -f "$OVERRIDE"' EXIT
cat > "$OVERRIDE" <<'EOF'
services:
  fisherboy-api:
    environment:
      FISHERBOY_OPEN_GOD: "1"
      COOKIE_SECURE: "0"
EOF

compose() { "${DC[@]}" -p "$PROJECT" -f "$COMPOSE_FILE" -f "$OVERRIDE" "$@"; }

# --- Levantar ----------------------------------------------------------------
echo ""
echo "→ Buildeando y levantando (la primera vez baja Chromium, ~1 GB; paciencia)…"
if ! compose up -d --build; then
  echo ""
  echo "✗ Falló el 'up'. Últimas líneas de log:"
  compose logs --tail=30
  exit 1
fi

# --- Esperar a que la API esté lista -----------------------------------------
echo ""
printf "→ Esperando a que la API responda en %s/healthz " "$BASE_URL"
ready=0
for _ in $(seq 1 60); do   # hasta ~120s
  if curl -sf --max-time 3 "$BASE_URL/healthz" >/dev/null 2>&1; then ready=1; break; fi
  printf "."; sleep 2
done
echo ""
if [ "$ready" -ne 1 ]; then
  echo "✗ La API no respondió a tiempo. Log del contenedor de la API:"
  compose logs --tail=40 fisherboy-api
  echo ""
  echo "El stack quedó arriba; podés investigar y después bajarlo con:"
  echo "  ${DC[*]} -p $PROJECT -f $COMPOSE_FILE down"
  exit 1
fi
echo "  API lista."
sleep 2   # respiro para que el sidecar bgutil termine de arrancar

# --- Verificaciones ----------------------------------------------------------
echo ""
echo "════════════════════════════════════════════════════════"
PROJECT="$PROJECT" BASE_URL="$BASE_URL" bash "$ROOT/scripts/check-pot.sh"
verdict=$?
echo "════════════════════════════════════════════════════════"

# --- Cierre ------------------------------------------------------------------
echo ""
echo "El stack quedó ARRIBA. Probá un MP3 real en:  $BASE_URL"
echo "Cuando termines, bajalo con:"
echo "  ${DC[*]} -p $PROJECT -f $COMPOSE_FILE down"
exit $verdict
