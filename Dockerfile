# Imagen custom para Nel AI Vendedor en ALMANAC
# Base: imagen oficial de Hermes Agent (Nous Research)
FROM nousresearch/hermes-agent:latest

# Metadata
LABEL maintainer="Leandro Vera <leanvf@almanac.lat>"
LABEL project="almanac-hermes-nel-ai"
LABEL version="1.0.0"

# Variables de entorno de Hermes
ENV HERMES_HOME=/home/hermes/.hermes
ENV PYTHONUNBUFFERED=1

# Instalar dependencias del sistema que el daemon necesita
# (psycopg2 para postgres, requests para APIs, etc.)
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Crear directorios para los assets
RUN mkdir -p /opt/nel-ai/daemon /opt/nel-ai/configuracion /opt/nel-ai/profile

# Copiar el daemon
COPY daemon/ /opt/nel-ai/daemon/

# Copiar las reglas de prompt (frases, productos, etc.)
COPY configuracion/ /opt/nel-ai/configuracion/

# Copiar el perfil base (SOUL, config, memories)
COPY profile/ /opt/nel-ai/profile/

# Crear entrypoint
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Volver al usuario por defecto de la imagen
USER hermes

# Healthcheck: validar que el gateway responde
HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=60s \
    CMD curl -fsS http://127.0.0.1:8642/health || exit 1

# Puerto del gateway (Coolify lo publica)
EXPOSE 8642

# Arranca gateway + daemon juntos, si uno muere el container muere
ENTRYPOINT ["/entrypoint.sh"]
