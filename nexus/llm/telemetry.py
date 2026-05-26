from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class LLMTelemetrySink(Protocol):
    def record_span(self, name: str, attributes: dict[str, object]) -> None: ...

    def increment_counter(self, name: str, value: int | float = 1, labels: dict[str, str] | None = None) -> None: ...

    def observe_histogram(self, name: str, value: int | float, labels: dict[str, str] | None = None) -> None: ...

    def set_gauge(self, name: str, value: int | float, labels: dict[str, str] | None = None) -> None: ...


@dataclass
class InMemoryTelemetrySink:
    spans: list[tuple[str, dict[str, object]]] = field(default_factory=list)
    counters: list[tuple[str, int | float, dict[str, str]]] = field(default_factory=list)
    histograms: list[tuple[str, int | float, dict[str, str]]] = field(default_factory=list)
    gauges: list[tuple[str, int | float, dict[str, str]]] = field(default_factory=list)

    def record_span(self, name: str, attributes: dict[str, object]) -> None:
        self.spans.append((name, attributes))

    def increment_counter(self, name: str, value: int | float = 1, labels: dict[str, str] | None = None) -> None:
        self.counters.append((name, value, labels or {}))

    def observe_histogram(self, name: str, value: int | float, labels: dict[str, str] | None = None) -> None:
        self.histograms.append((name, value, labels or {}))

    def set_gauge(self, name: str, value: int | float, labels: dict[str, str] | None = None) -> None:
        self.gauges.append((name, value, labels or {}))
