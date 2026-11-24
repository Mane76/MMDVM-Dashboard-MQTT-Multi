"""Esecutore comandi MMDVMHost con connessione MQTT dedicata.

Nessuna connessione viene aperta durante l'importazione.
"""

import queue
import threading
import time

import paho.mqtt.client as mqtt

from mobile_commands import validate
from mobile_mqtt import classify_response, parse_status


class MMDVMExecutor:
    def __init__(self, broker, topic_base, timeout=8):
        if not topic_base or any(c in topic_base for c in "+#\x00"):
            raise ValueError("Invalid MMDVM topic")

        self.broker = broker
        self.base = topic_base.strip("/")
        self.timeout = timeout
        self.lock = threading.Lock()

    def execute(self, command):
        command = validate(command)

        # Una sola operazione per esecutore.
        if not self.lock.acquire(blocking=False):
            return {
                "success": False,
                "message": "Executor busy",
                "modes": {},
            }

        try:
            return self._exchange(command)
        finally:
            self.lock.release()

    def _exchange(self, command):
        responses = queue.Queue()
        connected = threading.Event()
        subscribed = threading.Event()

        client = mqtt.Client(
            client_id="",
            clean_session=True,
        )

        if self.broker.get("username"):
            client.username_pw_set(
                self.broker["username"],
                self.broker.get("password") or None,
            )

        if self.broker.get("tls"):
            client.tls_set()

        def on_connect(c, userdata, flags, rc):
            if rc == 0:
                connected.set()
                c.subscribe(self.base + "/response", qos=1)

        def on_subscribe(c, userdata, mid, granted_qos):
            subscribed.set()

        def on_message(c, userdata, msg):
            if msg.topic == self.base + "/response" and not msg.retain:
                responses.put(msg.payload.decode("utf-8", "replace"))

        client.on_connect = on_connect
        client.on_subscribe = on_subscribe
        client.on_message = on_message

        try:
            client.connect(
                self.broker["host"],
                int(self.broker.get("port", 1883)),
                keepalive=30,
            )
            client.loop_start()

            if not connected.wait(self.timeout):
                raise TimeoutError("MQTT connection timeout")

            if not subscribed.wait(self.timeout):
                raise TimeoutError("MQTT subscription timeout")

            def send(value):
                info = client.publish(
                    self.base + "/command",
                    value,
                    qos=1,
                    retain=False,
                )
                if info.rc != mqtt.MQTT_ERR_SUCCESS:
                    raise RuntimeError("MQTT publish failed")

            # Svuotiamo eventuali risposte precedenti.
            while not responses.empty():
                responses.get_nowait()

            send(command)

            deadline = time.monotonic() + self.timeout

            while time.monotonic() < deadline:
                try:
                    response = responses.get(
                        timeout=max(0.01, deadline - time.monotonic())
                    )
                except queue.Empty:
                    break

                kind = classify_response(response)

                if kind == "error":
                    return {
                        "success": False,
                        "message": "MMDVMHost returned KO",
                        "modes": {},
                    }

                if kind == "ack":
                    break
            else:
                raise TimeoutError("Command ACK timeout")

            if kind != "ack":
                raise TimeoutError("Command ACK timeout")

            send("status")
            deadline = time.monotonic() + self.timeout

            while time.monotonic() < deadline:
                try:
                    response = responses.get(
                        timeout=max(0.01, deadline - time.monotonic())
                    )
                except queue.Empty:
                    break

                if classify_response(response) == "status":
                    return {
                        "success": True,
                        "message": "ACK received; status retrieved",
                        "modes": parse_status(response),
                    }

            raise TimeoutError("Status response timeout")

        except Exception as exc:
            return {
                "success": False,
                "message": str(exc)[:256],
                "modes": {},
            }

        finally:
            client.disconnect()
            client.loop_stop()
