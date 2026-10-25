"""Pure text helpers: JSON extraction, chunking, and the patient-facing summary."""

from __future__ import annotations

import json
import re
from typing import Any

from .models import Prescription

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_SENTENCE_RE = re.compile(r"(?<=[.!?।])\s+")


def extract_json(text: str) -> dict[str, Any]:
    """Parse the first JSON object in an LLM response (handles fences and <think> blocks)."""
    cleaned = _THINK_RE.sub("", text or "").strip()
    fenced = _FENCE_RE.search(cleaned)
    if fenced:
        cleaned = fenced.group(1).strip()
    start = cleaned.find("{")
    if start == -1:
        raise ValueError("No JSON object found in model response")
    obj, _ = json.JSONDecoder().raw_decode(cleaned[start:])
    if not isinstance(obj, dict):
        raise ValueError("Model response JSON is not an object")
    return obj


def chunk_text(text: str, limit: int) -> list[str]:
    """Split text into chunks of at most `limit` chars, preferring line then sentence boundaries."""
    pieces: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if len(line) <= limit:
            pieces.append(line)
            continue
        for sentence in _SENTENCE_RE.split(line):
            while len(sentence) > limit:
                cut = sentence.rfind(" ", 0, limit)
                cut = cut if cut > 0 else limit
                pieces.append(sentence[:cut].strip())
                sentence = sentence[cut:].strip()
            if sentence:
                pieces.append(sentence)

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        candidate = f"{current}\n{piece}" if current else piece
        if len(candidate) <= limit:
            current = candidate
        else:
            chunks.append(current)
            current = piece
    if current:
        chunks.append(current)
    return chunks


def _sentence(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", "!", "?")) else f"{text}."


def build_patient_summary(rx: Prescription) -> str:
    """Plain-English, listener-friendly summary used as the source for translation and speech."""
    lines: list[str] = []

    header = "This is your prescription"
    if rx.doctor_name:
        doctor = rx.doctor_name if rx.doctor_name.lower().startswith("dr") else f"Dr. {rx.doctor_name}"
        header += f" from {doctor}"
    if rx.date:
        header += f", dated {rx.date}"
    lines.append(_sentence(header))

    if rx.diagnosis:
        lines.append(_sentence("The doctor has noted: " + ", ".join(rx.diagnosis)))

    if rx.medicines:
        count = len(rx.medicines)
        lines.append(f"You have {count} medicine{'s' if count != 1 else ''} to take.")
        for i, med in enumerate(rx.medicines, start=1):
            title = med.name
            # Skip strength when the name already carries it ("Dolo 650" + "650 mg").
            if med.strength and med.strength.split()[0].lower() not in med.name.lower():
                title += f" {med.strength}"
            how = ", ".join(p for p in (med.dosage, med.frequency, med.timing) if p)
            text = f"Medicine {i}: {title}."
            if how:
                text += f" Take {how}"
                text += f", for {med.duration}." if med.duration else "."
            elif med.duration:
                text += f" Continue for {med.duration}."
            if med.instructions:
                text += " " + _sentence(med.instructions)
            lines.append(text)

    if rx.tests:
        lines.append(_sentence("Tests advised: " + ", ".join(rx.tests)))
    if rx.advice:
        lines.append("Doctor's advice: " + " ".join(_sentence(a) for a in rx.advice))
    if rx.follow_up:
        lines.append(_sentence("Follow-up: " + rx.follow_up))

    lines.append(
        "Please confirm these instructions with your doctor or pharmacist before taking any medicine."
    )
    return "\n".join(lines)
