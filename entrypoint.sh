#!/bin/sh
# Entrypoint de Nel AI Vendedor en container.
# Arranca gateway de Hermes + daemon de Chatwoot en paralelo.
#
# HERMES_HOME en la imagen base es /opt/data (no /home/hermes/.hermes).

set -e

echo "[entrypoint] Iniciando Nel AI Vendedor..."

export HERMES_HOME=/opt/data

# 1. Crear symlinks para que el daemon encuentre sus assets.
# El daemon espera:
#   $HERMES_HOME/profiles/nel-ai-vendedor/SOUL.md
#   $HERMES_HOME/profiles/nel-ai-vendedor/.env
#   $HERMES_HOME/work/almanac-onboarding/configuracion/frases-y-tono.md
#   $HERMES_HOME/work/almanac-onboarding/configuracion/productos.md
#   $HERMES_HOME/work/almanac-onboarding/configuracion/precios.md
#   $HERMES_HOME/work/almanac-onboarding/configuracion/informacion-general.md
#   $HERMES_HOME/work/almanac-onboarding/configuracion/que-hacemos.md
#   $HERMES_HOME/work/almanac-onboarding/configuracion/mercado-competencia.md
#   $HERMES_HOME/work/almanac-onboarding/configuracion/mision-vision.md

echo "[entrypoint] Creando estructura de directorios..."
mkdir -p "$HERMES_HOME/work/almanac-onboarding/configuracion"

# 2. Asegurar que el perfil existe
if [ ! -d "$HERMES_HOME/profiles/nel-ai-vendedor" ]; then
    echo "[entrypoint] Perfil no existe, copiando desde /opt/nel-ai/profile/"
    mkdir -p "$HERMES_HOME/profiles"
    cp -r /opt/nel-ai/profile "$HERMES_HOME/profiles/nel-ai-vendedor"
fi

# 3. Crear symlinks para los assets de prompt
# Solo si no existen ya (los archivos reales)
for f in frases-y-tono productos precios informacion-general que-hacemos mercado-competencia mision-vision; do
    if [ -f "/opt/nel-ai/configuracion/${f}.md" ] && [ ! -e "$HERMES_HOME/work/almanac-onboarding/configuracion/${f}.md" ]; then
        ln -s "/opt/nel-ai/configuracion/${f}.md" "$HERMES_HOME/work/almanac-onboarding/configuracion/${f}.md"
        echo "[entrypoint]   link: ${f}.md -> /opt/nel-ai/configuracion/${f}.md"
    fi
done

# 4. Directorio de logs
mkdir -p "$HERMES_HOME/profiles/nel-ai-vendedor/logs"

# 5. Arrancar gateway
echo "[entrypoint] Iniciando Hermes Gateway..."
gateway run >> "$HERMES_HOME/profiles/nel-ai-vendedor/logs/gateway.log" 2>&1 &
GATEWAY_PID=$!

echo "[entrypoint] Esperando 15s a que el gateway esté listo..."
sleep 15

# 6. Arrancar daemon
echo "[entrypoint] Iniciando daemon Nel AI (Chatwoot polling)..."
cd /opt/nel-ai/daemon
python3 nel-chatwoot-daemon.py \
    >> "$HERMES_HOME/profiles/nel-ai-vendedor/logs/daemon.log" 2>&1 &
DAEMON_PID=$!

echo "[entrypoint] Gateway PID=$GATEWAY_PID, Daemon PID=$DAEMON_PID"

# 7. Esperar a que cualquiera termine; si uno muere, matar el otro
wait -n $GATEWAY_PID $DAEMON_PID
EXIT_CODE=$?

echo "[entrypoint] Proceso terminó (código $EXIT_CODE), apagando container..."

kill $GATEWAY_PID 2>/dev/null || true
kill $DAEMON_PID 2>/dev/null || true

exit $EXIT_CODE
