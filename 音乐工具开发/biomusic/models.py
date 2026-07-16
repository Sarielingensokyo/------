from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class BioRecord:
    name: str
    data_type: str
    symbols: list[str]
    source_labels: list[str]
    features: dict[str, list[float]] = field(default_factory=dict)
    categories: dict[str, list[str]] = field(default_factory=dict)
    coordinates: list[tuple[float, float, float]] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def length(self) -> int:
        return len(self.symbols)


@dataclass
class MusicEvent:
    event_id: int
    source_index: int
    source_label: str
    symbol: str
    onset: float
    duration: float
    midi: int
    velocity: int
    pan: float
    timbre: str
    expected_pc: int
    mapping_rule: str
    voice_id: str = "melody"
    role: str = "foreground_melody"
    parent_event_id: int | None = None
    features: dict[str, float] = field(default_factory=dict)
    row_position: int | None = None
    row_form: str | None = None
    status: str = "proposed"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["pitch_class"] = self.midi % 12
        return data


@dataclass
class Violation:
    event_id: int | None
    rule: str
    severity: str
    message: str
    repaired: bool = False
    action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GVRReport:
    proposed_count: int
    retained_count: int
    passed: bool
    violations_before: list[Violation]
    violations_after: list[Violation]
    repairs: list[Violation]
    checks: dict[str, bool]
    tone_row: list[int] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposed_count": self.proposed_count,
            "retained_count": self.retained_count,
            "passed": self.passed,
            "checks": self.checks,
            "tone_row": self.tone_row,
            "violations_before": [v.to_dict() for v in self.violations_before],
            "violations_after": [v.to_dict() for v in self.violations_after],
            "repairs": [v.to_dict() for v in self.repairs],
        }
