"""Sarvam AI pipeline: Vision (digitise) -> LLM (structure) -> Translate -> TTS."""

from __future__ import annotations

import base64
import json
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx
from sarvamai import SarvamAI
from sarvamai.core.api_error import ApiError

from . import config
from .config import Language
from .errors import SahayakError
from .media import PreparedDocument
from .models import Prescription
from .text import chunk_text, extract_json

logger = logging.getLogger(__name__)

STRUCTURE_SYSTEM_PROMPT = """\
You are a careful clinical pharmacist assistant. You convert OCR text of an Indian medical \
prescription into structured JSON for a patient who cannot read the handwriting.

Rules:
- Use ONLY information present in the text. Never invent medicines, doses, or diagnoses.
- Correct obvious OCR misspellings of well-known drug names only when you are confident.
- Rewrite shorthand in plain English, e.g. OD = once a day, BD/BID = twice a day, \
TDS/TID = three times a day, QID = four times a day, HS = at bedtime, SOS = only when needed, \
STAT = immediately, AC = before food, PC = after food, 1-0-1 = once in the morning and once at night, \
1-1-1 = morning, afternoon and night, x 5d = for 5 days, Tab = tablet, Cap = capsule, Syp = syrup.
- If a value is unclear or illegible, set it to null and describe the problem in "uncertain".
- Ignore printed clinic boilerplate (phone numbers, registration numbers, addresses).

Respond with a single JSON object and nothing else, using exactly this shape:
{
  "patient_name": string | null,
  "patient_age": string | null,
  "doctor_name": string | null,
  "clinic": string | null,
  "date": string | null,
  "diagnosis": [string],
  "medicines": [
    {
      "name": string,            // brand or generic name, e.g. "Paracetamol"
      "strength": string | null, // e.g. "500 mg"
      "dosage": string | null,   // amount per dose, e.g. "1 tablet", "5 ml"
      "frequency": string | null,// plain English, e.g. "twice a day (morning and night)"
      "timing": string | null,   // e.g. "after food"
      "duration": string | null, // e.g. "5 days"
      "instructions": string | null
    }
  ],
  "tests": [string],
  "advice": [string],
  "follow_up": string | null,
  "uncertain": [string]
}"""


def _api_error_message(step: str, exc: ApiError) -> str:
    status = exc.status_code or 0
    if status in (401, 403):
        return "Your Sarvam API key was rejected. Please check SARVAM_API_KEY and try again."
    if status == 429:
        return "Sarvam's rate limit was reached. Please wait a minute and try again."
    if status == 402:
        return "Your Sarvam account is out of credits. Please top up at dashboard.sarvam.ai."
    if 400 <= status < 500:
        return f"Sarvam couldn't process this request during {step} (error {status})."
    return f"Sarvam is having trouble right now ({step}, error {status}). Please try again shortly."


@contextmanager
def _step(name: str) -> Iterator[None]:
    """Translate SDK/network failures into user-facing SahayakErrors."""
    try:
        yield
    except SahayakError:
        raise
    except ApiError as exc:
        logger.warning("Sarvam API error during %s: HTTP %s", name, exc.status_code)
        raise SahayakError(_api_error_message(name, exc)) from exc
    except httpx.TimeoutException as exc:
        raise SahayakError(f"Sarvam took too long to respond during {name}. Please try again.") from exc
    except httpx.HTTPError as exc:
        raise SahayakError(f"Network problem while contacting Sarvam during {name}. Please try again.") from exc


def _digitised_text(results: dict[str, Any]) -> str:
    """Join page text from a digitise JSON result, honouring block reading order."""
    pages: list[str] = []
    for document in results.get("documents") or []:
        for page in document.get("pages") or []:
            blocks = sorted(page.get("blocks") or [], key=lambda b: b.get("reading_order") or 0)
            text = "\n".join((b.get("text") or "").strip() for b in blocks) or (page.get("content") or "")
            if text.strip():
                pages.append(text.strip())
    return "\n\n".join(pages)


class PrescriptionPipeline:
    def __init__(self, api_key: str, *, timeout: float = 90.0) -> None:
        if not api_key:
            raise SahayakError("A Sarvam API key is required.")
        self._client = SarvamAI(api_subscription_key=api_key, timeout=timeout)

    # 1. Sarvam Vision ---------------------------------------------------------
    def digitise(self, doc: PreparedDocument, language: str = "en-IN") -> str:
        """OCR the prescription with Sarvam Vision and return its text as Markdown."""
        with _step("text recognition"):
            job = self._client.doc_ai.digitise(
                file=[(doc.filename, doc.data, doc.mime)],
                language=language,
                content_type=config.DIGITISE_CONTENT_TYPE,
            )
            status = (job.status or "").lower()
            deadline = time.monotonic() + config.DIGITISE_TIMEOUT_SECONDS
            while status not in config.DIGITISE_TERMINAL_STATUSES:
                if time.monotonic() > deadline:
                    raise SahayakError("Reading the prescription took too long. Please try again.")
                time.sleep(config.DIGITISE_POLL_SECONDS)
                status = (self._client.doc_ai.get_status(job.job_id).status or "").lower()

            if status in ("failed", "rejected"):
                raise SahayakError(
                    "Sarvam Vision couldn't read this image. Try a sharper, well-lit photo "
                    "with the whole prescription in frame."
                )

            # Digitise jobs from this endpoint produce HTML internally; "json" is the
            # format that can always be served and exposes plain-text layout blocks.
            results = self._client.doc_ai.get_results(job.job_id, format="json")

        text = _digitised_text(results.dict())
        if not text:
            raise SahayakError(
                "No text was found in the image. Try a sharper, well-lit photo of the prescription."
            )
        return text

    # 2. LLM structuring -------------------------------------------------------
    def structure(self, ocr_text: str) -> Prescription:
        """Turn raw OCR text into a structured Prescription using Sarvam's LLM."""
        messages = [
            {"role": "system", "content": STRUCTURE_SYSTEM_PROMPT},
            {"role": "user", "content": f"Prescription OCR text:\n\n{ocr_text}"},
        ]
        with _step("prescription analysis"):
            response = self._client.chat.completions(
                model=config.LLM_MODEL,
                messages=messages,
                temperature=0.1,
                max_tokens=config.LLM_MAX_TOKENS,
                # Disable hidden reasoning: with it on, sarvam-105b can exhaust the
                # token budget before emitting any JSON. Extraction doesn't need it.
                reasoning_effort=None,
                response_format={"type": "json_object"},
            )
        content = response.choices[0].message.content if response.choices else None
        try:
            data = extract_json(content or "")
        except (ValueError, json.JSONDecodeError) as exc:
            # Never log the content itself: it is patient health information.
            logger.warning("Unparseable LLM output (%d chars)", len(content or ""))
            raise SahayakError("The prescription couldn't be analysed. Please try again.") from exc
        return Prescription.from_dict(data)

    # 3. Translation -----------------------------------------------------------
    def translate(self, text: str, target: str) -> str:
        """Translate English text to the target language with sarvam-translate."""
        if target == "en-IN":
            return text
        translated: list[str] = []
        with _step("translation"):
            for chunk in chunk_text(text, config.TRANSLATE_CHUNK_CHARS):
                response = self._client.text.translate(
                    input=chunk,
                    source_language_code="en-IN",
                    target_language_code=target,
                    model=config.TRANSLATE_MODEL,
                )
                translated.append(response.translated_text)
        return "\n".join(translated)

    # 4. Text-to-speech --------------------------------------------------------
    def synthesize(self, text: str, language: Language, voice: str, pace: float = 1.0) -> bytes:
        """Speak the text with Bulbul and return MP3 audio.

        MP3 keeps responses well under serverless payload limits; MP3 streams are
        frame-based, so per-chunk outputs can be concatenated directly.
        """
        audio = bytearray()
        with _step("audio generation"):
            for chunk in chunk_text(text, config.TTS_CHUNK_CHARS):
                response = self._client.text_to_speech.convert(
                    text=chunk,
                    language_code=language.code,
                    model=language.tts_model,
                    speaker=voice,
                    pace=pace,
                    output_audio_codec="mp3",
                    speech_sample_rate=config.TTS_SAMPLE_RATE,
                )
                for encoded in response.audios:
                    audio.extend(base64.b64decode(encoded))
        if not audio:
            raise SahayakError("No audio was generated. Please try again.")
        return bytes(audio)
