"""Safety-owned controller/estimator plugin lifecycle and output authority."""
from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum


class PluginState(str, Enum):
    DISABLED = "disabled"
    SHADOW = "shadow"
    CANDIDATE = "candidate"
    ACTIVE = "active"
    FAULTED = "faulted"


@dataclass(frozen=True)
class PluginDescriptor:
    name: str
    kind: str
    owner: str
    safety_class: str = "observation"


class Plugin:
    """Small deterministic lifecycle object; control output is opt-in."""

    _ALLOWED = {
        PluginState.DISABLED: {PluginState.SHADOW, PluginState.CANDIDATE},
        PluginState.SHADOW: {PluginState.DISABLED, PluginState.CANDIDATE, PluginState.FAULTED},
        PluginState.CANDIDATE: {PluginState.SHADOW, PluginState.ACTIVE, PluginState.FAULTED},
        PluginState.ACTIVE: {PluginState.SHADOW, PluginState.DISABLED, PluginState.FAULTED},
        PluginState.FAULTED: {PluginState.DISABLED, PluginState.SHADOW},
    }

    def __init__(self, descriptor: PluginDescriptor):
        self.descriptor = descriptor
        self.state = PluginState.DISABLED
        self.last_fault: str | None = None

    @property
    def drives_output(self) -> bool:
        return self.state is PluginState.ACTIVE

    def transition(self, target: PluginState) -> None:
        if target not in self._ALLOWED[self.state]:
            raise ValueError(f"illegal plugin transition {self.state.value}->{target.value}")
        self.state = target
        if target is not PluginState.FAULTED:
            self.last_fault = None

    def fault(self, detail: str) -> None:
        self.last_fault = str(detail)
        if self.state is not PluginState.FAULTED:
            self.transition(PluginState.FAULTED)


class AuthorityArbiter:
    """Single owner for output authority with heartbeat and conflict policy."""

    def __init__(self, heartbeat_timeout_s: float = 0.5):
        if heartbeat_timeout_s <= 0:
            raise ValueError("heartbeat_timeout_s must be positive")
        self.heartbeat_timeout_s = float(heartbeat_timeout_s)
        self.owner: str | None = None
        self._last_heartbeat = 0.0
        self.reason = "unowned"

    @property
    def active(self) -> bool:
        return self.owner is not None

    def claim(self, owner: str, now: float | None = None) -> None:
        if not owner:
            raise ValueError("owner is required")
        if self.owner not in (None, owner):
            raise RuntimeError(f"authority owned by {self.owner}")
        self.owner = owner
        self._last_heartbeat = time.monotonic() if now is None else float(now)
        self.reason = "claimed"

    def heartbeat(self, owner: str, now: float | None = None) -> None:
        if self.owner != owner:
            raise RuntimeError("heartbeat from non-owner")
        self._last_heartbeat = time.monotonic() if now is None else float(now)
        self.reason = "healthy"

    def release(self, owner: str, reason: str = "released") -> None:
        if self.owner not in (None, owner):
            raise RuntimeError("release from non-owner")
        self.owner = None
        self.reason = reason

    def expire(self, now: float | None = None) -> bool:
        if self.owner is None:
            return False
        current = time.monotonic() if now is None else float(now)
        if current - self._last_heartbeat <= self.heartbeat_timeout_s:
            return False
        self.owner = None
        self.reason = "heartbeat_expired"
        return True

