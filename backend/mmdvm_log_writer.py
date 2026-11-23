import re
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = (
    Path(os.environ["MMDVM_NODE_DATA"]) / "logs/mmdvmhost"
    if os.environ.get("MMDVM_NODE_DATA")
    else Path(__file__).resolve().parent.parent / "data/logs/mmdvmhost"
)
TZ = ZoneInfo("Europe/Rome")
RETENTION_DAYS = 7
NOISE = 'Valid remote command of "status" received'


def save_log(topic, payload):
    parts = topic.split("/")
    if len(parts) < 3 or parts[-1] != "log":
        return

    callsign = parts[-2].upper()
    if not re.fullmatch(r"[A-Z0-9_-]{1,32}", callsign):
        return

    lines = payload.decode("utf-8", errors="replace").splitlines()
    lines = [line for line in lines if line.strip() and NOISE not in line]
    if not lines:
        return

    ROOT.mkdir(parents=True, exist_ok=True)
    today = datetime.now(TZ).date().isoformat()
    path = ROOT / f"{callsign}-{today}.log"

    with path.open("a", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")


def cleanup_logs():
    if not ROOT.exists():
        return
    cutoff = datetime.now(TZ).date() - timedelta(days=RETENTION_DAYS - 1)
    for path in ROOT.glob("*.log"):
        match = re.fullmatch(r"[A-Z0-9_-]+-(\\d{4}-\\d{2}-\\d{2})\\.log", path.name)
        if match:
            try:
                day = datetime.strptime(match.group(1), "%Y-%m-%d").date()
                if day < cutoff:
                    path.unlink()
            except ValueError:
                pass
