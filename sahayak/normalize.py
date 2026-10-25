"""Deterministic clean-up of LLM-structured prescriptions.

A safety net for messy handwriting: whatever the LLM returns, raw shorthand and implausible
values never reach the patient, Commonwealth durations are spelled out, and tablets are never
spoken in grams. Legibility is judged by comparing the result with the OCR text rather than by
asking the LLM how confident it is (which proved unreliable), so a clearly written prescription
never triggers "please confirm" warnings.
"""

from __future__ import annotations

import re
from dataclasses import replace

from .models import Medicine, Prescription

_DURATION_RE = re.compile(r"^\s*(?:for\s+)?(\d+)\s*/\s*(7|52|12)\s*$", re.IGNORECASE)
_DURATION_UNITS = {"7": "day", "52": "week", "12": "month"}
# "-> 4/2": an arrow-duration whose denominator isn't 7, 52 or 12 is a misread of the handwriting.
_MISREAD_DURATION_RE = re.compile(r"(?:->|→)\s*\S{0,3}\s*\d+\s*/\s*(?!(?:7|52|12)\b)\d+\b")
_DOSE_WITH_UNIT_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml|iu|units?)\b", re.IGNORECASE)
_WORD_RE = re.compile(r"[^\W\d_]{3,}")
_GRAMS_RE = re.compile(r"^\s*\d+(?:\.\d+)?\s*(?:g|gm|gms|grams?)\s*$", re.IGNORECASE)
_LEADING_NUMBER_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)")
_SOLID_FORMS = ("tablet", "tab ", "capsule", "cap ")
_FOOD_WORDS = ("food", "meal", "empty stomach", "breakfast", "lunch", "dinner")
TOPICAL_WORDS = ("gel", "cream", "ointment", "lotion", "apply")
# A dose of more than 4 tablets or a frequency above 6 times a day is almost certainly a misread.
_UNIT_COUNT_RE = re.compile(r"(\d+)\s*(?:tablets?|capsules?|tabs?|caps?)\b", re.IGNORECASE)
_TIMES_RE = re.compile(r"(\d+)\s*times\b", re.IGNORECASE)
MAX_UNITS_PER_DOSE = 4
MAX_TIMES_PER_DAY = 6
# Prescription shorthand (and its usual OCR misreads) -> plain English, plus the time-of-day
# slots it implies. Applied when the LLM leaves raw shorthand in a frequency/timing field.
_SHORTHAND: dict[str, tuple[str, tuple[str, ...]]] = {
    "od": ("once a day", ()), "o.d": ("once a day", ()), "o.d.": ("once a day", ()),
    "bd": ("twice a day", ()), "b.d": ("twice a day", ()), "b.d.": ("twice a day", ()),
    "bid": ("twice a day", ()),
    "tds": ("three times a day", ()), "tid": ("three times a day", ()),
    "qid": ("four times a day", ()),
    "nocte": ("at night", ("night",)), "noct": ("at night", ("night",)),
    "nocti": ("at night", ("night",)), "nacht": ("at night", ("night",)),
    "hs": ("at bedtime", ("bedtime",)),
    "mane": ("in the morning", ("morning",)),
    "stat": ("immediately", ()),
}
_AS_NEEDED_WORDS = frozenset({"prn", "sos", "as needed", "when needed", "as required", "if needed"})
# Latin frequency words that OCR sometimes turns into a separate "medicine" line.
_TIMING_WORDS = frozenset(_SHORTHAND) | _AS_NEEDED_WORDS
_UNSURE_PHRASES = {
    "name": "the name",
    "strength": "the strength",
    "dose": "how much to take",
    "frequency": "how often to take it",
    "duration": "how long to take it",
}
UNSURE_ORDER = ("name", "strength", "dose", "frequency", "duration")
DURATION_NOTE = (
    "Some of the durations were hard to read, so please ask your pharmacist how long to take each medicine."
)


def spell_duration(value: str | None) -> str | None:
    """'10/7' -> '10 days', '2/52' -> '2 weeks', '1/12' -> '1 month'; anything else unchanged."""
    if not value:
        return value
    match = _DURATION_RE.match(value)
    if not match:
        return value
    count, unit = int(match.group(1)), _DURATION_UNITS[match.group(2)]
    return f"{count} {unit}{'' if count == 1 else 's'}"


def is_plain_language(value: str) -> bool:
    """True for '1 tablet', 'at night', '10 ml'; False for raw shorthand like '4/2', 'x' or 'T'."""
    return bool(_WORD_RE.search(value) or _DOSE_WITH_UNIT_RE.search(value))


def is_food_timing(value: str | None) -> bool:
    text = (value or "").lower()
    return any(word in text for word in _FOOD_WORDS)


def _is_topical(med: Medicine) -> bool:
    text = f"{med.name} {med.dosage or ''} {med.instructions or ''}".lower()
    return any(word in text for word in TOPICAL_WORDS)


def _is_plausible(field: str, value: str) -> bool:
    if field == "dosage":
        match = _UNIT_COUNT_RE.search(value)
        return not match or int(match.group(1)) <= MAX_UNITS_PER_DOSE
    if field == "frequency":
        match = _TIMES_RE.search(value)
        return not match or int(match.group(1)) <= MAX_TIMES_PER_DAY
    return True


def _looks_like_initials(med: Medicine) -> bool:
    """'HTHNS' with no dose or strength: the doctor's initials, not a medicine."""
    letters = re.sub(r"[^A-Za-z]", "", med.name)
    return (
        len(letters) >= 4
        and not re.search(r"[aeiouy]", letters, re.IGNORECASE)
        and not med.dosage
        and not med.strength
    )


def unclear_sentence(fields: list[str]) -> str | None:
    """['strength', 'name'] -> 'The name and the strength were hard to read, so please confirm ...'"""
    phrases = [_UNSURE_PHRASES[f] for f in UNSURE_ORDER if f in fields]
    if not phrases:
        return None
    joined = phrases[0] if len(phrases) == 1 else f"{', '.join(phrases[:-1])} and {phrases[-1]}"
    verb = "was" if len(phrases) == 1 else "were"
    return f"{joined[0].upper()}{joined[1:]} {verb} hard to read, so please confirm with your pharmacist."


def normalize_medicine(med: Medicine) -> Medicine | None:
    if med.name.strip().strip(".").lower() in _TIMING_WORDS:
        return None

    schedule = list(med.schedule)
    as_needed = med.as_needed
    fields: dict[str, str | None] = {}
    for name in ("frequency", "timing"):
        value = (getattr(med, name) or "").strip().lower()
        if value in _AS_NEEDED_WORDS:
            as_needed, fields[name] = True, None
        elif value in _SHORTHAND:
            plain, slots = _SHORTHAND[value]
            fields[name] = plain
            schedule = schedule or list(slots)
    med = replace(med, schedule=schedule, as_needed=as_needed, **fields)

    instructions = (med.instructions or "").strip(". ")
    if instructions.lower() in _AS_NEEDED_WORDS:
        med = replace(med, instructions=None, as_needed=True)
    elif is_food_timing(instructions) and not med.timing:
        med = replace(med, timing=instructions, instructions=None)  # "after food" is timing

    fields = {}
    for name in ("dosage", "frequency", "timing", "duration"):
        value = getattr(med, name)
        if name == "duration":
            value = spell_duration(value)
        if value and not (is_plain_language(value) and _is_plausible(name, value)):
            value = None  # raw shorthand or a misread; the script says it isn't clear
        fields[name] = value

    unsure = list(med.unsure)
    strength = med.strength
    form = f" {med.name} {fields['dosage'] or ''} ".lower()
    if strength and _GRAMS_RE.match(strength) and any(f in form for f in _SOLID_FORMS):
        # Almost certainly a misread "mg": saying "10 grams" of a tablet would be dangerous.
        strength = None
        unsure.append("strength")

    cleaned = replace(med, strength=strength, unsure=unsure, **fields)
    return None if _looks_like_initials(cleaned) else cleaned


def _corrections(med: Medicine, ocr: str) -> list[str]:
    """Fields the LLM changed relative to the OCR text: a corrected name or strength unit."""
    found = []
    words = [w for w in re.findall(r"[A-Za-z]{3,}", med.name)]
    if words and not all(re.search(rf"\b{re.escape(w)}", ocr, re.IGNORECASE) for w in words):
        found.append("name")
    number = _LEADING_NUMBER_RE.match(med.strength or "")
    if number and not _GRAMS_RE.match(med.strength or ""):
        if re.search(rf"\b{re.escape(number.group(1))}\s*(?:g|gm)\b", ocr, re.IGNORECASE):
            found.append("strength")  # OCR said "10g"; we're saying "10 mg"
    return found


def _person_name(value: str | None) -> str | None:
    """Drop vowel-less initials such as 'HTHNS' that OCR picks up from a signature."""
    if value and not re.search(r"[aeiouy]", re.sub(r"(?i)^dr\.?\s*", "", value), re.IGNORECASE):
        return None
    return value


def normalize_prescription(rx: Prescription, ocr_text: str | None = None) -> Prescription:
    medicines = []
    for med in rx.medicines:
        cleaned = normalize_medicine(med)
        if cleaned is None:
            continue
        unsure = cleaned.unsure + (_corrections(cleaned, ocr_text) if ocr_text else [])
        unsure = [f for f in UNSURE_ORDER if f in unsure]
        medicines.append(replace(cleaned, unsure=unsure, unclear=unclear_sentence(unsure)))

    notes = []
    misread_durations = bool(ocr_text and _MISREAD_DURATION_RE.search(ocr_text))
    if misread_durations:
        notes.append(DURATION_NOTE)
    unclear_count = sum(
        1 for m in medicines
        if m.unsure
        or not (m.dosage or _is_topical(m))
        or not (m.as_needed or m.schedule or m.frequency)
    )
    hard_to_read = misread_durations or (len(medicines) >= 2 and unclear_count * 2 >= len(medicines))

    return replace(
        rx,
        patient_name=_person_name(rx.patient_name),
        doctor_name=_person_name(rx.doctor_name),
        medicines=medicines,
        uncertain=notes,
        hard_to_read=hard_to_read,
    )
