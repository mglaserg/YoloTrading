from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HealthCheck:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class HealthSummary:
    checks: tuple[HealthCheck, ...]

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    @property
    def status(self) -> str:
        return "PRE-LIVE READY" if self.ok else "ATTENTION REQUIRED"


def print_health(summary: HealthSummary) -> None:
    print("\nYOLO HEALTH")
    print(f"overall: {summary.status}")
    for check in summary.checks:
        icon = "OK" if check.ok else "FAIL"
        print(f"[{icon:<4}] {check.name:<22} {check.detail}")
