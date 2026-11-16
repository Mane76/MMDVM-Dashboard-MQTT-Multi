"""Private, per-node BrandMeister settings."""

import json
import os
import tempfile

from node_paths import data_file

SETTINGS = data_file("brandmeister-settings.json")

DEFAULTS = {
    "device_id": "",
    "api_key": "",
}


def load():
    if not SETTINGS.exists():
        return dict(DEFAULTS)

    with SETTINGS.open(encoding="utf-8") as handle:
        data = json.load(handle)

    if not isinstance(data, dict):
        raise ValueError("Invalid BrandMeister settings")

    return {**DEFAULTS, **data}


def public():
    data = load()
    return {
        "device_id": data["device_id"],
        "api_key_set": bool(data["api_key"]),
    }


def save(changes):
    allowed = {
        "device_id", "api_key",
    }

    if not isinstance(changes, dict) or set(changes) - allowed:
        raise ValueError("Invalid BrandMeister fields")

    data = load()

    if "device_id" in changes:
        value = changes["device_id"]
        if not isinstance(value, str):
            raise ValueError("Invalid Device ID")
        if value and (not value.isascii() or
                      not value.isdecimal() or
                      len(value) > 12):
            raise ValueError("Invalid Device ID")
        data["device_id"] = value

    if "api_key" in changes:
        value = changes["api_key"]
        if not isinstance(value, str) or len(value) > 2048:
            raise ValueError("Invalid API key")
        if value and any(ord(char) < 33 or ord(char) > 126 for char in value):
            raise ValueError("Invalid API key")
        # Empty input never overwrites an existing secret.
        if value:
            data["api_key"] = value

    SETTINGS.parent.mkdir(
        parents=True, exist_ok=True, mode=0o700
    )

    fd, temporary = tempfile.mkstemp(
        prefix=".brandmeister-",
        dir=str(SETTINGS.parent),
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(data, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(temporary, SETTINGS)

    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

    return public()
