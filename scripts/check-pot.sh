#!/usr/bin/env bash
# Verifica que el stack de descarga de video quedó bien montado y endurecido.
#
# Chequea cuatro cosas, cada una con veredicto OK/FALLO:
#   1. Los cuatro contenedores están arriba.
#   2. La cadena PO token anda de punta a punta (Fisherboy → bgutil → versiones que coinciden),
#      vía GET /api/download/pot/health.
#   3. El aislamiento de red es real: bgutil NO comparte red con Redis.
#   4. El hardening quedó aplicado al contenedor de bgutil (cap_drop ALL + no-new-privileges +
#      imagen fijada por digest).
#
# No baja ningún video (eso se prueba a mano en la UI, y puede fallar por IP aunque todo esté
# bien). Solo valida la infraestructura.
#
# Resuelve los contenedores por LABEL de compose (no por nombre): así funciona sin importar
# cómo compose combine proyecto + servicio + réplica en el nombre final.
#
# Uso, con el stack levantado:
#   PROJECT=fisherboy bash scripts/check-pot.sh
#
# Variables (opcionales):
#   PROJECT   nombre de proyecto de compose (default: fisherboy)
#   BASE_URL  dónde escucha la API           (default: http://localhost:8000)
set -uo pipefail

PROJECT="${PROJECT:-fisherboy}"
BASE_URL="${BASE_URL:-http://localhost:8000}"
NET_MAIN="${PROJECT}_fisherboy"   # la red 'fisherboy' del compose, prefijada por el proyecto
NET_POT="${PROJECT}_pot"          # la red dedicada 'pot'

fails=0
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31m✗ %s\033[0m\n' "$1"; fails=$((fails+1)); }
sect() { printf '\n\033[1m%s\033[0m\n' "$1"; }

# ID del contenedor de un servicio de compose, vía sus labels (a prueba de nombres).
cid() {
  docker ps -aq \
    --filter "label=com.docker.compose.project=${PROJECT}" \
    --filter "label=com.docker.compose.service=$1" 2>/dev/null | head -1
}

# --- 1. Contenedores arriba --------------------------------------------------
sect "1. Contenedores"
declare -A CID
for svc in fisherboy-api fisherboy-worker fisherboy-redis fisherboy-bgutil; do
  id="$(cid "$svc")"
  CID[$svc]="$id"
  if [ -n "$id" ] && [ "$(docker inspect -f '{{.State.Running}}' "$id" 2>/dev/null)" = "true" ]; then
    ok "$svc arriba"
  else
    bad "$svc NO está corriendo"
  fi
done
BG="${CID[fisherboy-bgutil]}"; API="${CID[fisherboy-api]}"

# --- 2. La cadena del PO token, de punta a punta -----------------------------
sect "2. Salud del proveedor de PO tokens (/api/download/pot/health)"
health="$(curl -s --max-time 8 "$BASE_URL/api/download/pot/health" 2>/dev/null)"
if [ -z "$health" ]; then
  bad "la API no respondió en $BASE_URL (¿levantó? ¿FISHERBOY_OPEN_GOD=1 en el .env?)"
else
  echo "     respuesta: $health"
  case "$health" in *'"ok":true'*|*'"ok": true'*) ok "bgutil responde y es alcanzable desde la API" ;;
    *) bad "el proveedor no está sano (revisá YT_POT_URL y que bgutil esté vivo)" ;; esac
  case "$health" in
    *'"mismatch":true'*|*'"mismatch": true'*) bad "plugin y sidecar en majors distintas → redeployá" ;;
    *'"mismatch":false'*|*'"mismatch": false'*) ok "las versiones de plugin y sidecar coinciden" ;;
  esac
fi

# --- 3. Aislamiento de red ---------------------------------------------------
sect "3. Aislamiento de red (bgutil no debe ver a Redis)"
# network inspect lista los contenedores por ID; el ID corto de `docker ps` es prefijo del
# largo, así que un match por subcadena alcanza.
in_net() { docker network inspect "$1" -f '{{range $k,$v := .Containers}}{{$k}}{{"\n"}}{{end}}' \
             2>/dev/null | grep -q "^$2"; }
if [ -z "$BG" ]; then
  bad "no encuentro el contenedor de bgutil, no puedo chequear la red"
else
  if in_net "$NET_POT" "$BG"; then ok "bgutil está en la red $NET_POT"; else bad "bgutil no está en $NET_POT"; fi
  if [ -n "$API" ] && in_net "$NET_POT" "$API"; then ok "la API alcanza a bgutil por $NET_POT"
    else bad "la API no está en $NET_POT (no podría pedir tokens)"; fi
  if in_net "$NET_MAIN" "$BG"; then bad "bgutil comparte $NET_MAIN con Redis (NO aislado)"
    else ok "bgutil NO está en $NET_MAIN: no ve a Redis"; fi
fi

# --- 4. Hardening del contenedor ---------------------------------------------
sect "4. Hardening de bgutil"
if [ -z "$BG" ]; then
  bad "sin contenedor de bgutil, no puedo inspeccionar el hardening"
else
  caps="$(docker inspect -f '{{.HostConfig.CapDrop}}' "$BG" 2>/dev/null)"
  secopt="$(docker inspect -f '{{.HostConfig.SecurityOpt}}' "$BG" 2>/dev/null)"
  img="$(docker inspect -f '{{.Config.Image}}' "$BG" 2>/dev/null)"
  case "$caps" in   *ALL*) ok "cap_drop: ALL" ;; *) bad "sin cap_drop ALL (es: ${caps:-vacío})" ;; esac
  case "$secopt" in *no-new-privileges*) ok "no-new-privileges activo" ;;
    *) bad "sin no-new-privileges (es: ${secopt:-vacío})" ;; esac
  case "$img" in *@sha256:*) ok "imagen fijada por digest" ;;
    *) bad "la imagen NO está fijada por digest: ${img:-desconocida}" ;; esac
fi

# --- Veredicto ---------------------------------------------------------------
if [ "$fails" -eq 0 ]; then
  printf '\n\033[1;32m✓ TODO OK\033[0m — el stack está sano y endurecido.\n'
  printf '  Ahora probá un MP3 real en %s (puede pedir cookies si tu IP está bloqueada).\n' "$BASE_URL"
  exit 0
else
  printf '\n\033[1;31m✗ %d CHEQUEO(S) FALLARON\033[0m — mirá los ✗ de arriba.\n' "$fails"
  exit 1
fi
