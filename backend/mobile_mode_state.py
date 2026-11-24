"""Stati radio confermati dall'Agent remoto."""
import json
import time
from pathlib import Path
from node_paths import data_file

MODES = ("dstar", "dmr", "ysf", "fm", "p25", "nxdn", "m17")
VALID = ("conn", "disc", "n/a")
STATE = data_file("mobile-mode-state.json")

def save(modes):
    if not isinstance(modes, dict):
        return False
    clean = {
        str(k).lower(): str(v).lower()
        for k, v in modes.items()
        if str(k).lower() in MODES and str(v).lower() in VALID
    }
    if not clean:
        return False
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_name(STATE.name + ".tmp")
    tmp.write_text(json.dumps({
        "source": "agent",
        "updated_at": time.time(),
        "modes": clean
    }))
    tmp.replace(STATE)
    return True

def snapshot():
    try:
        data = json.loads(STATE.read_text())
        modes = data.get("modes", {})
        return {
            "available": bool(modes),
            "source": "agent",
            "updated_at": data.get("updated_at"),
            "modes": {m: modes.get(m, "unknown") for m in MODES}
        }
    except (OSError, ValueError, TypeError):
        return {
            "available": False,
            "source": "agent",
            "updated_at": None,
            "modes": {m: "unknown" for m in MODES}
        }
