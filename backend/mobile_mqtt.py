"""Trasporto MQTT per Mobile Control - fase di preparazione.

Nessuna connessione automatica e nessun comando pubblicato.
"""
import queue
import threading

import paho.mqtt.client as mqtt

from mobile_commands import validate


class MobileMQTT:
    def __init__(self, topic_base):
        if not isinstance(topic_base, str):
            raise ValueError("Invalid MQTT topic")
        topic_base = topic_base.strip("/")
        if (not topic_base or len(topic_base) > 200
                or any(c in topic_base for c in "+# \t\r\n")):
            raise ValueError("Invalid MQTT topic")

        self.base = topic_base
        self.command_topic = topic_base + "/command"
        self.response_topic = topic_base + "/response"
        self.lock = threading.Lock()
        self.responses = queue.Queue(maxsize=100)

    def on_message(self, client, userdata, message):
        if message.topic != self.response_topic:
            return

        payload = message.payload.decode("utf-8", "replace")

        try:
            self.responses.put_nowait(payload)
        except queue.Full:
            pass

    def prepare_command(self, command):
        return self.command_topic, validate(command)


if __name__ == "__main__":
    control = MobileMQTT("mmdvm/TEST")

    assert control.prepare_command("enable dmr") == (
        "mmdvm/TEST/command", "enable dmr"
    )

    assert control.response_topic == "mmdvm/TEST/response"

    print("OK: trasporto MQTT preparato")


def parse_status(payload):
    """Interpreta la risposta al comando status di MMDVMHost."""
    import re

    if not isinstance(payload, str):
        return None

    modes = {}
    for mode, state in re.findall(
        r'(?<!\S)(dstar|dmr|ysf|fm|p25|nxdn|m17):(conn|disc|n/a)(?!\S)',
        payload,
        re.IGNORECASE,
    ):
        modes[mode.lower()] = state.lower()

    required = {"dstar", "dmr", "ysf", "fm", "p25", "nxdn"}
    return modes if required.issubset(modes) else None


def classify_response(payload):
    """Classifica una risposta MQTT senza eseguire comandi."""
    if payload == "OK":
        return "ack"
    if payload == "KO":
        return "error"

    modes = parse_status(payload)
    if modes is not None:
        return "status"

    return "unknown"


if __name__ == "__main__":
    sample = (
        "dstar:disc dmr:conn ysf:disc "
        "p25:n/a nxdn:n/a fm:conn"
    )

    assert classify_response("OK") == "ack"
    assert classify_response("KO") == "error"
    assert classify_response(sample) == "status"
    assert classify_response("invalid") == "unknown"

    assert parse_status(sample)["dmr"] == "conn"
    assert parse_status(sample)["p25"] == "n/a"

    print("OK: interpretazione risposte MQTT superata")


class CommandRejected(RuntimeError):
    pass


class ResponseTimeout(RuntimeError):
    pass


def wait_response(responses, expected, timeout=8):
    """Attende una risposta valida da una coda MQTT."""
    import time

    deadline = time.monotonic() + timeout

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ResponseTimeout("Nessuna risposta valida")

        try:
            payload = responses.get(timeout=remaining)
        except queue.Empty:
            raise ResponseTimeout("Nessuna risposta valida") from None

        kind = classify_response(payload)

        if kind == "error":
            raise CommandRejected("Comando rifiutato da MMDVMHost")

        if expected == "ack" and kind == "ack":
            return payload

        if expected == "status" and kind == "status":
            return parse_status(payload)


if __name__ == "__main__":
    test_queue = queue.Queue()
    test_queue.put("messaggio sconosciuto")
    test_queue.put("OK")
    assert wait_response(test_queue, "ack", timeout=1) == "OK"

    test_queue.put("KO")
    try:
        wait_response(test_queue, "ack", timeout=1)
        raise AssertionError("KO non rilevato")
    except CommandRejected:
        pass

    test_queue.put(
        "dstar:disc dmr:conn ysf:disc "
        "p25:n/a nxdn:n/a fm:conn"
    )
    assert wait_response(test_queue, "status", timeout=1)["dmr"] == "conn"

    try:
        wait_response(queue.Queue(), "ack", timeout=0.01)
        raise AssertionError("Timeout non rilevato")
    except ResponseTimeout:
        pass

    print("OK: gestione ACK, KO, status e timeout superata")
