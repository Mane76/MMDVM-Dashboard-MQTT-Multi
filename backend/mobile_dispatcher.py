"""Dispatcher Mobile Control per singolo nodo MMDVM Multi."""

import logging

from mobile_queue import (
    recover_on_startup,
    claim_next,
    complete,
)

logger = logging.getLogger("mobile-control")


class MobileDispatcher:
    def __init__(self, transport="disabled", engine=None):
        if transport not in ("disabled", "direct", "agent"):
            raise ValueError("Invalid transport")

        self.transport = transport
        self.engine = engine
        self.enabled = False
        self.operation_id = None

    def startup(self):
        result = recover_on_startup()
        logger.info("Mobile Control recovery: %s", result)
        return result


    def interrupt(self, reason="MQTT configuration changed"):
        """Registra un esito incerto senza ritrasmettere."""
        if self.operation_id is None:
            return False

        operation_id = self.operation_id
        complete(
            operation_id,
            False,
            "Outcome uncertain: " + reason,
        )
        self.operation_id = None

        if self.engine is not None:
            self.engine.reset()

        logger.warning(
            "Operation %s interrupted: %s",
            operation_id, reason,
        )
        return True

    def process_next(self):
        if self.engine is None:
            return None

        # Prima completa un'operazione già avviata,
        # anche se nel frattempo il controllo è disabilitato.
        if self.operation_id is not None:
            if self.engine.result is None:
                return None

            result = self.engine.result
            # Conserva gli stati radio confermati dall'Agent.
            if self.transport == "agent":
                try:
                    from mobile_mode_state import save
                    save(result.get("modes"))
                except Exception:
                    logger.exception("Unable to persist Agent radio modes")
            complete(
                self.operation_id,
                result["success"],
                result["message"],
            )

            finished = self.operation_id
            self.operation_id = None
            self.engine.reset()
            return finished

        # La disabilitazione impedisce solo nuovi comandi.
        if not self.enabled or self.engine.busy:
            return None

        job = claim_next()
        if job is None:
            return None

        self.operation_id = job["id"]

        try:
            if self.transport == "agent":
                self.engine.start(
                    job["command"],
                    self.operation_id,
                )
            else:
                self.engine.start(job["command"])
        except Exception as exc:
            # Un errore di pubblicazione non autorizza
            # la ripetizione automatica del comando.
            complete(
                self.operation_id,
                False,
                "Command dispatch failed: " + str(exc)[:180],
            )
            finished = self.operation_id
            self.operation_id = None
            self.engine.reset()
            return finished

        return self.operation_id
