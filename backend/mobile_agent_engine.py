"""Adattatore tra la coda Dashboard e il trasporto MQTT Agent."""


class AgentCommandEngine:
    def __init__(self, transport, client_provider):
        self.transport = transport
        self.client_provider = client_provider

    @property
    def busy(self):
        return self.transport.busy

    @property
    def result(self):
        return self.transport.result

    def start(self, command, operation_id):
        self.transport.start(
            self.client_provider(),
            operation_id,
            command,
        )

    def tick(self):
        self.transport.tick()

    def reset(self):
        self.transport.reset()
