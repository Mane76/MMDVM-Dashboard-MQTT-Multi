"""Minimal BrandMeister API v2 client. No third-party dependencies."""
import json
import re
import urllib.error
import urllib.request

BASE_URL = "https://api.brandmeister.network"
TIMEOUT = 10
MAX_RESPONSE = 262144

class BrandMeisterError(Exception):
    """Safe, non-secret API failure."""

def _digits(value, label, max_length):
    value = str(value)
    if not re.fullmatch(r"[0-9]{1,%d}" % max_length, value) or int(value) == 0:
        raise ValueError("Invalid " + label)
    return value

def _slot(slot):
    value = str(slot)
    if value not in ("1", "2"):
        raise ValueError("Invalid time slot")
    return value

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

_OPENER = urllib.request.build_opener(_NoRedirect)

def _call(device_id, api_key, suffix, method="GET", body=None):
    device = _digits(device_id, "Device ID", 12)
    if not isinstance(api_key, str) or not api_key or "\n" in api_key or "\r" in api_key:
        raise ValueError("Invalid BrandMeister API Key")
    headers = {"Accept": "application/json", "Authorization": "Bearer " + api_key}
    payload = None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE_URL + "/v2/device/" + device + suffix,
                                 data=payload, headers=headers, method=method)
    try:
        with _OPENER.open(req, timeout=TIMEOUT) as response:
            if response.status < 200 or response.status >= 300:
                raise BrandMeisterError("BrandMeister request rejected")
            raw = response.read(MAX_RESPONSE + 1)
    except urllib.error.HTTPError as exc:
        raise BrandMeisterError("BrandMeister HTTP " + str(exc.code)) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise BrandMeisterError("BrandMeister connection failed") from None
    if len(raw) > MAX_RESPONSE:
        raise BrandMeisterError("BrandMeister response too large")
    if not raw.strip():
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError):
        raise BrandMeisterError("Invalid BrandMeister JSON response") from None

def get_talkgroups(device_id, api_key):
    result = _call(device_id, api_key, "/talkgroup")
    if not isinstance(result, list):
        raise BrandMeisterError("Unexpected BrandMeister response format")
    return result

def add_talkgroup(device_id, api_key, slot, tg):
    slot = _slot(slot)
    tg = _digits(tg, "Talkgroup", 6)
    return _call(device_id, api_key, "/talkgroup", "POST", {"slot": int(slot), "group": int(tg)})

def delete_talkgroup(device_id, api_key, slot, tg):
    slot = _slot(slot)
    tg = _digits(tg, "Talkgroup", 6)
    return _call(device_id, api_key, "/talkgroup/" + slot + "/" + tg, "DELETE")

def drop_dynamic_groups(device_id, api_key, slot):
    slot = _slot(slot)
    # BrandMeister uses GET for this state-changing action; never expose as a GET in our API.
    return _call(device_id, api_key, "/action/dropDynamicGroups/" + slot)
