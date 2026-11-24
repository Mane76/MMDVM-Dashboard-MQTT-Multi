"""Protocollo MQTT Mobile Control tra Dashboard e Agent."""

import json
import re
import time
import uuid

from mobile_commands import validate

SCHEMA_VERSION = 1
MAX_PAYLOAD = 4096

OPERATION_ID = re.compile(r"^[0-9a-f]{32}$")
NODE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def topics(prefix):
    prefix = prefix.strip("/")

    if (
        not prefix
        or len(prefix) > 200
        or any(c in prefix for c in "+#\x00")
        or "//" in prefix
    ):
        raise ValueError("Invalid Agent topic prefix")

    return {
        "command": prefix + "/control/command",
        "response": prefix + "/control/response",
    }


def make_request(node_id, command, operation_id=None):
    if not NODE_ID.fullmatch(node_id):
        raise ValueError("Invalid node ID")

    command = validate(command)

    operation_id = operation_id or uuid.uuid4().hex

    if not OPERATION_ID.fullmatch(operation_id):
        raise ValueError("Invalid operation ID")

    return {
        "schema_version": SCHEMA_VERSION,
        "node_id": node_id,
        "operation_id": operation_id,
        "command": command,
        "created_at": time.time(),
    }


def parse_request(payload, expected_node):
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("Payload too large")

    data = json.loads(payload)

    if not isinstance(data, dict):
        raise ValueError("Invalid request")

    if set(data) != {
        "schema_version",
        "node_id",
        "operation_id",
        "command",
        "created_at",
    }:
        raise ValueError("Unexpected request fields")

    if data["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Unsupported schema")

    if data["node_id"] != expected_node:
        raise ValueError("Wrong node")

    from mobile_agent_guard import MAX_AGE, MAX_FUTURE

    created = data["created_at"]

    if (
        type(created) not in (int, float)
        or not time.time() - MAX_AGE <= created <= time.time() + MAX_FUTURE
    ):
        raise ValueError("Expired command")

    request = make_request(
        data["node_id"],
        data["command"],
        data["operation_id"],
    )
    request["created_at"] = created
    return request


def make_response(request, success, message, modes=None):
    return {
        "schema_version": SCHEMA_VERSION,
        "node_id": request["node_id"],
        "operation_id": request["operation_id"],
        "success": bool(success),
        "message": str(message)[:256],
        "modes": modes if isinstance(modes, dict) else {},
    }
