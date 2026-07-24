# Imagen custom para Nel AI Vendedor en ALMANAC
# Base: imagen oficial de Hermes Agent (Nous Research)
FROM nousresearch/hermes-agent:latest

# Metadata
LABEL maintainer="Leandro Vera <leanvf@almanac.lat>"
LABEL project="almanac-hermes-nel-ai"
LABEL version="1.3.0"

# === Build args (Coolify los pasa como --build-arg cuando se configura
#     "Available at Buildtime") ===
# Coolify 4.1.2 con build_pack=dockerfile pasa las env vars de la UI
# al container en build time, no en runtime. Por eso las declaramos como ARG
# y las hacemos ENV adentro de la imagen.

ARG CHATWOOT_BASE_URL
ARG CHATWOOT_API_TOKEN
ARG CHATWOOT_ACCOUNT_ID
ARG CHATWOOT_AGENT_NAME
ARG CHATWOOT_AGENT_EMAIL
ARG CHATWOOT_INBOX_IDS
ARG CHATWOOT_MODE
ARG CHATWOOT_IGNORE_AGENT_MESSAGES
ARG HERMES_GATEWAY_URL
ARG NEL_DAEMON_INTERVAL
ARG NEL_WEBHOOK_HOST
ARG NEL_WEBHOOK_PORT
ARG OPENROUTER_API_KEY
ARG GOOGLE_API_KEY
ARG MISTRAL_API_KEY
ARG NVIDIA_API_KEY
ARG AISA_API_KEY

# Convertir ARGs a ENVs para que estén disponibles en runtime del container
ENV CHATWOOT_BASE_URL=${CHATWOOT_BASE_URL:-}
ENV CHATWOOT_API_TOKEN=${CHATWOOT_API_TOKEN:-}
ENV CHATWOOT_ACCOUNT_ID=${CHATWOOT_ACCOUNT_ID:-1}
ENV CHATWOOT_AGENT_NAME=${CHATWOOT_AGENT_NAME:-Nel AI Vendedor}
ENV CHATWOOT_AGENT_EMAIL=${CHATWOOT_AGENT_EMAIL:-nel-ai-vendedor@almanac.lat}
ENV CHATWOOT_INBOX_IDS=${CHATWOOT_INBOX_IDS:-2,4}
ENV CHATWOOT_MODE=${CHATWOOT_MODE:-auto}
ENV CHATWOOT_IGNORE_AGENT_MESSAGES=${CHATWOOT_IGNORE_AGENT_MESSAGES:-true}
ENV HERMES_GATEWAY_URL=${HERMES_GATEWAY_URL:-http://127.0.0.1:8642}
ENV NEL_DAEMON_INTERVAL=${NEL_DAEMON_INTERVAL:-8}
ENV NEL_WEBHOOK_HOST=${NEL_WEBHOOK_HOST:-127.0.0.1}
ENV NEL_WEBHOOK_PORT=${NEL_WEBHOOK_PORT:-8766}
ENV OPENROUTER_API_KEY=${OPENROUTER_API_KEY:-}
ENV GOOGLE_API_KEY=${GOOGLE_API_KEY:-}
ENV MISTRAL_API_KEY=${MISTRAL_API_KEY:-}
ENV NVIDIA_API_KEY=${NVIDIA_API_KEY:-}
ENV AISA_API_KEY=${AISA_API_KEY:-}

# HERMES_HOME en la imagen base es /opt/data
ENV HERMES_HOME=/opt/data
ENV PYTHONUNBUFFERED=1

# Instalar paquetes necesarios como root
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Crear estructura para assets
RUN mkdir -p /opt/nel-ai/daemon /opt/nel-ai/configuracion /opt/nel-ai/profile && \
    chown -R hermes:hermes /opt/nel-ai

# Copiar daemon, configuracion y perfil
COPY --chown=hermes:hermes daemon/ /opt/nel-ai/daemon/
COPY --chown=hermes:hermes configuracion/ /opt/nel-ai/configuracion/
COPY --chown=hermes:hermes profile/ /opt/nel-ai/profile/

# Copiar servicios s6 personalizados
COPY --chown=root:root s6-services/nel-ai-init /etc/s6-overlay/s6-rc.d/nel-ai-init
COPY --chown=root:root s6-services/nel-ai-daemon /etc/s6-overlay/s6-rc.d/nel-ai-daemon

# Hacer ejecutables los run scripts
RUN chmod +x /etc/s6-overlay/s6-rc.d/nel-ai-init/run /etc/s6-overlay/s6-rc.d/nel-ai-daemon/run

# Registrar los servicios en el bundle "user" de s6-overlay
RUN touch /etc/s6-overlay/s6-rc.d/user/contents.d/nel-ai-init \
          /etc/s6-overlay/s6-rc.d/user/contents.d/nel-ai-daemon

# Healthcheck: validar que el gateway responde
HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=60s \
    CMD curl -fsS http://127.0.0.1:8642/health || exit 1

# Puerto del gateway (Coolify lo publica)
EXPOSE 8642

# Usar el entrypoint ORIGINAL de la imagen (s6-overlay como PID 1)
ENTRYPOINT ["/init", "/opt/hermes/docker/main-wrapper.sh"]
CMD ["gateway", "run"]
