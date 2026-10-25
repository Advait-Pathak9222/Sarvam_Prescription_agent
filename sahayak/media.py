"""Upload normalisation for Sarvam Vision."""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

from .config import IMAGE_MAX_SIDE_PX
from .errors import SahayakError


@dataclass(frozen=True)
class PreparedDocument:
    data: bytes
    filename: str
    mime: str


def prepare_document(data: bytes, filename: str, mime: str | None) -> PreparedDocument:
    """Normalise an upload for Sarvam Vision.

    PDFs pass through unchanged. Images are rotated per EXIF (phone photos), converted
    to RGB, downscaled to a sane resolution, and re-encoded as JPEG.
    """
    if (mime or "").lower() == "application/pdf" or filename.lower().endswith(".pdf"):
        return PreparedDocument(data, filename, "application/pdf")

    try:
        image = Image.open(io.BytesIO(data))
        image = ImageOps.exif_transpose(image).convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise SahayakError("That file doesn't look like a readable image. Please try a JPG, PNG or PDF.") from exc

    image.thumbnail((IMAGE_MAX_SIDE_PX, IMAGE_MAX_SIDE_PX), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=90, optimize=True)
    return PreparedDocument(out.getvalue(), "prescription.jpg", "image/jpeg")
