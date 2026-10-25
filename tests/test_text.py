import pytest

from sahayak.config import voice_gender
from sahayak.models import Medicine, Prescription
from sahayak.text import build_spoken_script, chunk_text, daily_routine, extract_json, speakable


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


def test_speakable_expands_units_and_slashes():
    assert speakable("650 mg, 10ml, 5 mcg, review / SOS") == "650 milligram, 10 millilitre, 5 microgram, review or SOS"
    assert speakable("Mgnesium") == "Mgnesium"


def test_voice_gender_uses_documented_names_only():
    assert voice_gender("ritu_hi_medical") == "Female"
    assert voice_gender("gokul_ta_narration") == "Male"
    assert voice_gender("chaitra_kn_conversation") is None
    assert voice_gender(None) is None


def _sample() -> Prescription:
    return Prescription(
        patient_name="Ramesh Kumar",
        doctor_name="Anita Sharma",
        diagnosis=["Viral fever", "Acute pharyngitis"],
        medicines=[
            Medicine(name="Dolo 650", strength="650 mg", dosage="1 tablet", timing="after food",
                     duration="5 days", schedule=["morning", "night"]),
            Medicine(name="Ascoril LS", dosage="10 ml", frequency="three times a day",
                     schedule=["morning", "afternoon", "night"]),
            Medicine(name="Pantop", strength="40 mg", dosage="1 tablet", frequency="once a day",
                     timing="before food (empty stomach)"),
            Medicine(name="Ondem", dosage="1 tablet", as_needed=True),
        ],
        tests=["CBC", "CRP"],
        advice=["Plenty of fluids", "Warm saline gargles"],
        follow_up="Review after 5 days / SOS",
    )


def test_daily_routine_only_uses_explicit_schedules():
    routine = daily_routine(_sample())
    assert routine == {
        "morning": {"after food": ["Dolo 650"], "food timing not written": ["Ascoril LS"]},
        "afternoon": {"food timing not written": ["Ascoril LS"]},
        "night": {"after food": ["Dolo 650"], "food timing not written": ["Ascoril LS"]},
    }  # Pantop has no time of day and Ondem is as-needed: neither is placed in a slot


def test_spoken_script_is_conversational_and_faithful():
    script = build_spoken_script(_sample())
    paragraphs = script.split("\n\n")
    assert paragraphs[0].startswith("Namaste Ramesh Kumar ji.")
    assert "Dr. Anita Sharma" in paragraphs[0]
    assert "The doctor has written viral fever and acute pharyngitis." in paragraphs[0]
    assert "You have 4 medicines. Let us go through them one by one." in script
    assert "The first one is Dolo 650. Take 1 tablet in the morning after food, and again at night after food, for 5 days." in script
    assert "Take 10 millilitre in the morning, in the afternoon, and again at night." in script
    # No time of day is written for Pantop, so the script must say so instead of guessing.
    assert ("The third one is Pantop, 40 milligram. Take 1 tablet once a day, before food (empty stomach). "
            "The time of day is not written") in script
    assert "Take 1 tablet only when you need it." in script
    assert "The doctor has also advised plenty of fluids and warm saline gargles." in script
    assert "the doctor has written: Review after 5 days or SOS." in script
    recap = next(p for p in paragraphs if p.startswith("Let me repeat it once"))
    assert "In the morning, after food, take Dolo 650. Also take Ascoril LS." in recap
    assert "Pantop" not in recap
    assert "Take Ondem only when you need it." in recap
    assert paragraphs[-1].endswith("get well soon.")
    assert " mg" not in script and " ml" not in script


def test_spoken_script_handles_missing_details():
    rx = Prescription(medicines=[Medicine(name="Vitamin D3")])
    script = build_spoken_script(rx)
    assert script.startswith("Namaste. I have gone through your prescription")
    assert "You have one medicine." in script
    assert "The amount is not clear on the prescription" in script
    assert "How often to take it is not clear" in script
    assert "Let me repeat it once" not in script


def test_spoken_script_without_medicines():
    assert "I could not find any medicines" in build_spoken_script(Prescription())
