"""Trasporto MQTT Dashboard -> Agent con correlazione per operazione.

Non esegue comandi di propria iniziativa. Il chiamante deve abilitare
esplicitamente l'invio e gestire la coda persistente.
"""
import json
import time
from mobile_agent_protocol import make_request, topics


class AgentTransport:
    def __init__(self, node_id, prefix, timeout=20):
        self.node_id = node_id
        self.topics = topics(prefix)
        self.timeout = float(timeout)
        self.pending = None
        self.result = None
        self.deadline = 0.0

    @property
    def busy(self):
        return self.pending is not None

    def start(self, client, operation_id, command):
        if self.busy:
            raise RuntimeError('Agent operation already pending')
        if client is None or not client.is_connected():
            raise RuntimeError('Agent MQTT disconnected')
        request = make_request(self.node_id, command, operation_id)
        payload = json.dumps(request, separators=(',', ':'))
        info = client.publish(self.topics['command'], payload, qos=1, retain=False)
        if info.rc != 0:
            raise RuntimeError('Agent MQTT publish failed: %s' % info.rc)
        self.pending = operation_id
        self.result = None
        self.deadline = time.monotonic() + self.timeout

    def on_message(self, topic, payload, retained=False):
        if topic != self.topics['response']:
            return False
        if retained:
            return True
        try:
            if len(payload) > 4096:
                return True
            obj = json.loads(payload)
            if (not isinstance(obj, dict)
                    or set(obj) != {'schema_version', 'node_id', 'operation_id', 'success', 'message', 'modes'}
                    or obj['schema_version'] != 1
                    or obj['node_id'] != self.node_id
                    or not isinstance(obj['success'], bool)
                    or not isinstance(obj['message'], str)
                    or not isinstance(obj['modes'], dict)):
                return True
            if self.pending is not None and obj['operation_id'] == self.pending:
                self.result = {'success': obj['success'], 'message': obj['message'][:256],
                               'modes': obj['modes']}
                self.pending = None
        except (ValueError, TypeError, UnicodeDecodeError):
            pass
        return True

    def tick(self):
        if self.pending is not None and time.monotonic() >= self.deadline:
            self.result = {'success': False,
                           'message': 'Agent response timeout; execution outcome unknown',
                           'modes': {}}
            self.pending = None

    def reset(self):
        self.pending = None
        self.result = None
        self.deadline = 0.0
