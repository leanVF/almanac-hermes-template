#!/bin/sh
# Entrypoint wrapper para Nel AI Vendedor.
# La imagen base usa /init (s6-overlay) + main-wrapper.sh como entrypoint.
# Necesitamos mantener ese entrypoint para que el gateway funcione,
# y agregar el daemon de Chatwoot como un proceso adicional.

set -e

echo "[entrypoint-wrapper] Iniciando Nel AI Vendedor wrapper..."

# 1. Configurar HERMES_HOME
export HERMES_HOME=/opt/data

# 2. Asegurar que el perfil existe
if [ ! -d "$HERMES_HOME/profiles/nel-ai-vendedor" ]; then
    echo "[entrypoint-wrapper] Perfil no existe, copiando desde /opt/nel-ai/profile/"
    mkdir -p "$HERMES_HOME/profiles"
    cp -r /opt/nel-ai/profile "$HERMES_HOME/profiles/nel-ai-vendedor"
fi

# 3. Crear symlinks para los assets del daemon
mkdir -p "$HERMES_HOME/work/almanac-onboarding/configuracion"
for f in frases-y-tono productos precios informacion-general que-hacemos mercado-competencia mision-vision; do
    if [ -f "/opt/nel-ai/configuracion/${f}.md" ] && [ ! -e "$HERMES_HOME/work/almanac-onboarding/configuracion/${f}.md" ]; then
        ln -s "/opt/nel-ai/configuracion/${f}.md" "$HERMES_HOME/work/almanac-onboarding/configuracion/${f}.md"
        echo "[entrypoint-wrapper]   link: ${f}.md"
    fi
done

# 4. Directorio de logs
mkdir -p "$HERMES_HOME/profiles/nel-ai-vendedor/logs"

# 5. Llamar al entrypoint ORIGINAL de la imagen base en background
#    Esto arranca s6-overlay y todos los servicios internos (incluido el gateway)
echo "[entrypoint-wrapper] Llamando al entrypoint de la imagen base..."
/init /opt/hermes/docker/main-wrapper.sh &
BASE_PID=$!

# 6. Esperar a que el gateway arranque (s6-overlay tarda ~30s en estar listo)
echo "[entrypoint-wrapper] Esperando 30s a que s6-overlay y el gateway arranquen..."
sleep 30

# 7. Arrancar el daemon de Nel AI
echo "[entrypoint-wrapper] Iniciando daemon Nel AI..."
cd /opt/nel-ai/daemon
python3 nel-chatwoot-daemon.py \
    >> "$HERMES_HOME/profiles/nel-ai-vendedor/logs/daemon.log" 2>&1 &
DAEMON_PID=$!

echo "[entrypoint-wrapper] Base PID=$BASE_PID, Daemon PID=$DAEMON_PID"

# 8. Monitorear (sin wait -n — usar kill -0)
# Si el daemon muere, salimos; s6-overlay se encarga del gateway
while true; do
    if ! kill -0 $DAEMON_PID 2>/dev/null; then
        echo "[entrypoint-wrapper] Daemon murió, saliendo..."
        exit 1
    fi
    if ! kill -0 $BASE_PID 2>/dev/null; then
        echo "[entrypoint-wrapper] s6-overlay murió, saliendo..."
        exit 1
    fi
    sleep 5
done
