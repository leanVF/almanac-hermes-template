# Imagen custom para Nel AI Vendedor en ALMANAC
# Base: imagen oficial de Hermes Agent (Nous Research)
FROM nousresearch/hermes-agent:latest

# Metadata
LABEL maintainer="Leandro Vera <leanvf@almanac.lat>"
LABEL project="almanac-hermes-nel-ai"
LABEL version="1.1.0"

# La imagen base usa /opt/data como HERMES_HOME (es propiedad del user 'hermes' UID 10000)
# NO usar /home/hermes — ese path no existe en la imagen base.
ENV HERMES_HOME=/opt/data
ENV PYTHONUNBUFFERED=1

# Instalar dependencias del sistema como root
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Crear directorios para los assets del daemon (bajo /opt/nel-ai, world-writable OK)
RUN mkdir -p /opt/nel-ai/daemon /opt/nel-ai/configuracion /opt/nel-ai/profile && \
    chown -R hermes:hermes /opt/nel-ai

# Copiar el daemon
COPY --chown=hermes:hermes daemon/ /opt/nel-ai/daemon/

# Copiar las reglas de prompt (frases, productos, etc.)
COPY --chown=hermes:hermes configuracion/ /opt/nel-ai/configuracion/

# Copiar el perfil base (SOUL, config, memories)
COPY --chown=hermes:hermes profile/ /opt/nel-ai/profile/

# Crear entrypoint
COPY --chown=hermes:hermes entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Correr como el usuario 'hermes' (UID 10000) — NO root
USER hermes

# Healthcheck: validar que el gateway responde
HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=60s \
    CMD curl -fsS http://127.0.0.1:8642/health || exit 1

# Puerto del gateway (Coolify lo publica)
EXPOSE 8642

# Arranca gateway + daemon juntos
ENTRYPOINT ["/entrypoint.sh"]
