# Imagen custom para Nel AI Vendedor en ALMANAC
# Base: imagen oficial de Hermes Agent (Nous Research)
FROM nousresearch/hermes-agent:latest

# Metadata
LABEL maintainer="Leandro Vera <leanvf@almanac.lat>"
LABEL project="almanac-hermes-nel-ai"
LABEL version="1.2.0"

# Variables de entorno
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
# s6-overlay busca servicios en /etc/s6-overlay/s6-rc.d/ y /etc/services.d/
COPY --chown=root:root s6-services/nel-ai-init /etc/s6-overlay/s6-rc.d/nel-ai-init
COPY --chown=root:root s6-services/nel-ai-daemon /etc/s6-overlay/s6-rc.d/nel-ai-daemon

# Hacer ejecutables los run scripts
RUN chmod +x /etc/s6-overlay/s6-rc.d/nel-ai-init/run /etc/s6-overlay/s6-rc.d/nel-ai-daemon/run

# CRÍTICO: registrar los servicios en el bundle "user" de s6-overlay
# Sin esto, s6-overlay no los ejecuta aunque estén en s6-rc.d/
# (El bundle "user" es lo que s6-overlay arranca por default)
RUN touch /etc/s6-overlay/s6-rc.d/user/contents.d/nel-ai-init \
          /etc/s6-overlay/s6-rc.d/user/contents.d/nel-ai-daemon

# Healthcheck: validar que el gateway responde
HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=60s \
    CMD curl -fsS http://127.0.0.1:8642/health || exit 1

# Puerto del gateway (Coolify lo publica)
EXPOSE 8642

# Usar el entrypoint ORIGINAL de la imagen (s6-overlay como PID 1)
# El CMD por default es el main-wrapper.sh que ejecuta `hermes` (gateway)
ENTRYPOINT ["/init", "/opt/hermes/docker/main-wrapper.sh"]
CMD ["gateway", "run"]
