"""Typed representation of a structured prescription."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text and text.lower() not in {"null", "none", "n/a", "na", "-"} else None


def _str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [s for s in (_opt_str(v) for v in value) if s]


@dataclass
class Medicine:
    name: str
    strength: str | None = None
    dosage: str | None = None
    frequency: str | None = None
    timing: str | None = None
    duration: str | None = None
    instructions: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Medicine | None":
        name = _opt_str(data.get("name"))
        if not name:
            return None
        return cls(
            name=name,
            strength=_opt_str(data.get("strength")),
            dosage=_opt_str(data.get("dosage")),
            frequency=_opt_str(data.get("frequency")),
            timing=_opt_str(data.get("timing")),
            duration=_opt_str(data.get("duration")),
            instructions=_opt_str(data.get("instructions")),
        )


@dataclass
class Prescription:
    patient_name: str | None = None
    patient_age: str | None = None
    doctor_name: str | None = None
    clinic: str | None = None
    date: str | None = None
    diagnosis: list[str] = field(default_factory=list)
    medicines: list[Medicine] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    advice: list[str] = field(default_factory=list)
    follow_up: str | None = None
    uncertain: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Prescription":
        """Build from LLM output, tolerating missing keys and loose types."""
        raw_meds = data.get("medicines") or []
        medicines = [
            m for m in (Medicine.from_dict(item) for item in raw_meds if isinstance(item, dict)) if m
        ]
        return cls(
            patient_name=_opt_str(data.get("patient_name")),
            patient_age=_opt_str(data.get("patient_age")),
            doctor_name=_opt_str(data.get("doctor_name")),
            clinic=_opt_str(data.get("clinic")),
            date=_opt_str(data.get("date")),
            diagnosis=_str_list(data.get("diagnosis")),
            medicines=medicines,
            tests=_str_list(data.get("tests")),
            advice=_str_list(data.get("advice")),
            follow_up=_opt_str(data.get("follow_up")),
            uncertain=_str_list(data.get("uncertain")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def is_empty(self) -> bool:
        return not (self.medicines or self.diagnosis or self.advice or self.tests)
