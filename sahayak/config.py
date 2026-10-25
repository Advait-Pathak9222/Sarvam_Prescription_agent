"""Static configuration: model IDs, API limits, and supported languages/voices."""

from __future__ import annotations

from dataclasses import dataclass

# --- Sarvam models -----------------------------------------------------------
LLM_MODEL = "sarvam-105b"
# mayura:v1 in modern-colloquial mode gives everyday spoken phrasing (natural code-mixing,
# medicine names kept as written) and covers all 11 languages below.
TRANSLATE_MODEL = "mayura:v1"
TRANSLATE_MODE = "modern-colloquial"
TRANSLATE_FALLBACK_MODEL = "sarvam-translate:v1"  # formal register; used if mayura fails
TTS_MODEL = "bulbul:v4-flash"
# bulbul:v4-flash has no personas for Malayalam or Odia yet; those use v3.
TTS_FALLBACK_MODEL = "bulbul:v3"

# --- Limits (kept below the documented API maximums) --------------------------
TRANSLATE_CHUNK_CHARS = 900  # mayura:v1 max is 1000
TRANSLATE_WORKERS = 6
TTS_CHUNK_CHARS = 2000  # bulbul v3 / v4-flash max is 2500
LLM_MAX_TOKENS = 4096
TTS_SAMPLE_RATE = 22050

DIGITISE_POLL_SECONDS = 2.0
DIGITISE_TIMEOUT_SECONDS = 180.0
DIGITISE_CONTENT_TYPE = "mixed"  # printed letterhead + handwritten body
DIGITISE_TERMINAL_STATUSES = frozenset({"completed", "partially_completed", "failed", "rejected"})

IMAGE_MAX_SIDE_PX = 2400
# Vercel caps serverless request bodies at 4.5 MB; the browser downsizes photos first.
MAX_UPLOAD_BYTES = 4 * 1024 * 1024
MAX_TEXT_CHARS = 8000
ALLOWED_UPLOAD_TYPES = frozenset({"image/jpeg", "image/png", "image/webp", "application/pdf"})


@dataclass(frozen=True)
class Language:
    code: str
    name: str
    native: str
    voices: tuple[str, ...]  # bulbul:v4-flash persona IDs, best default first
    fallback_voices: tuple[str, ...] = ("ritu", "priya", "aditya", "shubh")  # bulbul:v3

    @property
    def label(self) -> str:
        return self.native if self.native == self.name else f"{self.native} · {self.name}"

    @property
    def tts_model(self) -> str:
        return TTS_MODEL if self.voices else TTS_FALLBACK_MODEL

    @property
    def available_voices(self) -> tuple[str, ...]:
        return self.voices or self.fallback_voices


LANGUAGES: tuple[Language, ...] = (
    Language("hi-IN", "Hindi", "हिन्दी", (
        "ritu_hi_medical", "ritu_hi_conversation", "simran_hi_conversation",
        "aditya_hi_conversational", "rahul_hi_conversational", "shubh_hi_customer",
    )),
    Language("en-IN", "English", "English", (
        "ritu_en_medical", "ishita_en_medical", "roopa_en_conversational",
        "dev_en_conversational", "simran_en_conversation", "shubh_en_narration",
    )),
    Language("bn-IN", "Bengali", "বাংলা", ("roopa_bn_conversational", "arnab_bn_conversation")),
    Language("mr-IN", "Marathi", "मराठी", (
        "ishita_mr_conversational", "nilesh_mr_conversation", "neha_mr_narration", "soham_mr_narration",
    )),
    Language("ta-IN", "Tamil", "தமிழ்", ("gokul_ta_narration", "vijay_ta_narration")),
    Language("te-IN", "Telugu", "తెలుగు", (
        "kavitha_te_conversation", "pooja_te_conversation", "tarun_te_narration",
    )),
    Language("kn-IN", "Kannada", "ಕನ್ನಡ", (
        "chaitra_kn_conversation", "chetan_kn_conversation", "suchitra_kn_narration",
    )),
    Language("gu-IN", "Gujarati", "ગુજરાતી", (
        "pooja_gu_conversational", "bhavik_gu_conversation", "pooja_gu_customer",
    )),
    Language("pa-IN", "Punjabi", "ਪੰਜਾਬੀ", (
        "anand_pa_conversation", "harpreet_pa_narration", "jaspal_pa_banking",
    )),
    Language("ml-IN", "Malayalam", "മലയാളം", ()),
    Language("od-IN", "Odia", "ଓଡ଼ିଆ", ()),
)

LANGUAGES_BY_CODE: dict[str, Language] = {lang.code: lang for lang in LANGUAGES}


# Speaker genders as documented in Sarvam's Bulbul voice catalog (v3 male/female lists;
# v4 personas reuse these names). Used so translations use matching first-person forms
# (e.g. Hindi "समझाती हूँ" vs "समझाता हूँ"). Undocumented names are left unset.
_MALE_VOICES = frozenset({
    "aayan", "aditya", "advait", "amit", "anand", "ashutosh", "dev", "gokul", "kabir", "manan",
    "mani", "mohit", "rahul", "ratan", "rehan", "rohan", "shubh", "soham", "sumit", "sunny",
    "tarun", "varun", "vijay",
})
_FEMALE_VOICES = frozenset({
    "ishita", "kavitha", "kavya", "neha", "pooja", "priya", "ritu", "roopa", "rupali", "shreya",
    "shruti", "simran", "suhani", "tanya",
})


def voice_gender(voice_id: str | None) -> str | None:
    """'ritu_hi_medical' -> 'Female'; unknown or undocumented voices -> None."""
    name = (voice_id or "").split("_")[0].lower()
    if name in _MALE_VOICES:
        return "Male"
    if name in _FEMALE_VOICES:
        return "Female"
    return None


def voice_label(voice_id: str) -> str:
    """'ritu_hi_medical' -> 'Ritu · medical'; v3 names like 'ritu' -> 'Ritu'."""
    parts = voice_id.split("_")
    name = parts[0].title()
    return f"{name} · {parts[-1]}" if len(parts) >= 3 else name
