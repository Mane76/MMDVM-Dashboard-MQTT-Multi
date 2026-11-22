# IR3ZZO_RUNTIME_MODES_TOPIC_FIX3
"""Read-only runtime-mode state from MMDVMHost's native MQTT remote control."""
import json
import re
import time
from pathlib import Path
from node_paths import data_file
import config as dashboard_config
from mqtt_settings import load as mqtt_settings

STATE = data_file('runtime-modes.json')
MODES = ('dstar', 'dmr', 'ysf', 'fm', 'p25', 'nxdn', 'm17')
STATUS_PATTERN = re.compile(r'(?<!\S)(dstar|dmr|ysf|fm|p25|nxdn|m17):(conn|disc|n/a)(?!\S)', re.I)

def topic_base(settings=None):
    """Resolve MMDVMHost *native* control topic, preserving MQTT case.

    A node's Agent prefix may use lowercase node_id even if MMDVMHost
    publishes on mmdvm/IR3ZZO/response. Prefer its configured callsign.
    An explicit remote_control_topic always wins.
    """
    configured = dashboard_config.get('deployment', 'remote_control_topic', '').strip().strip('/')
    if configured:
        base = configured.removesuffix('/command').removesuffix('/response')
    else:
        callsign = dashboard_config.get('repeater', 'callsign', '').strip().upper()
        if re.fullmatch(r'[A-Z0-9]{2,12}(?:-[0-9]{1,2})?', callsign):
            base = 'mmdvm/' + callsign
        else:
            base = ''
        if not base:
            prefix = dashboard_config.get('deployment', 'agent_topic_prefix', '').strip().strip('/')
            if prefix.lower().endswith('/agent/v1'):
                base = prefix[:-len('/agent/v1')]
        if not base:
            topic = (settings or mqtt_settings()).get('topic', '').strip('/')
            if topic.endswith('/json') and not any(c in topic for c in '+#'):
                base = topic[:-len('/json')]
    if not base or any(c in base for c in '+# ') or len(base) > 200:
        return None
    return base

def parse_response(value):
    if not isinstance(value, str) or len(value) > 1024:
        return None
    pairs = {k.lower(): v.lower() for k, v in STATUS_PATTERN.findall(value)}
    # Reject bare OK/error responses and unrelated payloads.
    return pairs if len(pairs) >= 2 else None

def save_response(topic, payload, expected_base):
    if not expected_base or topic != expected_base + '/response':
        return False
    modes = parse_response(payload.decode('utf-8', 'replace'))
    if not modes:
        return False
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE.with_name(STATE.name + '.tmp')
    temp.write_text(json.dumps({'topic': expected_base, 'received_at': time.time(), 'modes': modes}), encoding='utf-8')
    temp.replace(STATE)
    return True

def snapshot():
    base = topic_base()
    try:
        data = json.loads(STATE.read_text(encoding='utf-8'))
        fresh = (base and data.get('topic') == base and
                 0 <= time.time() - float(data['received_at']) <= 45)
        if fresh:
            return {'available': True, 'topic': base, 'modes': {m: data.get('modes', {}).get(m, 'unknown') for m in MODES}}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return {'available': False, 'topic': base, 'modes': {m: 'unknown' for m in MODES}}
