#!/usr/bin/env python3
"""
Nel AI Vendedor — Daemon de Chatwoot para perfil nel-ai-vendedor.

Hace polling al inbox de WhatsApp de Chatwoot cada N segundos y, para cada
mensaje nuevo que no sea del propio agente, llama al LLM (via Hermes gateway)
con el SOUL + skills y responde via Chatwoot API.

Uso:
    nel-ai-vendedor daemon start     # Arranca como servicio (s6/systemd)
    nel-ai-vendedor daemon stop      # Para el servicio
    nel-ai-vendedor daemon status    # Estado del servicio
    nel-ai-vendedor daemon run       # Run en foreground (debug)

Variables de entorno leídas desde el perfil:
    CHATWOOT_BASE_URL, CHATWOOT_API_TOKEN, CHATWOOT_ACCOUNT_ID
    CHATWOOT_INBOX_IDS (csv), CHATWOOT_MODE, CHATWOOT_IGNORE_AGENT_MESSAGES
    HERMES_GATEWAY_URL (default http://localhost:8765)
    NEL_DAEMON_INTERVAL (default 8 segundos)
"""

import os
import sys
import json
import time
import signal
import logging
import threading
import requests
from pathlib import Path
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import ThreadingMixIn

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

HERMES_HOME = Path(os.environ.get("HERMES_HOME", "/home/leanvf/.hermes"))
PROFILE_DIR = HERMES_HOME / "profiles" / "nel-ai-vendedor"
LOG_FILE = PROFILE_DIR / "logs" / "daemon.log"
STATE_FILE = PROFILE_DIR / "logs" / "daemon.state.json"
NEL_WEBHOOK_HOST = os.environ.get("NEL_WEBHOOK_HOST", "127.0.0.1")
NEL_WEBHOOK_PORT = int(os.environ.get("NEL_WEBHOOK_PORT", "8766"))

LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("nel-daemon")

# Cargar .env del perfil
def load_env():
    env_file = PROFILE_DIR / ".env"
    if not env_file.exists():
        log.error(f"No se encontró .env en {env_file}")
        sys.exit(1)
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip()
            # Si ya está en env del proceso, no pisar (permite override)
            os.environ.setdefault(k, v)

load_env()

CHATWOOT_BASE_URL = os.environ["CHATWOOT_BASE_URL"].rstrip("/")
CHATWOOT_TOKEN = os.environ["CHATWOOT_API_TOKEN"]
CHATWOOT_ACCOUNT_ID = int(os.environ.get("CHATWOOT_ACCOUNT_ID", "1"))
CHATWOOT_INBOX_IDS = [
    int(x) for x in os.environ.get("CHATWOOT_INBOX_IDS", "").split(",") if x.strip()
]
CHATWOOT_IGNORE_AGENT_MESSAGES = (
    os.environ.get("CHATWOOT_IGNORE_AGENT_MESSAGES", "true").lower() == "true"
)
CHATWOOT_MODE = os.environ.get("CHATWOOT_MODE", "assisted")

HERMES_GATEWAY_URL = os.environ.get("HERMES_GATEWAY_URL", "http://127.0.0.1:8645")
DAEMON_INTERVAL = int(os.environ.get("NEL_DAEMON_INTERVAL", "8"))

# Skills: cargamos la lista de skills custom para inyectarlas al contexto
SKILLS_DIR = HERMES_HOME / "profiles" / "nel-ai-vendedor" / "skills" / "custom"
SOUL_FILE = PROFILE_DIR / "SOUL.md"

# ---------------------------------------------------------------------------
# State (para idempotencia)
# ---------------------------------------------------------------------------

def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"processed_message_ids": [], "last_poll_at": None}

def save_state(state):
    # Mantener solo los últimos 1000 IDs para no crecer infinito
    state["processed_message_ids"] = state["processed_message_ids"][-1000:]
    STATE_FILE.write_text(json.dumps(state, indent=2))

state = load_state()

# ---------------------------------------------------------------------------
# Chatwoot API
# ---------------------------------------------------------------------------

def chatwoot(method, path, **kw):
    headers = {"api_access_token": CHATWOOT_TOKEN, "Content-Type": "application/json"}
    url = f"{CHATWOOT_BASE_URL}{path}"
    try:
        r = requests.request(method, url, headers=headers, timeout=15, **kw)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        log.error(f"Chatwoot {method} {path} → {e.response.status_code}: {e.response.text[:300]}")
        return None
    except Exception as e:
        log.error(f"Chatwoot {method} {path} → {e}")
        return None

def _extract_payload(data):
    """Extrae la lista de conversaciones de la respuesta de Chatwoot.

    Formato de la respuesta v4:
      - Endpoint paginado: {"data": {"meta": ..., "payload": [...]}}
      - Endpoint sin paginar (algunos casos): [...]
    """
    if not data:
        return []
    if isinstance(data, list):
        return data
    # Endpoint paginado: payload dentro de data.data o data directamente
    if "payload" in data:
        return data["payload"]
    if "data" in data and isinstance(data["data"], dict) and "payload" in data["data"]:
        return data["data"]["payload"]
    return []


def fetch_conversations_to_handle():
    """Busca conversaciones abiertas en los inboxes que Nel AI atiende.

    Incluye conversaciones asignadas a Nel AI (mine) Y sin asignar (unassigned),
    para que Nel AI pueda tomar conversaciones nuevas que llegan al inbox.
    Filtra por inbox_id en código (Chatwoot no acepta filtro directo).
    """
    conversations = []

    # 1. Conversaciones asignadas a Nel AI
    data = chatwoot("GET", f"/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}/conversations",
                    params={"status": "open", "assignee_type": "me", "page": 1})
    conversations.extend(_extract_payload(data))

    # 2. Conversaciones sin asignar (que Nel AI puede tomar)
    data = chatwoot("GET", f"/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}/conversations",
                    params={"status": "open", "assignee_type": "unassigned", "page": 1})
    conversations.extend(_extract_payload(data))

    # Filtrar por inbox y deduplicar por id
    seen = set()
    filtered = []
    for c in conversations:
        if c["id"] in seen:
            continue
        seen.add(c["id"])
        if CHATWOOT_INBOX_IDS and c.get("inbox_id") not in CHATWOOT_INBOX_IDS:
            continue
        filtered.append(c)
    return filtered

def fetch_messages(conversation_id):
    data = chatwoot("GET", f"/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}/conversations/{conversation_id}/messages")
    return _extract_payload(data)

def send_message(conversation_id, content, private=False):
    payload = {"content": content, "message_type": "outgoing"}
    if private:
        payload["private"] = True
    return chatwoot(
        "POST",
        f"/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}/conversations/{conversation_id}/messages",
        json=payload,
    )

def assign_conversation(conversation_id):
    return chatwoot(
        "POST",
        f"/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}/conversations/{conversation_id}/assignments",
        json={"assignee_id": 3},  # user_id de Nel AI
    )

# ---------------------------------------------------------------------------
# LLM call (via Hermes gateway)
# ---------------------------------------------------------------------------

def call_llm(user_message, conversation_history=None):
    """Llama al LLM via el CLI de Hermes con el perfil nel-ai-vendedor.

    Como el CLI de Hermes NO inyecta automáticamente el SOUL.md ni las
    skills del perfil en el system prompt, lo hacemos nosotros: leemos el
    SOUL + el archivo de tono y los prependemos al prompt.

    Estructura del prompt final:
      [SOUL compacto]
      [Archivo frases-y-tono (truncado a 2000 chars para velocidad)]
      ---
      Historial reciente (últimos 6 mensajes)
      Mensaje actual del cliente

    Output del CLI tiene 3 partes:
      1. Status "Query: <prompt>" (línea inicial)
      2. Status "Background task running..." (si tarda)
      3. Bloque ╭─ ⚕ Hermes ─╮ ... ╰─...─╯ con la respuesta final
    Hay que parsear y quedarse solo con (3).
    """
    import subprocess
    import re

    # Leer SOUL compacto
    soul = ""
    if SOUL_FILE.is_symlink() or SOUL_FILE.exists():
        soul = SOUL_FILE.read_text()

    # Leer frases-y-tono (el más crítico para el estilo)
    tono_path = HERMES_HOME / "work" / "almanac-onboarding" / "configuracion" / "frases-y-tono.md"
    tono = tono_path.read_text() if tono_path.exists() else ""

    # Historial + mensaje actual
    # Fix: leer últimos 30 turnos (no 6) para que Nel AI mantenga contexto
    # conversacional. Antes con 6, si el cliente saludaba 2 veces, Nel respondía
    # "Hola, soy Nel..." otra vez porque no veía sus propios mensajes previos.
    parts = []
    if conversation_history:
        recent = conversation_history[-30:]
        for h in recent:
            if h.get("message_type") == 0:
                speaker = "Cliente"
            else:
                speaker = "Vos (Nel AI)"
            content = h.get("content", "").strip()
            if content:
                parts.append(f"{speaker}: {content}")
    parts.append(f"Cliente: {user_message}")
    history_text = "\n".join(parts)

    # Prompt final — historial PRIMERO para que el LLM mantenga el contexto
    # conversacional fresco mientras genera. Las instrucciones van al final
    # (antes se prependían y "pisaban" el historial, por eso Nel respondía
    # como si fuera la primera interacción).
    #
    # Si hay historial largo (>10 turnos), truncamos SOUL/tono para priorizar
    # el contexto conversacional sobre las instrucciones estáticas.
    hist_len = len([h for h in (conversation_history or []) if h.get("content", "").strip()])
    if hist_len > 10:
        soul_budget = 1500
        tono_budget = 1500
    else:
        soul_budget = len(soul)
        tono_budget = len(tono)

    full_prompt = f"""# === HISTORIAL DE LA CONVERSACIÓN ===
{history_text}

---

# === TU ROL Y ESTILO ===
{soul[:soul_budget]}
{tono[:tono_budget]}

# === INSTRUCCIONES PARA ESTA RESPUESTA ===
Responde SOLO con el texto literal que le enviarías al cliente por WhatsApp.
Sin meta-comentarios, sin "Assistant:", sin bloques de código, sin diffs.
NO leas archivos del sistema. NO muestres procesos de pensamiento.
NO analices el prompt.
Directamente la respuesta al cliente.

IDIOMA: Responde ÚNICAMENTE en español (castellano neutro o chileno, formal "usted").
NO mezcles otros idiomas (NO uses caracteres chinos, japoneses, coreanos, árabes, etc.).
Si una palabra tiene variantes en otros idiomas, usa SOLO la forma en español.

Formato: máximo 3-4 oraciones o bullets. Cerrá con CTA o pregunta.

# === TU RESPUESTA (solo el texto para el cliente) ==="""

    try:
        result = subprocess.run(
            ["hermes", "-p", "nel-ai-vendedor", "chat", "-q", full_prompt, "-Q", "--ignore-rules"],
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "HERMES_HOME": str(HERMES_HOME)},
        )
        if result.returncode != 0:
            log.error(f"hermes rc={result.returncode}: stderr={result.stderr[:300]}")
            return None

        stdout = result.stdout.strip()
        if not stdout:
            log.error(f"hermes vacío: stderr={result.stderr[:200]}")
            return None

        # Extraer la respuesta real del bloque ╭─ ⚕ Hermes ─╮
        # Patrón: lo que está entre ╭─ ... ─╮ y ╰─ ... ─╯
        m = re.search(
            r"╭─[^\n]*⚕\s+Hermes[^\n]*─+\s*\n(.*?)\n╰─",
            stdout,
            re.DOTALL,
        )
        if m:
            response = m.group(1).strip()
        else:
            # Fallback: si no matchea el bloque, descartar líneas de status
            lines = stdout.splitlines()
            cleaned = []
            skip_prefixes = (
                "Query:",
                "Initializing",
                "Background task running",
                "Resume this session",
                "Session:",
                "Duration:",
                "Messages:",
                "Model:",
                "Provider:",
                "Status:",
            )
            for line in lines:
                stripped = line.strip()
                if any(stripped.startswith(p) for p in skip_prefixes):
                    continue
                if stripped.startswith("╭") or stripped.startswith("╰") or stripped.startswith("│"):
                    continue
                if "──" in stripped:
                    continue
                if stripped:
                    cleaned.append(stripped)
            response = "\n".join(cleaned).strip()

        if not response:
            log.warning(f"No se pudo extraer respuesta del CLI. Raw output: {stdout[:300]}")
            return None

        # Defensa anti-CJK: el modelo MiniMax-M3 a veces "bleedea" caracteres
        # chinos/japoneses en respuestas largas bajo presión de contexto.
        # Si los detectamos, loggeamos y cortamos el response al último punto
        # anterior al CJK, para no mandar basura al cliente.
        cjk_indices = [i for i, c in enumerate(response)
                       if "\u4e00" <= c <= "\u9fff"
                       or "\u3040" <= c <= "\u309f"
                       or "\u30a0" <= c <= "\u30ff"
                       or "\u3400" <= c <= "\u4dbf"]
        if cjk_indices:
            first_cjk = cjk_indices[0]
            log.warning(
                f"⚠️ CJK detectado en respuesta (pos {first_cjk}/{len(response)}). "
                f"Contexto: '{response[max(0,first_cjk-30):first_cjk+30]}'. "
                f"Recortando al último punto antes del CJK."
            )
            # Cortar al último '.' o '?' o '!' antes del CJK
            prefix = response[:first_cjk]
            for terminator in ['. ', '? ', '! ', '.\n', '?\n', '!\n']:
                idx = prefix.rfind(terminator)
                if idx > 0:
                    response = prefix[:idx + len(terminator)].rstrip()
                    break
            else:
                # Sin terminador: cortar al último espacio
                idx = prefix.rfind(' ')
                if idx > 20:  # solo si quedó algo razonable
                    response = prefix[:idx].rstrip() + '.'
                else:
                    response = prefix.rstrip()
            log.info(f"Respuesta recortada a {len(response)} chars")

        log.debug(f"LLM response ({len(response)} chars): {response[:200]}")
        return response
    except subprocess.TimeoutExpired:
        log.error("hermes timeout (120s)")
        return None
    except Exception as e:
        log.error(f"hermes call failed: {e}")
        return None

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

running = True
def shutdown(*_):
    global running
    log.info("Shutdown solicitado")
    running = False
    save_state(state)

signal.signal(signal.SIGINT, shutdown)
signal.signal(signal.SIGTERM, shutdown)

def handle_conversation(conv):
    """Procesa una conversación: busca mensajes nuevos y responde."""
    conv_id = conv["id"]
    return _handle_conv_id(conv_id)


def _handle_conv_id(conv_id):
    """Procesa una conversación por ID. Usado por polling Y por webhook.

    Hace el fetch fresco de la conversación (puede haber cambiado) y delega
    a la lógica de buscar mensaje entrante nuevo y responder.
    """
    # Refrescar la conversación para tener assignee + meta actualizados.
    # Endpoint individual de Chatwoot devuelve la conversación como dict directo
    # (NO envuelta en {"payload": [...]}). fetch_messages sí usa el endpoint
    # de messages que sí viene envuelto.
    try:
        conv_resp = chatwoot("GET", f"/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}/conversations/{conv_id}")
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            log.debug(f"Conv #{conv_id}: 404, skip")
            return False
        raise
    if not isinstance(conv_resp, dict) or conv_resp.get("id") is None:
        log.debug(f"Conv #{conv_id}: respuesta inválida, skip")
        return False
    conv = conv_resp

    last_msg_id = conv.get("last_non_activity_message", {}).get("id")
    if not last_msg_id:
        last_msg_id = conv.get("last_activity", {}).get("id")

    # Si la conversación no está asignada a Nel AI, auto-asignarla
    if conv.get("assignee") is None:
        log.info(f"Conv #{conv_id}: sin asignar, tomando...")
        assign_conversation(conv_id)

    messages = fetch_messages(conv_id)
    if not messages:
        return False

    # Buscar el último mensaje entrante (message_type=0 es incoming, 1 es outgoing)
    # que NO sea de un agent (si CHATWOOT_IGNORE_AGENT_MESSAGES)
    last_incoming = None
    for m in reversed(messages):
        if m.get("message_type") != 0:
            continue
        if CHATWOOT_IGNORE_AGENT_MESSAGES and m.get("sender", {}).get("type") == "user":
            # sender.type puede ser 'user' (agent) o 'contact' (cliente)
            sender = m.get("sender", {})
            if sender.get("type") == "user" and sender.get("id") != 3:
                # Otro agent escribió, no responder
                continue
        last_incoming = m
        break

    if not last_incoming:
        return False

    msg_id = last_incoming["id"]
    if msg_id in state["processed_message_ids"]:
        log.debug(f"Conv #{conv_id}: msg {msg_id} ya procesado, skip")
        return False

    # Mensaje nuevo! Procesarlo
    content = last_incoming.get("content", "").strip()
    if not content:
        return False

    contact = conv.get("meta", {}).get("sender", {})
    contact_name = contact.get("name", "cliente")
    log.info(f"Conv #{conv_id}: nuevo mensaje de {contact_name}: {content[:100]}")

    # Llamar al LLM (el CLI carga SOUL + skills del perfil automáticamente)
    response_text = call_llm(
        content,
        conversation_history=messages,
    )

    if not response_text:
        log.warning(f"Conv #{conv_id}: LLM no devolvió respuesta, skip")
        return False

    # Limpiar respuesta (a veces los LLMs agregan "Assistant:" o quotes)
    response_text = response_text.strip()
    if response_text.startswith('"') and response_text.endswith('"'):
        response_text = response_text[1:-1]

    # Si modo es assisted, mandamos como private note (no la ve el cliente)
    is_private = CHATWOOT_MODE == "assisted"
    result = send_message(conv_id, response_text, private=is_private)
    if result:
        log.info(
            f"Conv #{conv_id}: respuesta {'(private/draft)' if is_private else '(pública)'} "
            f"enviada: {response_text[:80]}"
        )
        state["processed_message_ids"].append(msg_id)
        save_state(state)
        return True
    return False

def run_loop():
    log.info(f"Daemon iniciado. Intervalo={DAEMON_INTERVAL}s, inboxes={CHATWOOT_INBOX_IDS}, mode={CHATWOOT_MODE}")
    start_webhook_server()
    while running:
        try:
            conversations = fetch_conversations_to_handle()
            log.debug(f"Encontradas {len(conversations)} conversaciones para revisar")
            for conv in conversations:
                if not running:
                    break
                handle_conversation(conv)
            state["last_poll_at"] = datetime.now(timezone.utc).isoformat()
        except Exception as e:
            log.exception(f"Error en loop principal: {e}")
        # Sleep interrumpible
        for _ in range(DAEMON_INTERVAL):
            if not running:
                break
            time.sleep(1)
    log.info("Daemon detenido")


# ---------------------------------------------------------------------------
# Webhook listener (Opción A: Chatwoot → Nel)
# ---------------------------------------------------------------------------
#
# Chatwoot manda POST a https://api.almanac.lat/nel/webhook cuando llega un
# mensaje nuevo al inbox configurado. Tunnelled a localhost:8766 vía Cloudflare
# Tunnel. El handler dispara _handle_conv_id en un thread aparte (no bloquea
# el HTTP server, que responde 200 inmediato a Chatwoot).
#
# El polling de backup sigue corriendo en DAEMON_INTERVAL. Ambos paths usan
# state["processed_message_ids"] así que un mensaje procesado por webhook no
# se reprocesa por polling.

class NelWebhookHandler(BaseHTTPRequestHandler):
    """Handler HTTP para /nel/webhook y /nel/health."""

    def log_message(self, format, *args):
        # Silenciar el log default (ruidoso), usamos nuestro logger
        pass

    def _send_json(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/nel/health" or self.path == "/health":
            self._send_json(200, {
                "status": "ok",
                "uptime": "unknown",  # TODO: track start time
                "polling_interval": DAEMON_INTERVAL,
                "processed_count": len(state["processed_message_ids"]),
            })
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/nel/webhook":
            self._send_json(404, {"error": "not found"})
            return

        # Leer body
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8")
            payload = json.loads(raw)
        except Exception as e:
            log.warning(f"Webhook: body inválido: {e}")
            self._send_json(400, {"error": "invalid body"})
            return

        # Chatwoot manda un envelope con event + data
        event = payload.get("event") or payload.get("message_type", "")
        # Aceptamos message_created (evento de Chatwoot) o cualquier POST con
        # conversation_id (formato simplificado)
        if event and event not in ("message_created", "MessageCreated"):
            self._send_json(200, {"ignored": f"event={event}"})
            return

        # Extraer conversación + mensaje
        conv_id = None
        msg_id = None
        if isinstance(payload.get("data"), dict):
            data = payload["data"]
            conv_id = data.get("conversation", {}).get("id")
            msg_id = data.get("id") or data.get("message", {}).get("id")
        if conv_id is None:
            conv_id = payload.get("conversation_id")
        if msg_id is None:
            msg_id = payload.get("id") or payload.get("message_id")

        if conv_id is None:
            log.warning(f"Webhook: sin conversation_id, payload={payload}")
            self._send_json(400, {"error": "no conversation_id"})
            return

        # Idempotencia: si ya procesamos este mensaje, devolver 200 y listo
        if msg_id and msg_id in state["processed_message_ids"]:
            log.info(f"Webhook: msg {msg_id} ya procesado, ignorando")
            self._send_json(200, {"ok": True, "deduped": True})
            return

        log.info(f"Webhook: conv #{conv_id}, msg {msg_id} — procesando en thread")
        # Disparar procesamiento en thread aparte para responder 200 inmediato
        t = threading.Thread(
            target=_handle_conv_id_safe,
            args=(conv_id,),
            daemon=True,
        )
        t.start()

        self._send_json(200, {"ok": True, "conv_id": conv_id, "msg_id": msg_id})


def _handle_conv_id_safe(conv_id):
    """Wrapper para que errores en el thread no maten el daemon."""
    try:
        _handle_conv_id(conv_id)
    except Exception as e:
        log.exception(f"Error procesando conv #{conv_id} vía webhook: {e}")


def start_webhook_server():
    """Arranca el HTTP server en un thread daemon."""
    server = ThreadingHTTPServer((NEL_WEBHOOK_HOST, NEL_WEBHOOK_PORT), NelWebhookHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log.info(f"Webhook listener activo en http://{NEL_WEBHOOK_HOST}:{NEL_WEBHOOK_PORT}")
    return server


if __name__ == "__main__":
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "start":
            log.info("Para 'start' usá el wrapper nel-ai-vendedor daemon start")
            sys.exit(1)
    run_loop()
