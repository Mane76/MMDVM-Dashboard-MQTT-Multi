"""Motore MQTT non bloccante per MMDVM Mobile Control.

Non apre connessioni e non pubblica autonomamente.
Il collector dovrà chiamare tick() e on_response().
"""

import time

from mobile_commands import validate
from mobile_mqtt import classify_response, parse_status


class CommandEngine:
    def __init__(self, publish, timeout=8):
        self.publish = publish
        self.timeout = timeout
        self.phase = "idle"
        self.command = None
        self.deadline = 0
        self.result = None
        self.modes = None

    @property
    def busy(self):
        return self.phase in ("ack", "status")

    def start(self, command):
        if self.busy:
            raise RuntimeError("Command already running")

        self.command = validate(command)
        self.result = None
        self.modes = None

        self.phase = "ack"
        self.deadline = time.monotonic() + self.timeout

        try:
            self.publish(self.command)
        except Exception as exc:
            self.finish(False, str(exc))

    def on_response(self, payload):
        if not self.busy:
            return

        kind = classify_response(payload)

        if kind == "error":
            self.finish(False, "MMDVMHost rejected command")
            return

        if self.phase == "ack" and kind == "ack":
            self.phase = "status"
            self.deadline = time.monotonic() + self.timeout
            try:
                self.publish("status")
            except Exception as exc:
                self.finish(False, str(exc))
            return

        if self.phase == "status" and kind == "status":
            self.modes = parse_status(payload)
            command = self.command

            if command.startswith(("enable ", "disable ")):
                action, mode = command.split(" ", 1)
                state = self.modes.get(mode)

                if mode == "fm":
                    expected = "conn" if action == "enable" else "n/a"
                    if state != expected:
                        self.finish(False, "FM state does not match command")
                        return

                elif action == "disable" and state != "n/a":
                    self.finish(False, "Mode still enabled: " + mode)
                    return

                elif action == "enable" and state == "n/a":
                    self.finish(
                        False,
                        "Mode enable not confirmed: " + mode
                    )
                    return

            self.finish(True, "ACK received; status verified")

    def tick(self):
        if self.busy and time.monotonic() >= self.deadline:
            self.finish(False, "MQTT response timeout")

    def finish(self, success, message):
        self.result = {
            "success": success,
            "message": message,
            "command": self.command,
            "modes": self.modes,
        }
        self.phase = "done"

    def reset(self):
        self.phase = "idle"
        self.command = None
        self.result = None
        self.modes = None


if __name__ == "__main__":
    sent = []
    engine = CommandEngine(sent.append)

    engine.start("enable dmr")
    assert sent == ["enable dmr"]

    engine.on_response("OK")
    assert sent == ["enable dmr", "status"]

    engine.on_response(
        "dstar:disc dmr:conn ysf:disc "
        "p25:n/a nxdn:n/a fm:conn"
    )

    assert engine.result["success"]
    assert engine.result["modes"]["dmr"] == "conn"

    engine.reset()
    engine.start("disable ysf")
    engine.on_response("KO")
    assert engine.result["success"] is False

    print("OK: sequenza comando e conferma")
    print("OK: richiesta status successiva")
    print("OK: risposta KO gestita")
    print("OK: motore non bloccante")
    print("OK: nessuna connessione MQTT")
