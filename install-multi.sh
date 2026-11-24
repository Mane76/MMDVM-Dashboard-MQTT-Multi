#!/usr/bin/env bash
# MMDVM Dashboard MQTT Multi - installer Debian compatibile con altri siti HTTPS
# --check: controlli senza modifiche; --install: installazione
set -Eeuo pipefail
umask 077
SRC="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP=${MMDVM_APP_ROOT:-/opt/mmdvm-dashboard-multi}
STATE=/var/lib/mmdvm-dashboard-multi
USER_NAME=mmdvm-dashboard-multi
PORT=8090
MQTT_PORT=1883
SITE=mmdvm-dashboard-multi
MODE=${1:---check}
fail() { echo "ERRORE: $*" >&2; exit 1; }
info() { echo "==> $*"; }
[[ "$MODE" == --check || "$MODE" == --install ]] || fail 'Uso: install-multi.sh [--check|--install]'
[[ $EUID -eq 0 && -f /etc/debian_version ]] || fail 'Richiesto Debian con accesso root.'
[[ "$APP" == /* && "$APP" != / && "$APP" != *".."* && "$APP" != *$'\n'* ]] || fail 'Percorso APP non valido.'
[[ "$APP" != "$SRC" ]] || fail 'Directory applicazione uguale ai sorgenti.'
[[ -f "$SRC/UPDATE_REPOSITORY" && ! -L "$SRC/UPDATE_REPOSITORY" ]] || fail 'UPDATE_REPOSITORY mancante.'
UPDATE_REPOSITORY=$(cat "$SRC/UPDATE_REPOSITORY")
[[ "$UPDATE_REPOSITORY" == 'Mauro-go/MMDVM-Dashboard-MQTT-Multi' ]] || fail 'Repository aggiornamenti non autorizzato.'
for f in backend/app.py backend/multi.py backend/multi_worker.py backend/mqtt_collector.py backend/admin_api.py backend/telegram_bot.py config/dashboard.ini.example frontend/index.html admin/multi.html VERSION scripts/update-dmr-ids.sh systemd/mmdvm-multi-update.timer systemd/mmdvm-dmr-ids-update@.timer systemd/mmdvm-dmr-ids-update@.service; do
  [[ -f "$SRC/$f" ]] || fail "File mancante: $f"
done
[[ ! -e "$APP" && ! -L "$APP" ]] || fail "Installazione già presente: $APP (non eliminare i dati: serve una procedura di ripristino)."
for unit in mmdvm-dashboard-multi mmdvm-multi-collector mmdvm-multi-telegram mmdvm-multi-update; do
  [[ ! -e "/etc/systemd/system/$unit.service" ]] || fail "Servizio già presente: $unit"
done
[[ ! -e /etc/systemd/system/mmdvm-multi-update.timer ]] || fail 'Timer Multi già esistente.'
for path in "/etc/apache2/sites-available/$SITE.conf" "/etc/apache2/ssl/$SITE"; do
  [[ ! -e "$path" && ! -L "$path" ]] || fail "Configurazione Multi già esistente: $path"
done
# Broker MQTT esistente: non modificare Mosquitto.
MQTT_MODE=new
command -v ss >/dev/null || fail 'Comando ss mancante.'
if ss -H -ltn "sport = :$MQTT_PORT" | grep -q .; then
  MQTT_MODE=existing
elif dpkg-query -W -f='${Status}' mosquitto 2>/dev/null | grep -qx 'install ok installed'; then
  fail 'Mosquitto installato ma non in ascolto sulla 1883.'
fi
if [[ "$MQTT_MODE" == new ]]; then
  for path in "/etc/mosquitto/conf.d/$SITE.conf" "/etc/mosquitto/$SITE.passwd"; do
    [[ ! -e "$path" && ! -L "$path" ]] || fail "Conflitto: $path"
  done
fi
info "Broker MQTT: $MQTT_MODE"
# I servizi web preesistenti non vengono fermati o riconfigurati.
WEB_MODE=none
if command -v ss >/dev/null; then
  [[ -z $(ss -H -ltn "sport = :$PORT") ]] || fail "Porta backend $PORT occupata."
  if ss -H -ltn 'sport = :443' | grep -q .; then
    # Se la 443 è già occupata, NON tentare di avviare un nuovo server web.
    if command -v apache2ctl >/dev/null && apache2ctl -S 2>&1 | grep -Eq '(\*:443|:443[[:space:]])' && systemctl is-active --quiet apache2; then
      WEB_MODE=apache_existing
    else
      WEB_MODE=external
    fi
  elif command -v apache2ctl >/dev/null; then
    WEB_MODE=apache_existing
  else
    WEB_MODE=apache_new
  fi
else
  # Assenza di ss: scelta prudente, non installare né toccare un server HTTPS.
  WEB_MODE=external
fi
# Un server Apache già presente ma non attivo non va avviato se la 443 è usata altrove.
if [[ "$WEB_MODE" == apache_new && -e /etc/apache2 ]]; then WEB_MODE=apache_existing; fi
info "Modalità web rilevata: $WEB_MODE"
if [[ "$WEB_MODE" == external ]]; then
  info 'La porta 443 appartiene a un altro servizio: installerà SOLO il backend locale.'
  info 'Per accesso HTTPS occorrerà configurare il proxy web esistente verso 127.0.0.1:8090.'
fi
if [[ "$WEB_MODE" == apache_existing ]]; then
  apache2ctl configtest >/dev/null || fail 'Apache preesistente con configurazione non valida.'
fi
if [[ "$MODE" == --check ]]; then
  info 'Controlli preliminari superati; nessuna modifica eseguita.'
  info 'Avvio installazione: ./install-multi.sh --install'
  exit 0
fi
read -r -p 'Nominativo primo ripetitore (es. IR3ZZO): ' CALLSIGN
CALLSIGN=${CALLSIGN%$'\r'}
[[ "$CALLSIGN" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{1,19}$ ]] || fail 'Nominativo non valido.'
read -r -p "Node ID Agent [$CALLSIGN]: " NODE_ID
NODE_ID=${NODE_ID:-$CALLSIGN}
[[ "$NODE_ID" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$ ]] || fail 'Node ID non valido.'
read -r -p "Prefisso Agent [mmdvm/$NODE_ID/agent/v1]: " PREFIX
PREFIX=${PREFIX:-mmdvm/$NODE_ID/agent/v1}
[[ "$PREFIX" =~ ^[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*$ ]] || fail 'Prefisso Agent non valido.'
if [[ "$MQTT_MODE" == existing ]]; then
  info 'Inserire username e password del broker esistente.'
fi
read -r -p 'Username MQTT [mmdvm-dashboard]: ' MQTT_USER
MQTT_USER=${MQTT_USER:-mmdvm-dashboard}
[[ "$MQTT_USER" =~ ^[A-Za-z0-9_.-]+$ ]] || fail 'Username MQTT non valido.'
read -r -s -p 'Password MQTT (non visibile): ' MQTT_PASSWORD; echo
[[ -n "$MQTT_PASSWORD" ]] || fail 'Password MQTT obbligatoria.'
read -r -s -p 'Ripeti password MQTT: ' MQTT_CONFIRM; echo
[[ "$MQTT_PASSWORD" == "$MQTT_CONFIRM" ]] || fail 'Le password non corrispondono.'
unset MQTT_CONFIRM
DETECTED_IP=$(hostname -I 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i !~ /^127[.]/ && $i !~ /:/){print $i;exit}}')
HTTPS_NAME=${DETECTED_IP:-127.0.0.1}
if [[ "$WEB_MODE" != external ]]; then
  read -r -p "Nome host HTTPS dedicato alla dashboard [$HTTPS_NAME]: " HTTPS_NAME_IN
  HTTPS_NAME=${HTTPS_NAME_IN:-$HTTPS_NAME}
  [[ "$HTTPS_NAME" =~ ^[A-Za-z0-9._-]+$ ]] || fail 'Indirizzo HTTPS non valido.'
  if [[ "$WEB_MODE" == apache_existing ]] && apache2ctl -S 2>&1 | grep -Fq "namevhost $HTTPS_NAME"; then
    fail "ServerName $HTTPS_NAME già presente in Apache: scegliere un hostname dedicato."
  fi
fi
cat <<SUMMARY

Riepilogo:
  App:       $APP
  Ripetitore: $CALLSIGN
  Agent:     $PREFIX
  Broker MQTT: 127.0.0.1:$MQTT_PORT ($MQTT_MODE)
  Backend:   127.0.0.1:$PORT
  Web:       $WEB_MODE
  HTTPS:     $(if [[ "$WEB_MODE" == external ]]; then echo 'gestito dal servizio già esistente (proxy da configurare)'; else echo "https://$HTTPS_NAME/"; fi)

SUMMARY
read -r -p 'Confermi installazione? Scrivi INSTALLA: ' CONFIRM
[[ "$CONFIRM" == INSTALLA ]] || { info 'Annullato.'; exit 0; }
[[ ! -e "$APP" ]] || fail 'Directory APP comparsa durante la conferma.'
export DEBIAN_FRONTEND=noninteractive
info 'Installazione dipendenze'
apt-get update
PACKAGES=(openssl ca-certificates curl rsync python3 python3-flask python3-werkzeug python3-psutil python3-paho-mqtt)
if [[ "$MQTT_MODE" == new ]]; then PACKAGES+=(mosquitto mosquitto-clients); fi
if [[ "$WEB_MODE" == apache_new ]]; then PACKAGES+=(apache2); fi
apt-get install -y "${PACKAGES[@]}"
id "$USER_NAME" >/dev/null 2>&1 || useradd --system --home "$APP" --shell /usr/sbin/nologin "$USER_NAME"
install -d -o root -g root -m 0755 "$APP"
# Copia i sorgenti senza includere dati runtime o file temporanei.
for dir in backend frontend admin; do
  mkdir -p "$APP/$dir"
  rsync -a     --exclude='__pycache__/'     --exclude='*.pyc'     --exclude='*.db'     --exclude='*.sqlite3'     --exclude='*.bak*'     "$SRC/$dir/" "$APP/$dir/"
done
install -o root -g root -m 0644 "$SRC/VERSION" "$APP/VERSION"
install -d -o root -g root -m 0755 "$APP/scripts"
for f in "$SRC"/scripts/update_*.py; do install -o root -g root -m 0644 "$f" "$APP/scripts/$(basename "$f")"; done
install -o root -g root -m 0755 "$SRC/scripts/update-dmr-ids.sh" "$APP/scripts/update-dmr-ids.sh"
cat > /etc/systemd/system/mmdvm-multi-update.service <<UNIT
[Unit]
Description=MMDVM Dashboard Multi verified updater
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
User=root
Group=root
WorkingDirectory=$APP/scripts
Environment=MMDVM_APP_ROOT=$APP
ExecStart=/usr/bin/python3 $APP/scripts/update_worker.py
TimeoutStartSec=600
UMask=0077
NoNewPrivileges=true
ProtectHome=true
ProtectSystem=full
ReadWritePaths=$APP
UNIT
install -o root -g root -m 0644 "$SRC/systemd/mmdvm-multi-update.timer" /etc/systemd/system/mmdvm-multi-update.timer
install -o root -g root -m 0644 "$SRC/systemd/mmdvm-dmr-ids-update@.service" /etc/systemd/system/mmdvm-dmr-ids-update@.service
install -o root -g root -m 0644 "$SRC/systemd/mmdvm-dmr-ids-update@.timer" /etc/systemd/system/mmdvm-dmr-ids-update@.timer
install -d -o root -g "$USER_NAME" -m 2770 "$APP/config"
install -o root -g root -m 0644 "$SRC/UPDATE_REPOSITORY" "$APP/config/update-repository"
APP="$APP" PORT="$PORT" python3 - <<'PYRUNTIME'
import json,os
from pathlib import Path
app=Path(os.environ['APP'])
p=app/'config/update-runtime.json'
p.write_text(json.dumps({'mode':'multi','port':int(os.environ['PORT']),'services':['mmdvm-dashboard-multi.service','mmdvm-multi-collector.service','mmdvm-multi-telegram.service']},indent=2)+'\n')
p.chmod(0o644)
PYRUNTIME
install -d -o "$USER_NAME" -g "$USER_NAME" -m 0750 "$APP/data" "$APP/data/databases" "$STATE"
install -o root -g "$USER_NAME" -m 0644 "$SRC/config/dashboard.ini.example" "$APP/config/dashboard.ini.example"
install -o root -g "$USER_NAME" -m 0660 "$SRC/config/dashboard.ini.example" "$APP/config/dashboard.ini"
find "$APP/backend" "$APP/frontend" "$APP/admin" -type f \( -name '*.bak-*' -o -name '*.pyc' \) -delete
find "$APP/backend" "$APP/frontend" "$APP/admin" -type d -name __pycache__ -prune -exec rm -rf {} +
chown -R root:root "$APP/backend" "$APP/frontend" "$APP/admin"
find "$APP/backend" "$APP/frontend" "$APP/admin" -type d -exec chmod 0755 {} +
find "$APP/backend" "$APP/frontend" "$APP/admin" -type f -exec chmod 0644 {} +
export APP CALLSIGN NODE_ID PREFIX MQTT_USER MQTT_PASSWORD
python3 - <<'PYCONFIG'
import configparser,json,os
from pathlib import Path
root=Path(os.environ['APP']); c=configparser.ConfigParser(interpolation=None)
p=root/'config/dashboard.ini'; c.read(p)
c.set('repeater','callsign',os.environ['CALLSIGN'])
c.set('deployment','mode','remote')
c.set('deployment','node_id',os.environ['NODE_ID'])
c.set('deployment','agent_topic_prefix',os.environ['PREFIX'])
c.set('paths','dmr_ids',str(root/'data/databases/DMRIds.dat'))
c.set('paths','tg_list_dir',str(root/'data/databases'))
c.set('paths','tg_list_file','TG_List.csv')
with p.open('w') as h: c.write(h)
settings=dict(host='127.0.0.1',port=1883,topic=f"mmdvm/{os.environ['CALLSIGN']}/json",username=os.environ['MQTT_USER'],password=os.environ['MQTT_PASSWORD'],tls=False)
q=root/'data/mqtt-settings.json'; q.write_text(json.dumps(settings,indent=2)+'\n'); q.chmod(0o600)
PYCONFIG
chown "$USER_NAME:$USER_NAME" "$APP/data/mqtt-settings.json"
chmod 0660 "$APP/config/dashboard.ini"
printf 'MODE,TS,TG,LABEL,URL\nDMR,1,222,BM-IT,\n' > "$APP/data/databases/TG_List.csv"
chown "$USER_NAME:$USER_NAME" "$APP/data/databases/TG_List.csv"
chmod 0640 "$APP/data/databases/TG_List.csv"
[[ -s "$APP/data/databases/DMRIds.dat" ]] || "$APP/scripts/update-dmr-ids.sh" "$APP" || fail 'Download database DMR fallito.'
openssl rand -hex 32 > "$STATE/session-secret"
openssl rand -hex 24 > "$STATE/setup-token"
chown -R "$USER_NAME:$USER_NAME" "$STATE"
chmod 0600 "$STATE/session-secret" "$STATE/setup-token"
if [[ "$MQTT_MODE" == new ]]; then
  TMPPASS=$(mktemp /etc/mosquitto/.multi-pass.XXXXXXXX)
  trap 'rm -f -- "${TMPPASS:-}"' EXIT
  printf '%s:%s\n' "$MQTT_USER" "$MQTT_PASSWORD" > "$TMPPASS"
  mosquitto_passwd -U "$TMPPASS"
  chown mosquitto:mosquitto "$TMPPASS"
  chmod 0600 "$TMPPASS"
  mv -- "$TMPPASS" "/etc/mosquitto/$SITE.passwd"
  TMPPASS=
  cat > "/etc/mosquitto/conf.d/$SITE.conf" <<MOSQ
  listener 1883 0.0.0.0
  allow_anonymous false
  password_file /etc/mosquitto/$SITE.passwd
MOSQ
  chmod 0644 "/etc/mosquitto/conf.d/$SITE.conf"
fi
unset MQTT_PASSWORD
# La dashboard Flask serve già index.html e i file statici: NON toccare /var/www/html.
# Configura un VirtualHost aggiuntivo solo quando Apache è il server scelto.
if [[ "$WEB_MODE" == apache_existing || "$WEB_MODE" == apache_new ]]; then
  CERTDIR="/etc/apache2/ssl/$SITE"
  install -d -m 0700 "$CERTDIR"
  if [[ "$HTTPS_NAME" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then SAN="IP:$HTTPS_NAME"; else SAN="DNS:$HTTPS_NAME"; fi
  openssl req -x509 -nodes -newkey rsa:3072 -sha256 -days 825 \
    -keyout "$CERTDIR/server.key" -out "$CERTDIR/server.crt" \
    -subj "/CN=$HTTPS_NAME" -addext "subjectAltName=$SAN" >/dev/null 2>&1
  chmod 0600 "$CERTDIR/server.key"
  chmod 0644 "$CERTDIR/server.crt"
  cat > "/etc/apache2/sites-available/$SITE.conf" <<APACHE
<VirtualHost *:443>
    ServerName $HTTPS_NAME
    SSLEngine on
    SSLCertificateFile $CERTDIR/server.crt
    SSLCertificateKeyFile $CERTDIR/server.key
    ProxyPreserveHost On
    ProxyPass / http://127.0.0.1:$PORT/
    ProxyPassReverse / http://127.0.0.1:$PORT/
</VirtualHost>
APACHE
fi
MQTT_UNIT=""
if [[ "$MQTT_MODE" == new ]] || systemctl is-active --quiet mosquitto.service; then
  MQTT_UNIT=mosquitto.service
fi
cat > /etc/systemd/system/mmdvm-multi-collector.service <<UNIT
[Unit]
Description=MMDVM Multi MQTT Collector
Wants=network-online.target $MQTT_UNIT
After=network-online.target $MQTT_UNIT
[Service]
Type=simple
User=$USER_NAME
Group=$USER_NAME
WorkingDirectory=$APP/backend
Environment=MMDVM_NODE_CONFIG=$APP/config/dashboard.ini
Environment=MMDVM_NODE_DATA=$APP/data
Environment=MMDVM_DASHBOARD_STATE_DIR=$STATE
ExecStart=/usr/bin/python3 -u $APP/backend/mqtt_collector.py
Restart=always
RestartSec=5
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=true
ReadWritePaths=$APP/data $STATE
[Install]
WantedBy=multi-user.target
UNIT
cat > /etc/systemd/system/mmdvm-dashboard-multi.service <<UNIT
[Unit]
Description=MMDVM Dashboard MQTT Multi
Wants=network-online.target $MQTT_UNIT mmdvm-multi-collector.service
After=network-online.target $MQTT_UNIT
[Service]
Type=simple
User=$USER_NAME
Group=$USER_NAME
WorkingDirectory=$APP/backend
Environment=MMDVM_NODE_CONFIG=$APP/config/dashboard.ini
Environment=MMDVM_NODE_DATA=$APP/data
Environment=MMDVM_DASHBOARD_STATE_DIR=$STATE
Environment=MMDVM_DASHBOARD_PORT=$PORT
ExecStart=/usr/bin/python3 -u $APP/backend/app.py
Restart=always
RestartSec=5
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=true
ReadWritePaths=$APP/data $APP/config $STATE
[Install]
WantedBy=multi-user.target
UNIT
cat > /etc/systemd/system/mmdvm-multi-telegram.service <<UNIT
[Unit]
Description=MMDVM Dashboard Multi Telegram Notifications
Wants=network-online.target $MQTT_UNIT
After=network-online.target $MQTT_UNIT
[Service]
Type=simple
User=$USER_NAME
Group=$USER_NAME
WorkingDirectory=$APP/backend
Environment=MMDVM_NODE_CONFIG=$APP/config/dashboard.ini
Environment=MMDVM_NODE_DATA=$APP/data
Environment=MMDVM_DASHBOARD_STATE_DIR=$STATE
ExecStart=/usr/bin/python3 -u $APP/backend/telegram_bot.py
Restart=always
RestartSec=5
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=true
ReadWritePaths=$APP/data $STATE
[Install]
WantedBy=multi-user.target
UNIT
if [[ "$WEB_MODE" == apache_existing || "$WEB_MODE" == apache_new ]]; then
  info 'Validazione Apache senza sovrascrivere gli altri siti'
  a2enmod proxy proxy_http ssl >/dev/null
  a2ensite "$SITE" >/dev/null
  apache2ctl configtest || fail 'Configurazione Apache non valida.'
fi
systemctl daemon-reload
if [[ "$MQTT_MODE" == new ]]; then
  systemctl enable --now mosquitto
  systemctl restart mosquitto
fi
systemctl enable --now mmdvm-multi-collector.service mmdvm-dashboard-multi.service mmdvm-multi-telegram.service
if [[ "$WEB_MODE" == apache_new ]]; then systemctl enable --now apache2; fi
if [[ "$WEB_MODE" == apache_existing ]]; then systemctl reload apache2; fi
if [[ "$MQTT_MODE" == new ]]; then
  systemctl is-active --quiet mosquitto || fail 'Mosquitto non attivo.'
fi
for svc in mmdvm-dashboard-multi mmdvm-multi-collector mmdvm-multi-telegram; do
  systemctl is-active --quiet "$svc" || fail "Servizio non attivo: $svc"
done
systemctl enable --now "mmdvm-dmr-ids-update@$(basename "$APP").timer"
systemctl enable --now mmdvm-multi-update.timer
if [[ "$WEB_MODE" == external ]]; then
  info "Backend pronto solo in locale: http://127.0.0.1:$PORT/"
  info "Per HTTPS configurare il reverse proxy esistente verso http://127.0.0.1:$PORT/"
else
  info "Dashboard: https://$HTTPS_NAME/"
  info "Admin: https://$HTTPS_NAME/api/admin/"
fi
echo '=============================================='
echo '       CONFIGURAZIONE AMMINISTRATORE'
echo '=============================================='
echo "Setup token: $(cat "$STATE/setup-token")"
echo 'Conservare il token in luogo sicuro.'
echo '=============================================='
info 'MQTT TCP/1883 non cifrato: usare LAN/VPN o configurare TLS prima di esporlo su Internet.'
