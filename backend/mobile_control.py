from mobile_control_health import alive as collector_alive
#!/usr/bin/env python3
"""Mobile Control - API iniziale di sola lettura."""

from flask import Blueprint, jsonify
from admin_api import _require_auth
import runtime_modes
import remote_agent
from mobile_control_settings import enabled

mobile_control_bp = Blueprint(
    "mobile_control",
    __name__,
    url_prefix="/api/mobile-control"
)

@mobile_control_bp.route("/capabilities", methods=["GET"])
def capabilities():
    failure = _require_auth()
    if failure:
        return failure

    state = runtime_modes.snapshot()
    # Per i sette modi controllabili prevale lo stato Agent.
    agent_modes = None
    if remote_agent.enabled():
        agent_modes = remote_agent.snapshot()

    from mobile_mode_state import snapshot as mobile_modes_snapshot
    saved_modes = mobile_modes_snapshot()
    if agent_modes and agent_modes.get("online") and saved_modes["available"]:
        state = dict(state)
        state["modes"] = {
            **state.get("modes", {}),
            **saved_modes["modes"]
        }
        state["available"] = True
    control_enabled = enabled()
    collector_online = collector_alive()

    remote = remote_agent.enabled()
    agent_state = remote_agent.snapshot() if remote else {}
    agent_online = agent_state.get("online", False)
    agent_control_ready = agent_state.get("control_ready", False)

    # Il controllo attuale richiede Agent remoto.
    # Online indica raggiungibilità, non conferma
    # che l'esecutore dei comandi sia operativo.
    control_available = (
        control_enabled
        and collector_online
        and remote
        and agent_online
        and agent_control_ready
    )

    return jsonify({
        "ok": True,
        "control_enabled": control_enabled,
        "control_available": control_available,
        "collector_online": collector_online,
        "agent_online": agent_online,
        "agent_control_ready": agent_control_ready,
        "modes": state.get("modes", {}),
        "state_available": state.get("available", False),
        "special_commands": []
    })

@mobile_control_bp.route("/operations/<operation_id>", methods=["GET"])
def operation_status(operation_id):
    failure = _require_auth()
    if failure:
        return failure

    import re
    from mobile_queue import get

    if not re.fullmatch(r"[0-9a-f]{32}", operation_id):
        return jsonify(ok=False, error="Invalid operation ID"), 400

    operation = get(operation_id)
    if operation is None:
        return jsonify(ok=False, error="Operation not found"), 404

    return jsonify(ok=True, operation=operation)


@mobile_control_bp.route("/commands", methods=["POST"])
def submit_command():
    failure = _require_auth()
    if failure:
        return failure

    from flask import request
    from mobile_commands import validate

    # Sicurezza: nessun comando accettato finché
    # il controllo non è stato abilitato esplicitamente.
    if not enabled():
        return jsonify(
            ok=False,
            error="Mobile Control not enabled"
        ), 503

    if not collector_alive():
        return jsonify(
            ok=False,
            error="Mobile Control collector unavailable"
        ), 503

    # Per i nodi remoti riutilizza lo stato Agent esistente.
    # Online non implica che l'esecutore dei comandi sia pronto.
    if remote_agent.enabled() and not remote_agent.snapshot()["online"]:
        return jsonify(
            ok=False,
            error="Remote Agent offline or stale"
        ), 503

    # Il controllo è consentito soltanto con Agent remoto
    # online e con esecutore esplicitamente disponibile.
    agent_state = remote_agent.snapshot() if remote_agent.enabled() else {}
    if not agent_state.get("control_ready", False):
        return jsonify(
            ok=False,
            error="Agent command executor not ready"
        ), 503

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(ok=False, error="Invalid JSON"), 400

    try:
        command = validate(data.get("command"))
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400

    from mobile_queue import enqueue
    operation_id = enqueue(command)

    return jsonify(
        ok=True,
        queued=True,
        id=operation_id
    ), 202
