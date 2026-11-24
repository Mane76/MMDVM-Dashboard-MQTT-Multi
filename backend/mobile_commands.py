"""Comandi consentiti per il controllo MMDVMHost.

Modulo indipendente dal trasporto MQTT.
Non esegue né pubblica comandi.
"""

MODES = (
    "dstar", "dmr", "ysf", "fm",
    "p25", "nxdn", "m17",
)

SPECIAL_COMMANDS = frozenset({
    "mode idle",
    "mode lockout",
    "mode d-star",
    "mode dmr",
    "mode ysf",
    "mode fm",
    "mode p25",
    "mode nxdn",
    "mode m17",
    "reload",
})

ALLOWED_COMMANDS = frozenset(
    f"{action} {mode}"
    for action in ("enable", "disable")
    for mode in MODES
) | SPECIAL_COMMANDS


def validate(command):
    """Restituisce il comando validato o genera ValueError."""
    if not isinstance(command, str):
        raise ValueError("Invalid command")

    # CW_CONTROL_V1
    if command.startswith("cw "):
        import re
        message = command[3:]
        if not re.fullmatch(r"[A-Z0-9 /.-]{1,60}", message):
            raise ValueError("Invalid CW message")
        if message != message.strip() or not message.strip():
            raise ValueError("Invalid CW message")
        return command

    if command not in ALLOWED_COMMANDS:
        raise ValueError("Command not allowed")

    return command
