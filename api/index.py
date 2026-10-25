"""HTTP API for Prescription Sahayak.

Deployed on Vercel as a single Python serverless function (all /api/* routes are
rewritten here by vercel.json). Run locally with:

    uvicorn api.index:app --reload
"""

from __future__ import annotations

import logging
import os
import sys
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sahayak import __version__, config  # noqa: E402
from sahayak.config import LANGUAGES, LANGUAGES_BY_CODE, voice_label  # noqa: E402
from sahayak.errors import SahayakError  # noqa: E402
from sahayak.media import prepare_document  # noqa: E402
from sahayak.pipeline import PrescriptionPipeline  # noqa: E402
from sahayak.text import build_patient_summary  # noqa: E402

ON_VERCEL = bool(os.getenv("VERCEL"))
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sahayak.api")


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader for local development (Vercel injects env vars itself)."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


if not ON_VERCEL:
    _load_dotenv(ROOT / ".env")

app = FastAPI(
    title="Prescription Sahayak API",
    version=__version__,
    docs_url=None if ON_VERCEL else "/api/docs",
    redoc_url=None,
    openapi_url=None if ON_VERCEL else "/api/openapi.json",
)


@lru_cache(maxsize=1)
def _pipeline(api_key: str) -> PrescriptionPipeline:
    return PrescriptionPipeline(api_key)


def get_pipeline() -> PrescriptionPipeline:
    api_key = os.getenv("SARVAM_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(503, "The server is not configured yet: SARVAM_API_KEY is missing.")
    return _pipeline(api_key)


@app.exception_handler(SahayakError)
async def _sahayak_error(_: Request, exc: SahayakError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=502)


# --- Schemas -----------------------------------------------------------------
class AnalyseRequest(BaseModel):
    ocr_text: str = Field(min_length=1, max_length=config.MAX_TEXT_CHARS)


class TranslateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=config.MAX_TEXT_CHARS)
    target: str


class SpeakRequest(BaseModel):
    # One TTS chunk per request keeps each MP3 response well under Vercel's 4.5 MB
    # limit; the browser splits longer text and joins the audio.
    text: str = Field(min_length=1, max_length=config.TTS_CHUNK_CHARS)
    language: str
    voice: str | None = None
    pace: float = Field(default=1.0, ge=0.5, le=2.0)


def _language(code: str):
    language = LANGUAGES_BY_CODE.get(code)
    if language is None:
        raise HTTPException(422, f"Unsupported language: {code}")
    return language


# --- Routes ------------------------------------------------------------------
@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "version": __version__, "configured": bool(os.getenv("SARVAM_API_KEY"))}


@app.get("/api/languages")
def languages() -> list[dict]:
    return [
        {
            "code": lang.code,
            "name": lang.name,
            "native": lang.native,
            "label": lang.label,
            "tts_model": lang.tts_model,
            "voices": [{"id": v, "label": voice_label(v)} for v in lang.available_voices],
        }
        for lang in LANGUAGES
    ]


@app.post("/api/digitise")
async def digitise(file: UploadFile = File(...), language: str = Form("en-IN")) -> dict:
    """Step 1 — Sarvam Vision: read the prescription image/PDF into text."""
    _language(language)
    mime = (file.content_type or "").lower()
    if mime not in config.ALLOWED_UPLOAD_TYPES:
        raise HTTPException(415, "Please upload a JPG, PNG, WebP or PDF file.")
    data = await file.read(config.MAX_UPLOAD_BYTES + 1)
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "That file is too large. Please use a file under 4 MB.")
    if not data:
        raise HTTPException(422, "The uploaded file is empty.")

    doc = prepare_document(data, file.filename or "prescription", mime)
    text = get_pipeline().digitise(doc, language=language)
    return {"text": text}


@app.post("/api/analyse")
def analyse(body: AnalyseRequest) -> dict:
    """Step 2 — Sarvam LLM: structure OCR text into medicines, dosage, diagnosis."""
    prescription = get_pipeline().structure(body.ocr_text)
    return {"prescription": prescription.to_dict(), "summary": build_patient_summary(prescription)}


@app.post("/api/translate")
def translate(body: TranslateRequest) -> dict:
    """Step 3 — Sarvam Translate: English summary to the patient's language."""
    _language(body.target)
    return {"text": get_pipeline().translate(body.text, body.target), "language": body.target}


@app.post("/api/speak")
def speak(body: SpeakRequest) -> Response:
    """Step 4 — Sarvam Bulbul TTS: return the spoken summary as MP3."""
    language = _language(body.language)
    voice = body.voice or language.available_voices[0]
    if voice not in language.available_voices:
        raise HTTPException(422, f"Voice '{voice}' is not available for {language.name}.")
    audio = get_pipeline().synthesize(body.text, language, voice, body.pace)
    return Response(audio, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})


# Local development: serve the static site from the same origin.
if not ON_VERCEL:
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=ROOT / "public", html=True), name="site")
