"""Heartbeat per nodo del collector Mobile Control."""

import json
import os
import time
from pathlib import Path

from node_paths import data_file

HEARTBEAT = "mobile-control-heartbeat.json"


def update():
    path = data_file(HEARTBEAT)
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps({
        "timestamp": time.time(),
        "pid": os.getpid(),
    }))
    tmp.replace(path)


def alive(max_age=10):
    try:
        data = json.loads(Path(data_file(HEARTBEAT)).read_text())
        age = time.time() - float(data["timestamp"])
        return 0 <= age <= max_age
    except (OSError, ValueError, TypeError, KeyError):
        return False
