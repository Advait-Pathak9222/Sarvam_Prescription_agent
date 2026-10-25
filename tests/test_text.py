import pytest

from sahayak.models import Medicine, Prescription
from sahayak.text import build_patient_summary, chunk_text, extract_json


def test_extract_json_handles_fences_and_think_blocks():
    raw = '<think>reasoning</think>\n```json\n{"medicines": [{"name": "Dolo"}]}\n```'
    assert extract_json(raw) == {"medicines": [{"name": "Dolo"}]}


def test_extract_json_ignores_trailing_text():
    assert extract_json('Here you go: {"a": 1} hope that helps') == {"a": 1}


def test_extract_json_raises_without_object():
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_chunk_text_respects_limit_and_keeps_order():
    text = "\n".join(f"Line {i} " + "word " * 30 for i in range(20))
    chunks = chunk_text(text, 400)
    assert all(len(c) <= 400 for c in chunks)
    assert "".join(chunks).replace("\n", "").replace(" ", "") == text.replace("\n", "").replace(" ", "")


def test_chunk_text_splits_overlong_line():
    chunks = chunk_text("word " * 500, 300)
    assert len(chunks) > 1 and all(len(c) <= 300 for c in chunks)


def test_summary_mentions_every_medicine_and_safety_note():
    rx = Prescription(
        doctor_name="Anita Sharma",
        date="12/10/2026",
        diagnosis=["Viral fever"],
        medicines=[
            Medicine(name="Dolo 650", strength="650 mg", dosage="1 tablet",
                     frequency="twice a day", timing="after food", duration="5 days"),
            Medicine(name="Pantop", strength="40 mg", dosage="1 tablet", frequency="once a day"),
        ],
        advice=["Plenty of fluids", "Warm saline gargles"],
    )
    summary = build_patient_summary(rx)
    assert "Dr. Anita Sharma" in summary
    assert "Medicine 1: Dolo 650. Take 1 tablet, twice a day, after food, for 5 days." in summary
    assert "Medicine 2: Pantop 40 mg." in summary
    assert "Doctor's advice: Plenty of fluids. Warm saline gargles." in summary
    assert summary.endswith("before taking any medicine.")
