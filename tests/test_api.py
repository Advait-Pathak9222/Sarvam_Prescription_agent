import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import api.index as server
from sahayak.errors import SahayakError
from sahayak.models import Medicine, Prescription


class FakePipeline:
    def digitise(self, doc, language="en-IN"):
        assert doc.mime == "image/jpeg"
        return "Tab Dolo 650 1-0-1 x 5d"

    def structure(self, ocr_text):
        return Prescription(medicines=[Medicine(name="Dolo 650", dosage="1 tablet")])

    def translate(self, text, target):
        return f"[{target}] {text}"

    def synthesize(self, text, language, voice, pace=1.0):
        return b"ID3fake-mp3"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(server, "get_pipeline", lambda: FakePipeline())
    return TestClient(server.app)


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(buf, format="JPEG")
    return buf.getvalue()


def test_languages_lists_voices(client):
    data = client.get("/api/languages").json()
    hindi = next(l for l in data if l["code"] == "hi-IN")
    assert hindi["tts_model"] == "bulbul:v4-flash" and hindi["voices"]
    malayalam = next(l for l in data if l["code"] == "ml-IN")
    assert malayalam["tts_model"] == "bulbul:v3"


def test_full_flow(client):
    r = client.post("/api/digitise", files={"file": ("rx.jpg", _jpeg(), "image/jpeg")}, data={"language": "en-IN"})
    assert r.status_code == 200 and "Dolo" in r.json()["text"]

    r = client.post("/api/analyse", json={"ocr_text": r.json()["text"]})
    assert r.status_code == 200
    assert r.json()["prescription"]["medicines"][0]["name"] == "Dolo 650"
    summary = r.json()["summary"]

    r = client.post("/api/translate", json={"text": summary, "target": "hi-IN"})
    assert r.json()["text"].startswith("[hi-IN]")

    r = client.post("/api/speak", json={"text": "नमस्ते", "language": "hi-IN"})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg"


def test_rejects_unsupported_upload(client):
    r = client.post("/api/digitise", files={"file": ("a.txt", b"hello", "text/plain")})
    assert r.status_code == 415


def test_rejects_unknown_voice_and_language(client):
    assert client.post("/api/speak", json={"text": "x", "language": "hi-IN", "voice": "nope"}).status_code == 422
    assert client.post("/api/translate", json={"text": "x", "target": "xx-IN"}).status_code == 422



def test_sahayak_error_maps_to_502(monkeypatch):
    class Broken(FakePipeline):
        def translate(self, text, target):
            raise SahayakError("Sarvam is down")

    monkeypatch.setattr(server, "get_pipeline", lambda: Broken())
    r = TestClient(server.app).post("/api/translate", json={"text": "x", "target": "hi-IN"})
    assert r.status_code == 502 and r.json()["detail"] == "Sarvam is down"


def test_missing_api_key_returns_503(monkeypatch):
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)
    r = TestClient(server.app).post("/api/translate", json={"text": "x", "target": "hi-IN"})
    assert r.status_code == 503
