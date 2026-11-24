#!/usr/bin/env python3
"""Dedicated IR3UCO Agent subscriber; never touches the shared event collector."""
import json
import logging
import os
import time
from pathlib import Path

import paho.mqtt.client as mqtt
import remote_agent

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('ir3uco-agent')
settings_path = Path(os.environ['MMDVM_NODE_DATA']) / 'mqtt-settings.json'


def settings():
    data = json.loads(settings_path.read_text(encoding='utf-8'))
    if data.get('agent_same_broker') is not False:
        raise ValueError('Agent separate broker is not enabled')
    if not remote_agent.enabled() or remote_agent.node_id().lower() != 'ir3uco':
        raise ValueError('Not configured for remote IR3UCO')
    return data


def main():
    while True:
        try:
            cfg = settings()
            prefix = remote_agent.prefix()
            if prefix != 'mmdvm-agent/ir3uco':
                raise ValueError(f'Unexpected Agent prefix: {prefix!r}')
            client = mqtt.Client(client_id='multi-ir3uco-agent-subscriber')
            username = cfg.get('agent_username') or ''
            if username:
                client.username_pw_set(username, cfg.get('agent_password') or None)
            if cfg.get('agent_tls'):
                client.tls_set()

            def on_connect(c, userdata, flags, rc):
                if rc == 0:
                    c.subscribe(prefix + '/#', qos=1)
                    log.info('Connected to Agent broker; subscribed %s/#', prefix)
                else:
                    log.error('Agent MQTT connection refused (rc=%s)', rc)

            def on_message(c, userdata, msg):
                try:
                    if remote_agent.ingest(msg.topic, msg.payload):
                        log.info('Received Agent %s', msg.topic)
                except Exception:
                    log.exception('Invalid Agent message on %s', msg.topic)

            client.on_connect = on_connect
            client.on_message = on_message
            client.connect(cfg.get('agent_host') or '127.0.0.1', int(cfg.get('agent_port') or 1883), 60)
            client.loop_forever(retry_first_connection=True)
        except Exception:
            log.exception('Agent subscriber error; retrying in 5 seconds')
            time.sleep(5)


if __name__ == '__main__':
    main()
