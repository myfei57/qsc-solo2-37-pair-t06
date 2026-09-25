"""Cooling section with a stop permit that enforces the shutdown order."""

from __future__ import annotations

from typing import Any

from ..core.clock import Clock
from ..core.config import ControlConfig, require_within
from ..errors import StateError
from ..persistence.audit import AuditLedger
from ..persistence.store import DurableStore
from ..stages import gates as gate_names
from ..stages.gates import GateBoard


class Cooler:
    """Holds the outlet temperature and refuses an early stop."""

    document = "cooler"

    def __init__(
        self,
        store: DurableStore,
        clock: Clock,
        config: ControlConfig,
        gates: GateBoard,
        audit: AuditLedger,
    ) -> None:
        self.store = store
        self.clock = clock
        self.config = config
        self.gates = gates
        self.audit = audit
        self._running = False
        self._outlet_c = 20.0
        self._stop_requested = False
        self._last_action = ""
        self._load()

    def _load(self) -> None:
        stored = self.store.try_read(self.document)
        if stored is None:
            return
        self._running = bool(stored.payload.get("running", False))
        self._outlet_c = float(stored.payload.get("outlet_c", 20.0))
        self._stop_requested = bool(stored.payload.get("stop_requested", False))
        self._last_action = str(stored.payload.get("last_action", ""))

    def persist(self) -> None:
        self.store.write(
            self.document,
            {
                "running": self._running,
                "outlet_c": self._outlet_c,
                "stop_requested": self._stop_requested,
                "last_action": self._last_action,
            },
        )

    def request_stop(self, *, reason: str) -> dict[str, Any]:
        """Record the line-wide stop request that every section reacts to."""

        self._stop_requested = True
        return self._commit("stop-request", str(reason))

    def is_running(self) -> bool:
        return self._running

    def outlet_c(self) -> float:
        return self._outlet_c

    def start(self, *, reason: str) -> dict[str, Any]:
        if self._running:
            raise StateError("the cooling section is already running", section="cool")
        self._running = True
        return self._commit("start", str(reason))

    def stop(self, *, reason: str) -> dict[str, Any]:
        """Cooling may only stop once a stop has been requested for the line."""

        if not self._stop_requested:
            raise StateError("no line stop has been requested", section="cool", action="cooling-stop")
        if not self._running:
            raise StateError("the cooling section is already stopped", section="cool")
        self._running = False
        return self._commit("stop", str(reason))

    def set_outlet(self, value_c: float, *, reason: str) -> dict[str, Any]:
        value = require_within(value_c, 0.0, 40.0, field_name="outlet_c", scope="cool")
        self._outlet_c = value
        return self._commit("outlet", str(reason))

    def _commit(self, action: str, reason: str) -> dict[str, Any]:
        entry = {
            "action": action,
            "running": self._running,
            "outlet_c": self._outlet_c,
            "reason": reason,
            "timestamp": self.clock.timestamp(),
        }
        self._last_action = action
        self.persist()
        self.audit.record(f"cool-{action}", "cool", reason, cause=None)
        return dict(entry)

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        if not self._last_action:
            return []
        return [
            {
                "action": self._last_action,
                "running": self._running,
                "outlet_c": self._outlet_c,
                "reason": "",
                "timestamp": self.clock.timestamp(),
            }
        ][-max(0, int(limit)) :]

    def snapshot(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "outlet_c": self.outlet_c(),
            "stop_permit_open": self.gates.is_open(gate_names.STERILIZATION_STOPPED),
            "history": self.history(5),
        }


__all__ = ["Cooler"]
