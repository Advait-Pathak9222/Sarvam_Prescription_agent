"""Pure text helpers: JSON extraction, chunking, and the spoken explanation."""

from __future__ import annotations

import json
import re
from typing import Any

from .models import SCHEDULE_SLOTS, UNSURE_FIELDS, Medicine, Prescription
from .normalize import TOPICAL_WORDS, is_food_timing

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_SENTENCE_RE = re.compile(r"(?<=[.!?।])\s+")

# Spoken forms for unit abbreviations, so TTS doesn't spell them out letter by letter.
_SPEAKABLE = (
    (re.compile(r"(?<=\d)\s*mcg\b", re.IGNORECASE), " microgram"),
    (re.compile(r"(?<=\d)\s*mg\b", re.IGNORECASE), " milligram"),
    (re.compile(r"(?<=\d)\s*ml\b", re.IGNORECASE), " millilitre"),
    (re.compile(r"(?<=\d)\s*(?:gm|gms|g)\b", re.IGNORECASE), " gram"),
    (re.compile(r"(?<=\d)\s*iu\b", re.IGNORECASE), " units"),
    (re.compile(r"\s+/\s+"), " or "),
)

_ORDINALS = ("first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth")
_SLOT_PHRASES = {
    "morning": "in the morning",
    "afternoon": "in the afternoon",
    "evening": "in the evening",
    "night": "at night",
    "bedtime": "at bedtime",
}
_FOOD_GROUPS = ("before food", "after food", "food timing not written")
_UNSURE_PHRASES = {
    "name": "the name",
    "strength": "the strength",
    "dose": "how much to take",
    "frequency": "how often to take it",
    "duration": "how long to take it",
}


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


def split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def speakable(text: str) -> str:
    """Expand abbreviations and symbols that TTS would otherwise read awkwardly."""
    for pattern, spoken in _SPEAKABLE:
        text = pattern.sub(spoken, text)
    return text


def _sentence(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", "!", "?")) else f"{text}."


def _lower_first(text: str) -> str:
    """'Viral fever' -> 'viral fever' mid-sentence, but keep acronyms like 'BP' or 'CBC'."""
    text = text.strip()
    if len(text) > 1 and text[0].isupper() and not text[1].isupper():
        return text[0].lower() + text[1:]
    return text


def _food_phrase(timing: str | None) -> str | None:
    if not timing:
        return None
    timing = _lower_first(timing)
    return "on an empty stomach" if timing == "empty stomach" else timing


def _join(items: list[str]) -> str:
    """['a'] -> 'a'; ['a', 'b'] -> 'a and b'; ['a', 'b', 'c'] -> 'a, b and c'."""
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _food_group(timing: str | None) -> str:
    text = (timing or "").lower()
    if "before" in text or "empty stomach" in text:
        return "before food"
    if "after" in text or "with food" in text:
        return "after food"
    return "food timing not written"


def daily_routine(rx: Prescription) -> dict[str, dict[str, list[str]]]:
    """Group medicines by time of day, then by before/after food.

    Built only from explicitly stated schedules, so the recap never invents a timing.
    """
    routine: dict[str, dict[str, list[str]]] = {}
    for med in rx.medicines:
        if med.as_needed:
            continue
        for slot in med.schedule:
            routine.setdefault(slot, {}).setdefault(_food_group(med.timing), []).append(med.name)
    return {slot: routine[slot] for slot in SCHEDULE_SLOTS if slot in routine}


def _medicine_paragraph(index: int, med: Medicine, *, brief: bool = False) -> str:
    """One medicine, spoken. `brief` shortens the confirm-with-pharmacist sentence when the
    whole prescription has already been flagged as hard to read."""
    opener = f"The {_ORDINALS[index]} one is" if index < len(_ORDINALS) else "Next is"
    title = med.name
    # Skip strength when the name already carries it ("Dolo 650" + "650 mg").
    if med.strength and med.strength.split()[0].lower() not in med.name.lower():
        title += f", {med.strength}"
    sentences = [f"{opener} {title}."]

    # Food timing ("after food") is tied to each time of day; other timing text ("at night")
    # only stands in for a missing frequency, so it is never said twice.
    food = _food_phrase(med.timing) if is_food_timing(med.timing) else None
    other_time = None if food else _food_phrase(med.timing)
    if med.as_needed:
        when = ", ".join(p for p in ("only when you need it", food) if p)
    elif med.schedule:
        # Tie food timing to every slot ("in the morning after food, and again at night after
        # food"): unambiguous when heard, and it survives translation intact.
        slots = [_SLOT_PHRASES[s] + (f" {food}" if food else "") for s in med.schedule]
        when = slots[0] if len(slots) == 1 else f"{', '.join(slots[:-1])}, and again {slots[-1]}"
    else:
        when = ", ".join(p for p in (med.frequency or other_time, food) if p)
    topical = _is_topical(med)
    if topical:
        sentences.append(_sentence(f"Apply it on the skin {when}".strip()))
    elif med.dosage or when:
        sentences.append(_sentence(f"Take {med.dosage or 'it'} {when}".strip()))
    if med.duration:
        # Its own sentence, so translation can't attach it to just one of the times above.
        sentences.append(_sentence(f"{'Use' if topical else 'Take'} it for {med.duration}"))

    # The doctor's own instructions belong with the dosing, before any "please confirm".
    instructions = (med.instructions or "").strip(". ")
    if instructions and instructions.lower() != (med.dosage or "").lower() and not (
        topical and instructions.lower().startswith("apply")
    ):
        sentences.append(_sentence(_capitalize(_food_phrase(instructions) or instructions)))

    # One sentence covering everything missing or guessed, instead of a warning per field.
    unsure = set(med.unsure)
    if not med.dosage and not topical:
        unsure.add("dose")
    if not (med.as_needed or med.schedule or med.frequency or other_time):
        unsure.add("frequency")
    if topical:
        unsure -= {"dose", "strength"}
    if med.as_needed:
        unsure.discard("frequency")
    phrases = [
        "how long to use it" if topical and f == "duration" else _UNSURE_PHRASES[f]
        for f in UNSURE_FIELDS
        if f in unsure
    ]
    if phrases:
        if brief:
            sentences.append(f"Please ask your pharmacist to confirm {_join(phrases)}.")
        else:
            pronoun = "it was" if len(phrases) == 1 else "they were"
            sentences.append(
                f"Please ask your pharmacist to confirm {_join(phrases)}, as {pronoun} hard to read on the prescription."
            )
    elif not med.as_needed and not med.schedule:
        sentences.append("The time of day is not written, so please ask your pharmacist when to take it.")
    return " ".join(sentences)


def _is_topical(med: Medicine) -> bool:
    text = f"{med.name} {med.dosage or ''} {med.instructions or ''}".lower()
    return any(word in text for word in TOPICAL_WORDS)


def _capitalize(text: str) -> str:
    return text[:1].upper() + text[1:]


def _recap_paragraph(rx: Prescription) -> str | None:
    routine = daily_routine(rx)
    as_needed = [m.name for m in rx.medicines if m.as_needed]
    if not routine and not as_needed:
        return None

    sentences = ["Let me repeat it once, so it is easy to remember."]
    for slot, groups in routine.items():
        slot_phrase = _SLOT_PHRASES[slot]
        first = True
        for group in _FOOD_GROUPS:
            names = groups.get(group)
            if not names:
                continue
            lead = slot_phrase[0].upper() + slot_phrase[1:] if first else "Then"
            if group == "food timing not written":
                sentences.append(f"{lead}, take {_join(names)}." if first else f"Also take {_join(names)}.")
            else:
                sentences.append(f"{lead}, {group}, take {_join(names)}.")
            first = False
    if as_needed:
        sentences.append(f"Use {_join(as_needed)} only when you need it.")
    return " ".join(sentences)


def build_spoken_script(rx: Prescription) -> str:
    """A warm, pharmacist-style explanation built only from the extracted facts.

    Written in code rather than by an LLM so medicines, doses and timings can never be
    invented or dropped; the conversational tone comes from the phrasing here and from
    colloquial translation. Paragraphs are separated by blank lines.
    """
    paragraphs: list[str] = []

    greeting = f"Namaste {rx.patient_name} ji." if rx.patient_name else "Namaste."
    source = "your prescription"
    if rx.doctor_name:
        doctor = rx.doctor_name if rx.doctor_name.lower().startswith("dr") else f"Dr. {rx.doctor_name}"
        source += f" from {doctor}"
    intro = [greeting, f"I have gone through {source}, and I will explain it to you slowly."]
    if rx.diagnosis:
        intro.append(_sentence(f"The doctor has written {_join([_lower_first(d) for d in rx.diagnosis])}"))
    hard_to_read = rx.hard_to_read
    if hard_to_read:
        intro.append(
            "This prescription is quite hard to read, so please go through it with your pharmacist as well."
        )
    paragraphs.append(" ".join(intro))

    count = len(rx.medicines)
    if count == 0:
        paragraphs.append("I could not find any medicines on this prescription. Please show it to your pharmacist.")
    elif count == 1:
        paragraphs.append("You have one medicine.")
    else:
        paragraphs.append(f"You have {count} medicines. Let us go through them one by one.")
    paragraphs.extend(
        _medicine_paragraph(i, med, brief=hard_to_read) for i, med in enumerate(rx.medicines)
    )

    extras = []
    if rx.tests:
        extras.append(_sentence(f"The doctor has also asked you to get these tests done: {_join(rx.tests)}"))
    if rx.advice:
        advice = [_lower_first(a).rstrip(".") for a in rx.advice]
        extras.append(_sentence(f"The doctor has also advised {_join(advice)}"))
    if rx.follow_up:
        extras.append(_sentence(f"For your follow-up, the doctor has written: {rx.follow_up}"))
    if extras:
        paragraphs.append(" ".join(extras))

    if rx.uncertain:
        paragraphs.append(
            "A few things were not clear on the prescription, so please confirm them with your pharmacist. "
            + " ".join(_sentence(u) for u in rx.uncertain)
        )

    recap = _recap_paragraph(rx) if count > 1 else None
    if recap:
        paragraphs.append(recap)

    paragraphs.append(
        "If anything is unclear, please ask your doctor or pharmacist before taking any medicine. "
        "Take care, and get well soon."
    )
    return "\n\n".join(speakable(p) for p in paragraphs)
