"""Safety net for messy handwriting, using patterns from a real Commonwealth-style prescription."""

import re

import pytest

from sahayak.models import Medicine, Prescription
from sahayak.normalize import (
    DURATION_NOTE,
    is_plain_language,
    normalize_medicine,
    normalize_prescription,
    spell_duration,
    unclear_sentence,
)
from sahayak.text import build_spoken_script


@pytest.mark.parametrize(
    ("raw", "spoken"),
    [("10/7", "10 days"), ("1/12", "1 month"), ("3/12", "3 months"), ("2/52", "2 weeks"),
     ("1/7", "1 day"), ("for 5/7", "5 days"), ("1 month", "1 month"), (None, None)],
)
def test_spell_duration(raw, spoken):
    assert spell_duration(raw) == spoken


@pytest.mark.parametrize("value", ["4/2", "x", "T", "10/7 ->", "1/2"])
def test_raw_shorthand_is_not_plain_language(value):
    assert not is_plain_language(value)


@pytest.mark.parametrize("value", ["1 tablet", "at night", "10 ml", "once a day", "5mg"])
def test_plain_values_are_kept(value):
    assert is_plain_language(value)


def test_raw_shorthand_never_reaches_the_patient():
    med = normalize_medicine(Medicine(name="Nebilong", dosage="T", frequency="x", duration="4/2"))
    assert (med.dosage, med.frequency, med.duration) == (None, None, None)


def test_commonwealth_duration_is_spelled_out():
    assert normalize_medicine(Medicine(name="Derzde", duration="10/7")).duration == "10 days"


def test_tablet_in_grams_is_never_spoken():
    rx = normalize_prescription(Prescription(medicines=[Medicine(name="Amlong", strength="10g", dosage="1 tablet")]))
    med = rx.medicines[0]
    assert med.strength is None
    assert med.unsure == ["strength"]
    assert med.unclear == "The strength was hard to read, so please confirm with your pharmacist."


def test_grams_kept_for_non_solid_forms():
    assert normalize_medicine(Medicine(name="Volini gel", strength="30g")).strength == "30g"


@pytest.mark.parametrize(
    ("frequency", "plain", "schedule"),
    [("Nacht", "at night", ["night"]), ("nocte", "at night", ["night"]), ("od", "once a day", []),
     ("BD", "twice a day", []), ("mane", "in the morning", ["morning"])],
)
def test_latin_frequency_words_are_translated(frequency, plain, schedule):
    med = normalize_medicine(Medicine(name="X", dosage="1 tablet", frequency=frequency))
    assert med.frequency == plain
    assert med.schedule == schedule


def test_prn_becomes_as_needed():
    med = normalize_medicine(Medicine(name="Volini gel", frequency="prn", instructions="as needed"))
    assert med.as_needed and med.frequency is None and med.instructions is None


def test_implausible_counts_are_dropped():
    med = normalize_medicine(Medicine(name="Amlong", dosage="100 tablets", frequency="100 times"))
    assert med.dosage is None and med.frequency is None


def test_timing_word_lines_and_initials_are_not_medicines():
    assert normalize_medicine(Medicine(name="Nocte")) is None
    assert normalize_medicine(Medicine(name="HTHNS", duration="4/12")) is None
    # A vowel-less abbreviation with a strength is a real medicine (e.g. HCQS 200).
    assert normalize_medicine(Medicine(name="HCQS", strength="200 mg")) is not None


def test_unclear_sentence():
    assert unclear_sentence([]) is None
    assert unclear_sentence(["duration", "strength"]) == (
        "The strength and how long to take it were hard to read, so please confirm with your pharmacist."
    )


CLEAN_OCR = (
    "Dr. Anita Sharma, MBBS, MD\nName: Ramesh Kumar Age: 54 M 12/10/2026\nRx\n"
    "1. Tab Dolo 650 1-0-1 x 5d PC\n2. Cap Amoxyclav 625 1-0-1 x 5 days after food\n"
    "3. Syp Ascoril LS 10 ml TDS\n4. Tab Pantop 40 mg OD AC (empty stomach)"
)
MESSY_OCR = (
    "N.P. Bulong 10g x 100PB -> 4/2 Nebilong 5g x as -> 4/2 Derzde 30g x Nacht -> 10/7 "
    "Advanke. T + ds -> 2 um Volici gel apply prn HTHNS 4/12 Dispensed by"
)


def test_clean_prescription_raises_no_warnings():
    rx = normalize_prescription(Prescription(medicines=[
        Medicine(name="Dolo 650", strength="650 mg", dosage="1 tablet", timing="after food",
                 duration="5 days", schedule=["morning", "night"]),
        Medicine(name="Amoxyclav 625", dosage="1 capsule", duration="5 days", schedule=["morning", "night"]),
        Medicine(name="Ascoril LS", dosage="10 ml", frequency="three times a day"),
        Medicine(name="Pantop", strength="40 mg", dosage="1 tablet", frequency="once a day"),
    ], uncertain=["The duration for Pantop is not specified."]), CLEAN_OCR)
    assert not rx.hard_to_read
    assert rx.uncertain == []  # the LLM's free-text notes are not read aloud
    assert all(m.unsure == [] and m.unclear is None for m in rx.medicines)


def test_messy_prescription_is_checked_against_the_ocr():
    rx = normalize_prescription(Prescription(doctor_name="HTHNS", medicines=[
        Medicine(name="Amlong", strength="10 mg", dosage="1 tablet", frequency="once a day", duration="1 month"),
        Medicine(name="Nebilong", strength="5 mg", dosage="1 tablet", frequency="as", duration="4/2"),
        Medicine(name="Derzde", strength="30 mg", dosage="1 tablet", frequency="Nacht", duration="10/7"),
    ]), MESSY_OCR)
    amlong, nebilong, derzde = rx.medicines
    assert amlong.unsure == ["name", "strength"]  # "Bulong" was corrected; OCR said "10g"
    assert nebilong.unsure == ["strength"] and nebilong.frequency is None and nebilong.duration is None
    assert (derzde.frequency, derzde.schedule, derzde.duration) == ("at night", ["night"], "10 days")
    assert rx.hard_to_read
    assert rx.uncertain == [DURATION_NOTE]  # "-> 4/2" is not a valid /7, /52 or /12 duration
    assert rx.doctor_name is None


def test_messy_prescription_script_is_clean():
    """End to end on the kind of output the LLM gives for a very messy prescription."""
    rx = normalize_prescription(Prescription(medicines=[
        Medicine(name="Amlong", strength="10g", dosage="1 tablet", frequency="x", duration="1/12",
                 unsure=["duration"]),
        Medicine(name="Derzde", strength="30 mg", dosage="1 tablet", frequency="Nacht", duration="10/7"),
        Medicine(name="Advanke", dosage="T", duration="2/52", unsure=["name"]),
        Medicine(name="Volini gel", dosage="apply", frequency="prn", instructions="apply"),
    ]))
    script = build_spoken_script(rx)
    assert "/" not in script and "10g" not in script and not re.search(r"\bgrams?\b", script)
    assert "This prescription is quite hard to read" in script
    assert "The first one is Amlong. Take 1 tablet. Take it for 1 month." in script
    assert "Please ask your pharmacist to confirm the strength, how often to take it and how long to take it." in script
    assert "The second one is Derzde, 30 milligram. Take 1 tablet at night. Take it for 10 days." in script
    assert "Take it for 2 weeks." in script
    assert "Apply it on the skin only when you need it." in script
    assert "Apply it on the skin only when you need it. apply" not in script.lower()
