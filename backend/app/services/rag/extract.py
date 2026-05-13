"""PDF / image / text extraction.

For PDFs we first try pypdf (cheap, text-native). If a page returns very
little text we fall back to OCR via pytesseract on a rendered page image
(pdf2image / poppler). This covers the iPhone-screenshot cheatsheets the
operator uploaded.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

# Threshold below which a PDF page is treated as "probably scanned" and
# routed through OCR.
OCR_FALLBACK_CHAR_THRESHOLD = 40


@dataclass(slots=True)
class ExtractedPage:
    page_number: int
    text: str
    used_ocr: bool


def extract(path: str | Path, content_type: str) -> list[ExtractedPage]:
    p = Path(path)
    if content_type in {"text/plain", "text/markdown", "application/json"}:
        return [ExtractedPage(page_number=1, text=p.read_text("utf-8", errors="ignore"), used_ocr=False)]
    if content_type == "application/pdf" or p.suffix.lower() == ".pdf":
        return list(_extract_pdf(p))
    if content_type.startswith("image/"):
        text = _ocr_image_bytes(p.read_bytes())
        return [ExtractedPage(page_number=1, text=text, used_ocr=True)]
    # Unknown type → best-effort read.
    return [ExtractedPage(page_number=1, text=p.read_text("utf-8", errors="ignore"), used_ocr=False)]


def _extract_pdf(p: Path) -> Iterable[ExtractedPage]:
    # Lazy imports so the module loads even if poppler/tesseract are missing.
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise RuntimeError("pypdf required for PDF extraction") from e

    reader = PdfReader(str(p))
    for idx, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        used_ocr = False
        if len(text) < OCR_FALLBACK_CHAR_THRESHOLD:
            ocr_text = _ocr_pdf_page(p, idx)
            if len(ocr_text) > len(text):
                text = ocr_text
                used_ocr = True
        yield ExtractedPage(page_number=idx, text=text, used_ocr=used_ocr)


def _ocr_pdf_page(p: Path, page_number: int) -> str:
    try:
        from pdf2image import convert_from_path  # type: ignore
        from PIL import Image  # noqa: F401
    except ImportError:
        logger.warning("pdf2image not installed; skipping OCR for %s p%d", p.name, page_number)
        return ""
    try:
        images = convert_from_path(
            str(p), first_page=page_number, last_page=page_number, dpi=200, fmt="png"
        )
    except Exception as exc:  # pragma: no cover
        logger.warning("pdf2image failed for %s p%d: %s", p.name, page_number, exc)
        return ""
    if not images:
        return ""
    buf = io.BytesIO()
    images[0].save(buf, format="PNG")
    return _ocr_image_bytes(buf.getvalue())


def _ocr_image_bytes(data: bytes) -> str:
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        logger.warning("pytesseract/pillow not installed; OCR skipped")
        return ""
    try:
        with Image.open(io.BytesIO(data)) as img:
            return pytesseract.image_to_string(img) or ""
    except Exception as exc:  # pragma: no cover
        logger.warning("OCR failed: %s", exc)
        return ""
