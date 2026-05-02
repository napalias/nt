"""LLM extraction of structured facts from territorial planning PDFs.

Pipeline:
1. Extract text from PDF (pymupdf for text-based, pytesseract for scanned)
2. Build Claude prompt asking for structured planning facts
3. Call Anthropic API with tool_use for structured output
4. Save extracted data to PlanningDocument

Usage:
    from apps.planning.services.extract import extract_planning_facts
    extract_planning_facts(planning_document)
"""

from __future__ import annotations

import logging

import anthropic
from django.conf import settings

logger = logging.getLogger(__name__)

EXTRACT_MODEL = "claude-sonnet-4-20250514"

EXTRACT_TOOL = {
    "name": "extract_planning_facts",
    "description": "Extract structured facts from a Lithuanian territorial planning document",
    "input_schema": {
        "type": "object",
        "properties": {
            "allowed_uses": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Allowed land uses (e.g. 'residential', 'commercial', "
                    "'industrial', 'agricultural', 'mixed')"
                ),
            },
            "max_height_m": {
                "type": "number",
                "description": "Maximum building height in meters, null if not specified",
            },
            "max_floors": {
                "type": "integer",
                "description": "Maximum number of floors, null if not specified",
            },
            "max_density": {
                "type": "number",
                "description": "Maximum building density coefficient, null if not specified",
            },
            "parking_requirements": {
                "type": "string",
                "description": "Parking requirements as free text, empty if not specified",
            },
            "extraction_confidence": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "description": (
                    "How confident you are in the extraction (0.0-1.0). "
                    "Lower if text was unclear, scanned, or partially readable"
                ),
            },
            "key_restrictions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Other notable restrictions or conditions mentioned",
            },
        },
        "required": [
            "allowed_uses",
            "extraction_confidence",
        ],
    },
}

SYSTEM_PROMPT = """\
You are an expert at reading Lithuanian territorial planning documents \
(teritorijų planavimo dokumentai). Your job is to extract structured facts \
from the text of these documents.

These documents define what can be built in a specific area: allowed uses, \
height limits, density, parking requirements, and other restrictions.

Key Lithuanian terms:
- Gyvenamoji paskirtis = residential use
- Komercinė paskirtis = commercial use
- Pramoninė paskirtis = industrial use
- Mišri paskirtis = mixed use
- Žemės ūkio paskirtis = agricultural use
- Maksimalus aukštis = maximum height
- Aukštų skaičius = number of floors
- Užstatymo tankis / intensyvumas = building density
- Automobilių stovėjimo vietos = parking spaces

Extract what you can find. If a value is not mentioned, return null for it. \
Set extraction_confidence based on how clear and complete the text was.
"""


def extract_text_from_pdf(pdf_path: str) -> str:
    """Extract text from a PDF file. Tries pymupdf first, falls back to OCR."""
    try:
        import pymupdf

        doc = pymupdf.open(pdf_path)
        text_parts = []
        for page in doc:
            text_parts.append(page.get_text())
        doc.close()
        text = "\n".join(text_parts)

        if len(text.strip()) > 100:
            return text

        logger.info("pymupdf got little text from %s, trying OCR", pdf_path)
    except Exception:
        logger.warning("pymupdf failed for %s", pdf_path)

    try:
        import pymupdf
        import pytesseract
        from PIL import Image

        doc = pymupdf.open(pdf_path)
        ocr_parts = []
        for page_num in range(min(len(doc), 10)):
            page = doc[page_num]
            pix = page.get_pixmap(dpi=200)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            ocr_parts.append(pytesseract.image_to_string(img, lang="lit"))
        doc.close()
        return "\n".join(ocr_parts)
    except Exception:
        logger.warning("OCR failed for %s", pdf_path)
        return ""


def extract_facts_from_text(text: str) -> dict:
    """Send extracted text to Claude and get structured planning facts."""
    api_key = settings.ANTHROPIC_API_KEY
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY is not set")

    truncated = text[:15000]

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=EXTRACT_MODEL,
        max_tokens=1024,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        tools=[EXTRACT_TOOL],
        tool_choice={"type": "tool", "name": "extract_planning_facts"},
        messages=[
            {
                "role": "user",
                "content": (
                    f"Extract structured planning facts from this document text:\n\n{truncated}"
                ),
            }
        ],
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "extract_planning_facts":
            return block.input

    raise ValueError("No extract_planning_facts tool call in response")


def extract_planning_facts(planning_doc) -> dict | None:
    """Full pipeline: extract text from PDFs, then extract facts via LLM.

    Args:
        planning_doc: PlanningDocument instance with related documents

    Returns:
        dict of extracted facts, or None if extraction failed
    """
    all_text = []

    for doc in planning_doc.documents.all():
        if doc.extracted_text:
            all_text.append(doc.extracted_text)
        elif doc.storage_path:
            text = extract_text_from_pdf(doc.storage_path)
            if text:
                doc.extracted_text = text
                doc.save(update_fields=["extracted_text"])
                all_text.append(text)

    combined_text = "\n\n---\n\n".join(all_text)
    if not combined_text.strip():
        logger.warning("No text extracted for planning doc %s", planning_doc.pk)
        return None

    try:
        facts = extract_facts_from_text(combined_text)
    except Exception:
        logger.exception("LLM extraction failed for planning doc %s", planning_doc.pk)
        return None

    planning_doc.allowed_uses = facts.get("allowed_uses", [])
    planning_doc.max_height_m = facts.get("max_height_m")
    planning_doc.max_floors = facts.get("max_floors")
    planning_doc.max_density = facts.get("max_density")
    planning_doc.parking_requirements = facts.get("parking_requirements", "")
    planning_doc.extraction_confidence = facts.get("extraction_confidence")
    planning_doc.save(
        update_fields=[
            "allowed_uses",
            "max_height_m",
            "max_floors",
            "max_density",
            "parking_requirements",
            "extraction_confidence",
        ]
    )

    logger.info(
        "Extracted facts for planning doc %s: confidence=%.0f%%, uses=%s, floors=%s",
        planning_doc.pk,
        (facts.get("extraction_confidence") or 0) * 100,
        facts.get("allowed_uses"),
        facts.get("max_floors"),
    )

    return facts
