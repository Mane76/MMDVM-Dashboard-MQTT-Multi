"""Configurazione condivisa del controllo per singolo nodo Multi."""

import json
from pathlib import Path
from node_paths import data_file

SETTINGS = "mobile-control-settings.json"


def enabled():
    path = data_file(SETTINGS)
    try:
        data = json.loads(Path(path).read_text())
        return data.get("enabled") is True
    except (OSError, ValueError, TypeError):
        return False
