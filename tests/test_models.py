from sahayak.models import Prescription


def test_from_dict_tolerates_loose_llm_output():
    rx = Prescription.from_dict({
        "patient_name": "  Ramesh ",
        "doctor_name": "null",
        "diagnosis": "Viral fever",
        "medicines": [{"name": "Dolo 650", "dosage": 1}, {"name": ""}, "garbage", {"dosage": "5 ml"}],
        "tests": None,
        "uncertain": ["", "Dose of item 3 unclear"],
    })
    assert rx.patient_name == "Ramesh"
    assert rx.doctor_name is None
    assert rx.diagnosis == ["Viral fever"]
    assert [m.name for m in rx.medicines] == ["Dolo 650"]
    assert rx.medicines[0].dosage == "1"
    assert rx.tests == []
    assert rx.uncertain == ["Dose of item 3 unclear"]
    assert not rx.is_empty


def test_empty_prescription():
    assert Prescription.from_dict({}).is_empty
