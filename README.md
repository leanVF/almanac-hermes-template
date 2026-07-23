# Nel AI Vendedor - Hermes Agent Deploy

Template para deployar Nel AI Vendedor (Hermes Agent + Chatwoot daemon) en Coolify.

## ¿Qué es esto?

Un deploy completo de Nel AI Vendedor como container aislado en Coolify. Incluye:

- Hermes Agent (gateway LLM)
- Daemon de Chatwoot (polling cada 8s, contesta WhatsApp/Instagram)
- Perfiles y assets de prompt preconfigurados
- Healthcheck real
- Auto-restart en crash

## Estructura

```
.
├── Dockerfile              # Imagen custom basada en nousresearch/hermes-agent
├── docker-compose.yml      # Para Coolify (build_pack=dockerfile)
├── entrypoint.sh           # Arranca gateway + daemon juntos
├── daemon/                 # Script de polling de Chatwoot
├── configuracion/          # Reglas de prompt (productos, tono, precios)
├── profile/                # Perfil Hermes (SOUL.md, config, memories)
├── .env.coolify.example    # Variables para Coolify (sin valores)
├── deploy-coolify.sh       # Script de creación vía API
├── rollback-coolify.sh     # Script de rollback
└── README.md               # Este archivo
```

## Cómo deployar

### Pre-requisitos

1. Token de Coolify con permisos de crear resources
2. Token de Cloudflare para crear el dominio
3. Credenciales de Chatwoot (URL + API token + account ID)
4. Al menos 1 API key de LLM (Anthropic, OpenAI, OpenRouter, etc.)

### Pasos

```bash
# 1. Cargar el vault de credenciales
source ~/.hermes/profiles/leandro-maestro/secrets/MAESTRO-CREDENCIALES.env

# 2. Crear el DNS en Cloudflare
# nel.almanac.lat → IP del server de Coolify

# 3. Crear la Application en Coolify
./deploy-coolify.sh

# 4. Ir a la UI de Coolify y agregar:
#    - Variables de entorno (copiar de .env.coolify.example)
#    - Dominio: nel.almanac.lat

# 5. Disparar deploy desde la UI

# 6. Validar
curl https://nel.almanac.lat:8642/health
```

## Cómo hacer rollback

```bash
./rollback-coolify.sh
```

## Decisiones de diseño

### Por qué imagen custom y no compose con bind mount

El container queda **100% autónomo**. No depende de paths del host. Si el server se muda, solo hay que copiar la imagen.

### Por qué gateway + daemon en el mismo container

Más simple de operar. Si el gateway cae, el daemon también (Coolify reinicia los dos juntos).

### Por qué sin bot de Telegram

Nel AI no usa Telegram para responder clientes (usa Chatwoot → WhatsApp/Instagram). Por principio de mínimo privilegio, Telegram queda fuera de este deploy.

## Variables de entorno obligatorias

| Variable | Para qué |
|---|---|
| `OPENROUTER_API_KEY` o cualquier LLM key | Modelo de IA |
| `CHATWOOT_BASE_URL` | URL de Chatwoot |
| `CHATWOOT_API_TOKEN` | Token de API |
| `CHATWOOT_ACCOUNT_ID` | ID de cuenta (default: 1) |
| `CHATWOOT_INBOX_IDS` | CSV de inboxes (ej: 2,4) |

## Volúmenes persistentes

- `nel-ai-data` → `/home/hermes/.hermes` → guarda state.db, logs, memories

Si querés empezar de cero: `docker volume rm nel-ai-data`
