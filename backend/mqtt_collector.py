from mobile_control_health import update as mobile_heartbeat
#!/usr/bin/env python3
"""Dashboard-only MQTT collector; reconnects when private settings change.

SQLite events are retained for 48 hours. Cleanup runs at startup and once
per local calendar day after midnight, within the existing collector process.
"""
import json
import logging
import os
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
from mqtt_store import connect, save, init_gateway_links, save_gateway_link, save_gateway_connection, save_dstar_link_history
from mqtt_settings import SETTINGS, load
import remote_agent
import runtime_modes  # IR3ZZO_RUNTIME_MODES_V1
from mobile_control_settings import enabled as mobile_control_enabled
from mobile_dispatcher import MobileDispatcher
from mobile_engine import CommandEngine
from mobile_agent_transport import AgentTransport
from mobile_agent_engine import AgentCommandEngine
from multi_mqtt import accept_event
from mmdvm_log_writer import save_log, cleanup_logs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("mqtt-collector")

RETENTION_HOURS = 48
CLEANUP_CHECK_INTERVAL = 60  # seconds; no additional service or timer


def cleanup_old_events(db):
    """Remove expired events without deleting the database or WAL files.

    SQLite datetime() interprets ISO-8601 timestamps with UTC offsets and
    fractional seconds, and compares them to the current UTC time.
    """
    before = db.total_changes
    db.execute(
        "DELETE FROM events WHERE datetime(timestamp) < datetime('now', ?)",
        (f"-{RETENTION_HOURS} hours",),
    )
    db.commit()
    removed = db.total_changes - before
    # PASSIVE does not wait for dashboard readers and is safe in WAL mode.
    db.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
    logger.info("SQLite cleanup completed: removed %d events older than %d hours", removed, RETENTION_HOURS)


def on_message(client, userdata, msg):
    try:
        if userdata.get("log_topic") and mqtt.topic_matches_sub(userdata["log_topic"], msg.topic):
            save_log(msg.topic, msg.payload)
            return
        engine = userdata.get("mobile_engine")
        remote_base = userdata.get("remote_base")
        if engine is not None and remote_base and msg.topic == remote_base + "/response":
            engine.on_response(msg.payload.decode("utf-8", "replace"))
        # AGENT_TRANSPORT_ROUTING_V1: non interpretare le risposte Agent come eventi.
        agent_transport = userdata.get('agent_transport')
        if agent_transport is not None and agent_transport.on_message(msg.topic, msg.payload, msg.retain):
            return
        if runtime_modes.save_response(msg.topic, msg.payload, userdata.get('remote_base')):
            return
        if userdata.get('remote_base') and msg.topic == userdata['remote_base'] + '/response':
            return
        gateway_topics = userdata.get("gateway_topics", {})
        gateway = gateway_topics.get(msg.topic)
        if gateway:
            event = json.loads(msg.payload.decode("utf-8"))
            if isinstance(event, dict):
                save_gateway_link(userdata["db"], gateway, event)
                save_gateway_connection(userdata["db"], gateway, event)
                if gateway == "dstargateway":
                    save_dstar_link_history(userdata["db"], gateway, event)
            return

        if remote_agent.ingest(msg.topic, msg.payload):
            return
        event = json.loads(msg.payload.decode("utf-8"))
        if not isinstance(event, dict) or not accept_event(msg.topic, event):
            return
        for mode, payload in event.items():
            if not isinstance(payload, dict):
                continue
            timestamp = payload.get("timestamp") or datetime.now(timezone.utc).isoformat()
            action = payload.get("action") or payload.get("state") or ""
            save(userdata["db"], str(timestamp), str(mode), str(action), payload)
            logger.info("%s %s %s", mode, action, timestamp)
    except (ValueError, UnicodeDecodeError, TypeError) as exc:
        logger.warning("Invalid MQTT JSON: %s", exc)
    except Exception:
        logger.exception("Failed to store MQTT event")


def main():
    # Mobile Control: motore non bloccante, invio disabilitato.
    # Il publisher viene collegato al client MQTT esistente.
    # Nessun comando parte finché il dispatcher resta disabilitato.
    mobile_transport = {"client": None, "base": None}

    def mobile_publish(command):
        client = mobile_transport["client"]
        base = mobile_transport["base"]

        if not mobile_dispatcher.enabled:
            raise RuntimeError("Mobile Control disabled")

        if client is None or not base or not client.is_connected():
            raise RuntimeError("MQTT not connected")

        info = client.publish(
            base + "/command",
            payload=command,
            qos=1,
            retain=False,
        )

        if info.rc != 0:
            raise RuntimeError("MQTT publish failed: %s" % info.rc)

    mobile_engine = CommandEngine(mobile_publish)
    mobile_dispatcher = MobileDispatcher(
        "disabled",
        engine=mobile_engine
    )
    mobile_dispatcher.startup()
    db = connect()
    init_gateway_links(db)
    client = None
    agent_client = None
    agent_retry_at = 0.0
    current = None
    retry_at = 0
    last_cleanup_date = None
    next_cleanup_check = 0.0
    try:
        while True:
            now = time.monotonic()
            mobile_heartbeat()
            if now >= next_cleanup_check:
                next_cleanup_check = now + CLEANUP_CHECK_INTERVAL
                today = datetime.now().date()  # local date, midnight rollover
                if today != last_cleanup_date:
                    try:
                        cleanup_old_events(db)
                        cleanup_logs()
                        last_cleanup_date = today
                    except Exception:
                        logger.exception("SQLite cleanup failed; will retry")
                        try:
                            db.rollback()
                        except Exception:
                            pass
            try:
                settings = load()
                if not SETTINGS.exists():
                    settings.update({
                        "host": os.environ.get("MMDVM_MQTT_HOST", "127.0.0.1"),
                        "port": int(os.environ.get("MMDVM_MQTT_PORT", "1883")),
                        "topic": os.environ.get("MMDVM_MQTT_TOPIC", "mmdvm/+/json")
                    })
                # COLLECTOR_DEPLOYMENT_RELOAD_V1
                import config as dashboard_config
                dashboard_config.reload_config()

                agent_enabled = remote_agent.enabled()
                agent_prefix = remote_agent.prefix() if agent_enabled else ""

                if agent_enabled and not agent_prefix:
                    raise ValueError("Remote Agent topic prefix is empty")

                agent_separate = agent_enabled and settings.get("agent_same_broker", True) is False
                signature = json.dumps({
                    "mqtt": settings,
                    "agent_enabled": agent_enabled,
                    "agent_prefix": agent_prefix,
                    "agent_node_id": remote_agent.node_id() if agent_enabled else ""
                }, sort_keys=True)
            except Exception:
                logger.exception("Invalid MQTT configuration; keeping current connection")
                time.sleep(3)
                continue
            if signature != current and time.monotonic() >= retry_at:
                mobile_dispatcher.interrupt("MQTT connection reconfigured")

                # I comandi ancora in attesa appartengono alla
                # configurazione precedente: non eseguirli
                # automaticamente dopo il cambio broker.
                from mobile_queue import cancel_pending
                cancelled = cancel_pending()
                if cancelled:
                    logger.warning(
                        "Cancelled %s pending Mobile Control commands",
                        cancelled,
                    )

                if agent_client is not None:
                    try:
                        agent_client.disconnect()
                    except Exception:
                        pass
                    agent_client = None
                if client is not None:
                    try:
                        client.disconnect()
                    except Exception:
                        pass
                    client = None
                try:
                    # GATEWAY_TOPICS_V1
                    topic = settings["topic"]
                    log_topic = topic[:-5] + "/log" if topic.endswith("/json") else None
                    gateway_topics = {}
                    if topic.endswith("/json") and "+" not in topic and "#" not in topic:
                        base = topic[:-5]
                        gateway_topics = {
                            f"{base}/{name}/json": name
                            for name in (
                                "dmrgateway",
                                "ysfgateway",
                                "dstargateway",
                                "aprsgateway",
                            )
                        }

                    remote_base = runtime_modes.topic_base(settings)
                    agent_transport = (AgentTransport(remote_agent.node_id(), agent_prefix)
                                       if agent_enabled else None)
                    new_client = mqtt.Client(userdata={
                        "db": db,
                        "gateway_topics": gateway_topics,
                        "remote_base": remote_base,
                        "mobile_engine": mobile_engine,
                        "agent_transport": agent_transport if not agent_separate else None,
                        "log_topic": log_topic,
                    })
                    new_client.on_message = on_message
                    new_client.on_subscribe = lambda *args: logger.info("MQTT subscription active (pid=%s)", os.getpid())
                    # COLLECTOR_CALLBACK_SNAPSHOT_V1
                    subscribed_topic = settings["topic"]
                    subscribed_log_topic = log_topic
                    subscribed_gateways = tuple(gateway_topics)
                    subscribed_agent = agent_enabled and not agent_separate
                    subscribed_prefix = agent_prefix

                    def connected(
                        c, userdata, flags, rc,
                        subscribed_topic=subscribed_topic,
                        subscribed_log_topic=subscribed_log_topic,
                        subscribed_gateways=subscribed_gateways,
                        subscribed_agent=subscribed_agent,
                        subscribed_prefix=subscribed_prefix
                    ):
                        if rc == 0:
                            c.subscribe(subscribed_topic, qos=0)
                            if subscribed_log_topic:
                                c.subscribe(subscribed_log_topic, qos=0)
                                logger.info("Subscribed MMDVMHost logs: %s", subscribed_log_topic)
                            for gateway_topic in subscribed_gateways:
                                c.subscribe(gateway_topic, qos=0)
                                logger.info("Subscribed gateway: %s", gateway_topic)
                            if remote_base:
                                c.subscribe(remote_base + '/response', qos=0)
                            if subscribed_agent:
                                c.subscribe(subscribed_prefix + "/#", qos=1)
                                logger.info("Subscribed Agent: %s/#", subscribed_prefix)
                            logger.info("Subscribed: %s", subscribed_topic)
                        else:
                            logger.error("MQTT connect error: %s", rc)
                    new_client.on_connect = connected
                    if settings.get("username"):
                        new_client.username_pw_set(settings["username"], settings.get("password") or None)
                    if settings.get("tls"):
                        new_client.tls_set()
                    new_client.connect(settings["host"], int(settings["port"]), keepalive=60)
                    client = new_client
                    # AGENT_DISPATCHER_WIRING_V1
                    # Ogni worker Multi gestisce esclusivamente il proprio nodo.
                    # Il client Agent viene scelto al momento dell'invio.
                    # L'abilitazione viene aggiornata nel ciclo principale.
                    mobile_dispatcher.enabled = False

                    if agent_transport is not None:
                        mobile_dispatcher.transport = "agent"
                        mobile_dispatcher.engine = AgentCommandEngine(
                            agent_transport,
                            lambda: (
                                agent_client if agent_separate else client
                            ),
                        )
                        logger.info(
                            "Agent command transport prepared for node %s",
                            remote_agent.node_id(),
                        )
                    else:
                        mobile_dispatcher.transport = "disabled"
                        mobile_dispatcher.engine = mobile_engine

                    last_remote_query = 0.0
                    current = signature
                    # Separate Agent broker, with independent MQTT connection and callbacks.
                    # Do not change or shadow mqtt_store.connect (SQLite).
                    if agent_separate:
                        agent_host = settings.get("agent_host") or settings["host"]
                        agent_port = int(settings.get("agent_port") or settings["port"])
                        agent_username = settings.get("agent_username") or ""
                        agent_password = settings.get("agent_password") or ""
                        agent_tls = bool(settings.get("agent_tls", False))
                        def agent_message(c, userdata, msg):
                            try:
                                if agent_transport is not None and agent_transport.on_message(msg.topic, msg.payload, msg.retain):
                                    return
                                remote_agent.ingest(msg.topic, msg.payload)
                            except Exception:
                                logger.exception("Failed to ingest Agent message")
                        def agent_connected(c, userdata, flags, rc):
                            if rc == 0:
                                c.subscribe(agent_prefix + "/#", qos=1)
                                logger.info("Subscribed separate Agent: %s/#", agent_prefix)
                            else:
                                logger.error("Agent MQTT connect error: %s", rc)
                        agent_client = mqtt.Client()
                        agent_client.on_connect = agent_connected
                        agent_client.on_message = agent_message
                        if agent_username:
                            agent_client.username_pw_set(agent_username, agent_password or None)
                        if agent_tls:
                            agent_client.tls_set()
                        try:
                            agent_client.connect(agent_host, agent_port, keepalive=60)
                        except Exception as exc:
                            logger.warning("Separate Agent MQTT connect pending: %s", exc)
                            agent_retry_at = time.monotonic() + 5
                    logger.info("MQTT connection configured")
                except Exception as exc:
                    logger.error("MQTT connection failed: %s", exc)
                    retry_at = time.monotonic() + 5
            if agent_client is not None:
                try:
                    if time.monotonic() >= agent_retry_at:
                        agent_rc = agent_client.loop(timeout=0.1)
                        if agent_rc != mqtt.MQTT_ERR_SUCCESS:
                            agent_client.reconnect()
                except Exception as exc:
                    logger.warning("Agent MQTT reconnect pending: %s", exc)
                    agent_retry_at = time.monotonic() + 5
            if client is not None:
                try:
                    mobile_transport["client"] = client
                    mobile_transport["base"] = remote_base
                    # Un solo aggiornamento del motore attivo.
                    if mobile_dispatcher.engine is not None:
                        mobile_dispatcher.engine.tick()

                    # Rilettura dinamica della configurazione per nodo.
                    # Un'operazione già avviata deve poter terminare.
                    mobile_dispatcher.enabled = (
                        mobile_control_enabled()
                        and agent_transport is not None
                        and agent_enabled
                    )

                    # Non avviare comandi mentre potrebbe essere
                    # ancora in arrivo una risposta al polling.
                    if time.monotonic() - last_remote_query >= 3:
                        finished_operation = mobile_dispatcher.process_next()
                        if finished_operation is not None:
                            last_remote_query = time.monotonic()
                    if remote_base and not mobile_engine.busy and time.monotonic() - last_remote_query >= 15:
                        last_remote_query = time.monotonic()
                        client.publish(remote_base + '/command', 'status', qos=0)
                    rc = client.loop(timeout=1.0)
                    if rc != mqtt.MQTT_ERR_SUCCESS:
                        client.reconnect()
                except Exception as exc:
                    logger.warning("MQTT reconnect pending: %s", exc)
                    time.sleep(3)
            else:
                time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Stopped")
    finally:
        if agent_client is not None:
            agent_client.disconnect()
        if client is not None:
            client.disconnect()
        db.close()


if __name__ == "__main__":
    main()
