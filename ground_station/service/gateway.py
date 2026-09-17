"""Transactional command gateway with an auditable event callback."""
from __future__ import annotations

from dataclasses import asdict
from typing import Callable

from ground_station.platform.transactions import Result


class CommandGateway:
    def __init__(self, bridge, event_sink: Callable[[str, dict], None] | None = None):
        self.bridge = bridge
        self.event_sink = event_sink
        self._pending: set[int] = set()

    def submit(self, command_id: int, index: int = 0, value: float = 0.0,
               flags: int = 0) -> int:
        txid = self.bridge.send_transaction(command_id, index, value, flags)
        self._pending.add(txid)
        if self.event_sink:
            self.event_sink("command_submitted", {"transaction_id": txid,
                                                    "command_id": command_id,
                                                    "index": index, "value": value})
        return txid

    def poll(self, timeout: float = 0.0) -> Result | None:
        result = self.bridge.poll_transaction_result(timeout)
        if result is not None:
            self._pending.discard(result.transaction_id)
            if self.event_sink:
                self.event_sink("command_result", asdict(result))
        return result
