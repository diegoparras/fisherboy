#!/usr/bin/env bash
# Verifica que el stack de descarga de video quedó bien montado y endurecido.
#
# Chequea cuatro cosas, cada una con veredicto OK/FALLO:
#   1. Los cuatro contenedores están arriba.
#   2. La cadena PO token anda de punta a punta (Fisherboy → bgutil → versiones que coinciden),
#      vía GET /api/download/pot/health.
#   3. El aislamiento de red es real: bgutil NO comparte red con Redis.
#   4. El hardening quedó aplicado al contenedor de bgutil (cap_drop ALL + no-new-privileges).
#
# No baja ningún video (eso se prueba a mano en la UI, y puede fallar por IP aunque todo esté
# bien). Solo valida la infraestructura.
#
# Uso, desde la raíz del repo y con el stack levantado:
#   docker compose -p fisherboy -f docker-compose.standalone.yml up -d --build
#   bash scripts/check-pot.sh
#
# Variables (opcionales):
#   PROJECT   nombre de proyecto de compose (default: fisherboy)
#   BASE_URL  dónde escucha la API           (default: http://localhost:8000)
set -uo pipefail

PROJECT="${PROJECT:-fisherboy}"
BASE_URL="${BASE_URL:-http://localhost:8000}"
API="${PROJECT}-api"; WORKER="${PROJECT}-worker"
REDIS="${PROJECT}-redis"; BGUTIL="${PROJECT}-bgutil"
NET_MAIN="${PROJECT}_fisherboy"; NET_POT="${PROJECT}_pot"

fails=0
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31m✗ %s\033[0m\n' "$1"; fails=$((fails+1)); }
head() { printf '\n\033[1m%s\033[0m\n' "$1"; }

# --- 1. Contenedores arriba --------------------------------------------------
head "1. Contenedores"
running() { [ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" = "true" ]; }
for c in "$API" "$WORKER" "$REDIS" "$BGUTIL"; do
  if running "$c"; then ok "$c arriba"; else bad "$c NO está corriendo"; fi
done

# --- 2. La cadena del PO token, de punta a punta -----------------------------
head "2. Salud del proveedor de PO tokens (/api/download/pot/health)"
health="$(curl -s --max-time 8 "$BASE_URL/api/download/pot/health" 2>/dev/null)"
if [ -z "$health" ]; then
  bad "la API no respondió en $BASE_URL (¿levantó? ¿FISHERBOY_OPEN_GOD=1 en el .env?)"
else
  echo "     respuesta: $health"
  case "$health" in
    *'"ok":true'*|*'"ok": true'*) ok "bgutil responde y es alcanzable desde la API" ;;
    *) bad "el proveedor no está sano (revisá YT_POT_URL y que $BGUTIL esté vivo)" ;;
  esac
  case "$health" in
    *'"mismatch":true'*|*'"mismatch": true'*)
      bad "plugin y sidecar en majors distintas → redeployá para alinearlos" ;;
    *'"mismatch":false'*|*'"mismatch": false'*)
      ok "las versiones de plugin y sidecar coinciden" ;;
  esac
fi

# --- 3. Aislamiento de red ---------------------------------------------------
head "3. Aislamiento de red (bgutil no debe ver a Redis)"
members() { docker network inspect "$1" \
  --format '{{range .Containers}}{{.Name}} {{end}}' 2>/dev/null; }
main_net="$(members "$NET_MAIN")"; pot_net="$(members "$NET_POT")"
if [ -z "$pot_net" ]; then
  bad "la red $NET_POT no existe (¿usaste -p $PROJECT al levantar?)"
else
  case " $pot_net " in *" $BGUTIL "*) ok "$BGUTIL está en la red $NET_POT" ;;
    *) bad "$BGUTIL no está en $NET_POT" ;; esac
  case " $pot_net " in *" $API "*) ok "la API alcanza a bgutil por $NET_POT" ;;
    *) bad "la API no está en $NET_POT (no podría pedir tokens)" ;; esac
fi
case " $main_net " in
  *" $BGUTIL "*) bad "$BGUTIL comparte la red $NET_MAIN con Redis (NO aislado)" ;;
  *)            ok "$BGUTIL NO está en $NET_MAIN: no ve a Redis" ;;
esac

# --- 4. Hardening del contenedor ---------------------------------------------
head "4. Hardening de $BGUTIL"
caps="$(docker inspect -f '{{.HostConfig.CapDrop}}' "$BGUTIL" 2>/dev/null)"
secopt="$(docker inspect -f '{{.HostConfig.SecurityOpt}}' "$BGUTIL" 2>/dev/null)"
case "$caps" in   *ALL*) ok "cap_drop: ALL" ;; *) bad "sin cap_drop ALL (es: ${caps:-vacío})" ;; esac
case "$secopt" in *no-new-privileges*) ok "no-new-privileges activo" ;;
  *) bad "sin no-new-privileges (es: ${secopt:-vacío})" ;; esac
# Confirmá que quedó fijado por digest (inmutable), no por tag móvil.
img="$(docker inspect -f '{{.Config.Image}}' "$BGUTIL" 2>/dev/null)"
case "$img" in *@sha256:*) ok "imagen fijada por digest" ;;
  *) bad "la imagen NO está fijada por digest: ${img:-desconocida}" ;; esac

# --- Veredicto ---------------------------------------------------------------
if [ "$fails" -eq 0 ]; then
  printf '\n\033[1;32m✓ TODO OK\033[0m — el stack está sano y endurecido.\n'
  printf '  Ahora probá un MP3 real en %s (puede pedir cookies si tu IP está bloqueada).\n' "$BASE_URL"
  exit 0
else
  printf '\n\033[1;31m✗ %d CHEQUEO(S) FALLARON\033[0m — mirá los ✗ de arriba.\n' "$fails"
  exit 1
fi
