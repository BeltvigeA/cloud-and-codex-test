"""Health gating utilities for printer reachability."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HealthState:
    """Represent the observable connectivity state for a printer."""

    isOnline: bool
    hasState: bool


class HealthGate:
    """Track consecutive reachability results to avoid status flapping."""

    def __init__(self, failsToOffline: int, oksToOnline: int) -> None:
        self._failsThreshold = max(1, int(failsToOffline))
        self._oksThreshold = max(1, int(oksToOnline))
        self._failCount = 0
        self._okCount = 0
        self._state = HealthState(isOnline=False, hasState=False)

    @property
    def state(self) -> HealthState:
        return self._state

    def observe(self, reachable: bool) -> HealthState:
        if reachable:
            self._okCount += 1
            self._failCount = 0
            if self._okCount >= self._oksThreshold and (not self._state.hasState or not self._state.isOnline):
                self._state = HealthState(isOnline=True, hasState=True)
        else:
            self._failCount += 1
            self._okCount = 0
            if self._failCount >= self._failsThreshold and (not self._state.hasState or self._state.isOnline):
                self._state = HealthState(isOnline=False, hasState=True)
        return self._state

    def reset(self) -> None:
        self._failCount = 0
        self._okCount = 0
        self._state = HealthState(isOnline=False, hasState=False)


__all__ = ["HealthGate", "HealthState"]
