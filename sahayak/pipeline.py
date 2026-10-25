"""Sarvam AI pipeline: Vision (digitise) -> LLM (structure) -> Translate (colloquial) -> TTS."""

from __future__ import annotations

import base64
import json
import logging
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
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
from .normalize import normalize_prescription
from .text import chunk_text, extract_json, split_paragraphs

logger = logging.getLogger(__name__)

STRUCTURE_SYSTEM_PROMPT = """\
You are an experienced clinical pharmacist. You convert OCR text of a handwritten medical \
prescription (India, or Commonwealth-style such as Kenya or the UK) into structured JSON for a \
patient who cannot read the handwriting. The OCR comes from messy handwriting, so read it the \
way a pharmacist would, but never invent anything.

Reading the prescription:
- Every handwritten medicine line is one medicine, usually: name, strength, dose, frequency, \
then an arrow (-> or →) followed by the duration. Include EVERY medicine line, even when parts \
are unreadable. Never drop a line and never split one line into two medicines.
- Printed form fields (surname, other names, D.O.B., registration number, address, department, \
"Dispensed by", "Checked by", "Doctor's stamp", form codes like "AKUH 12") are not instructions. \
A scribble near DATE or the signature is a date or signature, not a medicine: a short \
all-capitals token followed by a date-like value at the very end (e.g. "HTHNS 4/12") is the \
doctor's initials and the date.
- Frequency shorthand: od / OD = once a day; bd / BD / BID = twice a day; tds / TDS / TID = three \
times a day; qid / QID = four times a day; nocte / noct / HS = at night (bedtime); mane = in the \
morning; prn / SOS = only when needed; stat = immediately; AC = before food; PC = after food; \
"x" before a frequency means "times" or "for" (e.g. "x nocte" = at night). 1-0-1, 1-1-1, 0-0-1 \
style counts mean morning-afternoon-night.
- Dose shorthand: "T", "Tab", "i" or "i" with a dot = 1 tablet; "ii" = 2 tablets; "1/2" or "½" \
before a form = half a tablet; Cap = capsule; Syp = syrup; "apply" = apply on the skin.
- Duration shorthand (Commonwealth notation): N/7 = N days, N/52 = N weeks, N/12 = N months \
(e.g. "-> 10/7" = for 10 days, "-> 2/52" = for 2 weeks, "-> 1/12" = for 1 month); \
"wk" / "wks" = weeks; "5d" = 5 days.

Common OCR misreads of handwriting (correct them):
- "1/12" is often read as "4/2", "5/2", "Y/12", "4/12" or "1/2" right after an arrow. A duration \
after an arrow ending in "/2" is almost certainly "/12" (months): treat "-> 4/2" as 1 month.
- Handwritten "mg" is often read as "g". Tablets and capsules are almost never measured in grams, \
so for a tablet or capsule read "10g" as "10 mg". Never output grams for a tablet or capsule.
- "od" is often read as "as", "ds", "ad", "0d" or "o.d"; "nocte" as "Nacht", "Noct", "Nocti"; \
"wk" as "um", "uk" or "wh".
- Medicine names: correct to the real brand or generic name only when the name, strength and \
dose clearly fit a known medicine (e.g. "Bulong 10" or "Amlong 10" = Amlong 10 mg); otherwise \
keep the name as read.

Output rules:
- Use ONLY information in the text. Never invent medicines, doses, durations or diagnoses.
- "diagnosis" only when a diagnosis is explicitly written (e.g. after "Dx", "Diagnosis", \
"Imp" or "c/o"); otherwise []. A medicine name is never a diagnosis.
- Every field must be plain English a patient understands ("1 tablet", "once a day", "at night", \
"1 month"). Never copy raw shorthand such as "4/2", "x", "T", "as" or "10/7" into a field: \
interpret it, or set the field to null if you cannot.
- Fill "schedule" ONLY when the time of day is explicit: 1-0-1 or BD = ["morning", "night"]; \
1-1-1 or TDS = ["morning", "afternoon", "night"]; 0-0-1 = ["night"]; 1-0-0 or mane = ["morning"]; \
nocte or HS = ["night"]; a written time of day counts too. Otherwise leave it []. For example \
"Tab Pantop 40 OD AC (empty stomach)" has schedule [] because no time of day is written: \
"before food" or "empty stomach" is NOT a time of day.

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
      "frequency": string | null,// plain English, e.g. "once a day", "twice a day"
      "timing": string | null,   // e.g. "after food", "at night"
      "duration": string | null, // e.g. "5 days", "2 weeks", "1 month"
      "instructions": string | null,
      "schedule": [string],      // times of day, only from: "morning", "afternoon", "evening", "night", "bedtime"
      "as_needed": boolean       // true only for SOS / PRN / "when needed" medicines
    }
  ],
  "tests": [string],
  "advice": [string],
  "follow_up": string | null
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
        return normalize_prescription(Prescription.from_dict(data), ocr_text)

    # 3. Translation -----------------------------------------------------------
    def translate(self, text: str, target: str, speaker_gender: str | None = None) -> str:
        """Translate the English script into everyday spoken language.

        Uses mayura:v1 in modern-colloquial mode, one paragraph per request (in parallel)
        so paragraph breaks survive for display and TTS chunking. Falls back to the formal
        sarvam-translate model if mayura fails.
        """
        if target == "en-IN":
            return text
        pieces = [
            chunk
            for paragraph in split_paragraphs(text)
            for chunk in chunk_text(paragraph, config.TRANSLATE_CHUNK_CHARS)
        ]
        try:
            translated = self._translate_pieces(pieces, target, speaker_gender, colloquial=True)
        except SahayakError:
            logger.warning("Colloquial translation failed for %s; using formal model", target)
            translated = self._translate_pieces(pieces, target, speaker_gender, colloquial=False)
        return "\n\n".join(translated)

    def _translate_pieces(
        self, pieces: list[str], target: str, speaker_gender: str | None, *, colloquial: bool
    ) -> list[str]:
        options: dict[str, Any] = (
            {"model": config.TRANSLATE_MODEL, "mode": config.TRANSLATE_MODE}
            if colloquial
            else {"model": config.TRANSLATE_FALLBACK_MODEL}
        )
        if speaker_gender:
            options["speaker_gender"] = speaker_gender

        def one(piece: str) -> str:
            response = self._client.text.translate(
                input=piece, source_language_code="en-IN", target_language_code=target, **options
            )
            return response.translated_text.strip()

        with _step("translation"), ThreadPoolExecutor(max_workers=config.TRANSLATE_WORKERS) as pool:
            return list(pool.map(one, pieces))

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
