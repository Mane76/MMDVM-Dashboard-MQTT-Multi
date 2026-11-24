#!/bin/bash
set -Eeuo pipefail

APP=/opt/mmdvm-agent
SERVICE=mmdvm-agent.service
UNIT=/etc/systemd/system/$SERVICE
SOURCE_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKUP_ROOT=/var/backups/mmdvm-agent
STAMP="$(date -u +%Y%m%d-%H%M%S)"
BACKUP="$BACKUP_ROOT/$STAMP"
STAGING="$(mktemp -d /tmp/mmdvm-agent-update.XXXXXX)"

cleanup() {
    rm -rf "$STAGING"
}
trap cleanup EXIT

fail() {
    echo "ERRORE: $*"
    return 1
}

if [ "$(id -u)" -ne 0 ]; then
    fail "Eseguire come root."
    exit 1
fi

echo "===== VERIFICA PACCHETTO ====="

for f in VERSION agent.py detect-mmdvm-config.py \
         gateway_monitor.py mobile_agent_executor.py \
         mobile_agent_guard.py mobile_agent_protocol.py \
         mobile_agent_receiver.py mobile_agent_worker.py \
         mobile_commands.py mobile_mqtt.py; do
    [ -f "$SOURCE_DIR/$f" ] || {
        fail "File mancante: $f"
        exit 1
    }
done

PACKAGE_VERSION="$(cat "$SOURCE_DIR/VERSION")"

python3 - "$SOURCE_DIR" "$APP" <<'PY'
import ast
import sys
from pathlib import Path

source = Path(sys.argv[1])
app = Path(sys.argv[2])

version = (source / "VERSION").read_text().strip()
tree = ast.parse((source / "agent.py").read_text())

found = None
for node in tree.body:
    if isinstance(node, ast.Assign):
        if any(isinstance(t, ast.Name) and t.id == "VERSION"
               for t in node.targets):
            found = ast.literal_eval(node.value)

if found != version:
    raise SystemExit("Versione del pacchetto non coerente")

if not (app / "agent.json").is_file():
    raise SystemExit("Configurazione agent.json assente")

if not (app / "broker.json").is_file():
    raise SystemExit("Configurazione broker.json assente")

import json

settings = json.loads((app / "agent.json").read_text())

required = (
    "control_enabled",
    "mmdvm_topic_base",
    "mmdvm_broker_settings_file",
)

missing = [key for key in required if not settings.get(key)]

if missing:
    raise SystemExit(
        "Configurazione controllo remoto incompleta: "
        + ", ".join(missing)
    )

if settings["control_enabled"] is not True:
    raise SystemExit("Controllo remoto non abilitato in agent.json")

control_broker = Path(settings["mmdvm_broker_settings_file"])

if not control_broker.is_file():
    raise SystemExit(
        "Broker controllo remoto non trovato: " + str(control_broker)
    )

print("Versione pacchetto:", version)
print("Configurazioni esistenti: OK")
print("Controllo remoto: OK")
PY

[ -f "$APP/agent.py" ] || {
    fail "Agent installato non trovato"
    exit 1
}

[ -f "$UNIT" ] || {
    fail "Servizio systemd non trovato"
    exit 1
}

echo
echo "===== PREPARAZIONE CANDIDATO ====="

# Prepara tutti i moduli Python dell'Agent.
for source in "$SOURCE_DIR"/*.py; do
    install -m 644 "$source" "$STAGING/$(basename "$source")"
done

# Controlla la sintassi di tutti i moduli prima di fermare il servizio.
python3 - "$STAGING" <<'CHECK'
import ast
import pathlib
import sys

for path in pathlib.Path(sys.argv[1]).glob("*.py"):
    ast.parse(path.read_text(), filename=str(path))
CHECK

python3 -B "$STAGING/agent.py" \
    --config "$APP/agent.json" --dry-run \
    > "$STAGING/dry-run.json"

python3 -m json.tool "$STAGING/dry-run.json" >/dev/null

echo "OK: Agent candidato funzionante"

echo
echo "===== BACKUP COMPLETO ====="

install -d -m 700 "$BACKUP_ROOT"
install -d -m 700 "$BACKUP"

tar -czf "$BACKUP/installation.tar.gz" \
    -C / opt/mmdvm-agent \
    etc/systemd/system/mmdvm-agent.service

chmod 600 "$BACKUP/installation.tar.gz"

sha256sum "$BACKUP/installation.tar.gz" \
    > "$BACKUP/SHA256SUMS"

echo "Backup creato: $BACKUP"

echo
echo "===== INSTALLAZIONE ====="

# Memorizza i nuovi moduli, per rimuoverli in caso di rollback.
NEW_MODULES=()
for source in "$STAGING"/*.py; do
    name="$(basename "$source")"
    if [[ ! -e "$APP/$name" ]]; then
        NEW_MODULES+=("$APP/$name")
    fi
done

systemctl stop "$SERVICE"

# Aggiorna tutti i moduli senza toccare i file JSON.
for source in "$STAGING"/*.py; do
    install -o root -g root -m 644 "$source" "$APP/$(basename "$source")"
done

echo
echo "===== VERIFICA E RIAVVIO ====="

if systemctl restart "$SERVICE" &&
   systemctl is-active --quiet "$SERVICE"; then

    echo "OK: Agent aggiornato"
    echo "Versione: $PACKAGE_VERSION"
    echo "Backup conservato: $BACKUP"

else
    echo "ERRORE: aggiornamento fallito"
    echo "Avvio rollback..."

    systemctl stop "$SERVICE" || true

    tar -xzf "$BACKUP/installation.tar.gz" -C /

    # Elimina soltanto i moduli introdotti dall'aggiornamento fallito.
    if (( ${#NEW_MODULES[@]} )); then
        rm -f -- "${NEW_MODULES[@]}"
    fi

    systemctl daemon-reload
    systemctl restart "$SERVICE" || true

    if systemctl is-active --quiet "$SERVICE"; then
        echo "ROLLBACK COMPLETATO"
    else
        echo "ATTENZIONE: verificare manualmente il servizio"
    fi

    exit 1
fi
