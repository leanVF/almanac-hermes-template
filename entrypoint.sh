#!/bin/sh
# Entrypoint de Nel AI Vendedor en container
# Arranca gateway de Hermes + daemon de Chatwoot en paralelo.
# Si cualquiera muere, el container se cae (Coolify lo reinicia).

set -e

echo "[entrypoint] Iniciando Hermes Gateway + Nel AI Daemon..."

# Asegurar que el perfil existe en HERMES_HOME
# Coolify monta un volumen, pero la primera vez está vacío.
# Si no existe el perfil, lo creamos desde /opt/nel-ai/profile/

if [ ! -d "$HERMES_HOME/profiles/nel-ai-vendedor" ]; then
    echo "[entrypoint] Perfil no existe, inicializando desde /opt/nel-ai/profile/"
    mkdir -p "$HERMES_HOME/profiles/nel-ai-vendedor"
    cp -r /opt/nel-ai/profile/. "$HERMES_HOME/profiles/nel-ai-vendedor/"
    # Si hay un .env real montado, copiar sobre el .env.example
    if [ -f "$HERMES_HOME/profiles/nel-ai-vendedor/.env" ]; then
        echo "[entrypoint] .env real detectado, conservando"
    fi
fi

# Arrancar gateway en background
echo "[entrypoint] Iniciando gateway..."
gateway run &
GATEWAY_PID=$!

# Esperar 10 segundos para que el gateway esté listo
sleep 10

# Arrancar el daemon en background
echo "[entrypoint] Iniciando daemon de Nel AI..."
cd /opt/nel-ai/daemon
python3 nel-chatwoot-daemon.py >> "$HERMES_HOME/profiles/nel-ai-vendedor/logs/daemon.log" 2>&1 &
DAEMON_PID=$!

echo "[entrypoint] Gateway PID=$GATEWAY_PID, Daemon PID=$DAEMON_PID"

# Esperar a que cualquiera de los dos procesos termine.
# Si uno muere, matamos al otro y salimos con código != 0 para que
# Coolify reinicie el container.
wait -n $GATEWAY_PID $DAEMON_PID
EXIT_CODE=$?

echo "[entrypoint] Un proceso terminó (código $EXIT_CODE), apagando container..."

# Limpiar el otro proceso
kill $GATEWAY_PID 2>/dev/null || true
kill $DAEMON_PID 2>/dev/null || true

exit $EXIT_CODE
