"""Worker seriale per i comandi MMDVMHost ricevuti dall'Agent."""

import json
import logging
import queue
import threading

from mobile_agent_protocol import make_response

LOG = logging.getLogger("mobile-agent-worker")


class AgentControlWorker:
    def __init__(self, executor):
        self.executor = executor
        self.jobs = queue.Queue(maxsize=1)
        self.thread = None
        self.stopped = threading.Event()
        self.publisher = None
        self.response_topic = None
        self.lock = threading.Lock()
        self.occupied = False

    def start(self, publisher, response_topic):
        if self.thread is not None:
            raise RuntimeError("Worker already started")

        self.publisher = publisher
        self.response_topic = response_topic

        self.thread = threading.Thread(
            target=self._run,
            name="mmdvm-agent-control",
            daemon=True,
        )
        self.thread.start()

    def submit(self, request):
        if self.thread is None or self.stopped.is_set():
            return False

        with self.lock:
            if self.occupied:
                return False

            self.occupied = True

            try:
                self.jobs.put_nowait(request)
                return True
            except queue.Full:
                self.occupied = False
                return False

    def _run(self):
        while not self.stopped.is_set():
            try:
                request = self.jobs.get(timeout=0.2)
            except queue.Empty:
                continue

            try:
                try:
                    result = self.executor.execute(
                        request["command"]
                    )
                    response = make_response(
                        request,
                        result["success"],
                        result["message"],
                        result.get("modes"),
                    )
                except Exception:
                    LOG.exception("MMDVMHost execution failed")
                    response = make_response(
                        request,
                        False,
                        "MMDVMHost execution error",
                    )

                info = self.publisher(
                    self.response_topic,
                    json.dumps(response),
                    qos=1,
                    retain=False,
                )

                if info is not None and getattr(info, "rc", 0) != 0:
                    LOG.error("MQTT response publication failed")

            except Exception:
                LOG.exception("Unable to publish control result")

            finally:
                with self.lock:
                    self.occupied = False
                self.jobs.task_done()

    def stop(self):
        self.stopped.set()
        if self.thread is not None:
            self.thread.join(timeout=2)
