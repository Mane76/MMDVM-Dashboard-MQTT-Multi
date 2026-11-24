"""Ricevitore MQTT Agent Control, inizialmente disabilitato."""

import json
import logging

from mobile_agent_protocol import (
    topics,
    parse_request,
    make_response,
)
from mobile_agent_guard import CommandGuard

LOG = logging.getLogger("mobile-agent-control")


class AgentControlReceiver:
    def __init__(self, node_id, prefix, database, worker=None):
        self.node_id = node_id
        self.topics = topics(prefix)
        self.guard = CommandGuard(database)
        self.worker = worker
        self.enabled = False

    def on_message(self, client, message):
        if message.topic != self.topics["command"]:
            return False

        # I comandi retained non devono mai essere eseguiti.
        if message.retain:
            LOG.warning("Retained control command rejected")
            return True

        try:
            request = parse_request(
                message.payload,
                self.node_id,
            )
            self.guard.check(request)
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            LOG.warning("Invalid or duplicate control request")
            return True

        if not self.enabled:
            response = make_response(
                request,
                False,
                "Agent control disabled",
            )
        elif self.worker is None:
            response = make_response(
                request,
                False,
                "MMDVMHost executor not configured",
            )
        elif self.worker.submit(request):
            # Il worker pubblicherà la risposta finale.
            return True
        else:
            response = make_response(
                request,
                False,
                "Agent control worker busy",
            )

        result = client.publish(
            self.topics["response"],
            json.dumps(response),
            qos=1,
            retain=False,
        )

        if result.rc != 0:
            LOG.error("Unable to publish control response")

        return True
