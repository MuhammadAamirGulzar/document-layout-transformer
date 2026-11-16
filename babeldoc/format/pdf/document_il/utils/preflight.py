"""Document preflight classification and output PDF QA audit helpers (P1)."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import pymupdf

logger = logging.getLogger(__name__)

_CID_CHAR_RE = re.compile(r"^\(cid:\d+\)$")
_LEAKED_PLACEHOLDER_RE = re.compile(r"(?:\{\d{1,3}\}|<\s*/?\s*b\s*\d+\s*>)")


def _count_page_cid_and_chars(page: Any) -> tuple[int, int]:
    """Return ``(total_chars, broken_tounicode_chars)`` for an IL page."""
    chars = getattr(page, "pdf_character", None) or []
    total = len(chars)
    broken = 0
    for ch in chars:
        uni = getattr(ch, "char_unicode", "") or ""
        if uni == "\ufffd" or _CID_CHAR_RE.match(uni):
            broken += 1
    return total, broken


def _inspect_mupdf_page_widgets(mupdf_page: Any) -> tuple[int, int, int, int]:
    """Return ``(image_count, link_count, acroform_count, signature_count)``."""
    image_count = 0
    link_count = 0
    acroform_count = 0
    signature_count = 0
    try:
        image_count = len(mupdf_page.get_images(full=False))
    except Exception:
        image_count = 0
    try:
        link_count = len(mupdf_page.get_links())
    except Exception:
        link_count = 0
    try:
        widgets = list(mupdf_page.widgets() or [])
        acroform_count = len(widgets)
        sig_type = getattr(pymupdf, "PDF_WIDGET_TYPE_SIGNATURE", None)
        for widget in widgets:
            ftype = getattr(widget, "field_type", None)
            fname = str(getattr(widget, "field_name", "") or "").lower()
            ft_str = str(getattr(widget, "field_type_string", "") or "").lower()
            if (sig_type is not None and ftype == sig_type) or "sig" in ft_str or "signature" in fname:
                signature_count += 1
    except Exception:
        acroform_count = 0
        signature_count = 0
    return image_count, link_count, acroform_count, signature_count


def _classify_page(
    total_chars: int,
    broken_chars: int,
    image_count: int,
) -> tuple[str, float]:
    """Classify one page into born-digital, scanned, partial ToUnicode, or mixed."""
    cid_ratio = (broken_chars / total_chars) if total_chars > 0 else 0.0
    if total_chars > 0 and cid_ratio >= 0.50:
        return "broken_tounicode", cid_ratio
    if total_chars > 0 and cid_ratio >= 0.05:
        return "broken_tounicode_partial", cid_ratio
    if total_chars < 15 and image_count > 0:
        return "scanned_or_image_only", cid_ratio
    if broken_chars > 0 or (total_chars < 40 and image_count > 0):
        return "mixed_or_sparse", cid_ratio
    return "born_digital", cid_ratio


def inspect_document_preflight(
    docs: Any,
    mupdf_doc: Any | None = None,
    ledger: Any | None = None,
) -> dict[str, Any]:
    """Classify every IL page and detect AcroForm/signature/ToUnicode risks."""
    pages = getattr(docs, "page", None) or []
    page_reports: list[dict[str, Any]] = []
    counts = {
        "born_digital": 0,
        "scanned_or_image_only": 0,
        "broken_tounicode": 0,
        "broken_tounicode_partial": 0,
        "mixed_or_sparse": 0,
    }
    total_acroform = 0
    total_signatures = 0
    total_links = 0

    for idx, page in enumerate(pages):
        page_num = getattr(page, "page_number", idx)
        total_chars, broken_chars = _count_page_cid_and_chars(page)
        il_images = len(getattr(page, "pdf_figure", None) or [])
        mp_images, links, acro_cnt, sig_cnt = 0, 0, 0, 0
        if mupdf_doc is not None:
            try:
                if 0 <= page_num < len(mupdf_doc):
                    mp_images, links, acro_cnt, sig_cnt = _inspect_mupdf_page_widgets(
                        mupdf_doc[page_num]
                    )
            except Exception:
                pass
        image_count = max(il_images, mp_images)
        classification, cid_ratio = _classify_page(total_chars, broken_chars, image_count)
        counts[classification] = counts.get(classification, 0) + 1
        total_acroform += acro_cnt
        total_signatures += sig_cnt
        total_links += links
        page_reports.append(
            {
                "page": page_num,
                "classification": classification,
                "char_count": total_chars,
                "broken_tounicode_chars": broken_chars,
                "cid_ratio": round(cid_ratio, 4),
                "image_count": image_count,
                "link_count": links,
                "acroform_field_count": acro_cnt,
                "signature_field_count": sig_cnt,
            }
        )

    warnings: list[str] = []
    broken_pages = counts["broken_tounicode"] + counts["broken_tounicode_partial"]
    if broken_pages > 0:
        warnings.append("broken_tounicode_pages_detected")
    if counts["scanned_or_image_only"] > 0:
        warnings.append("scanned_or_image_only_pages_detected")
    if total_signatures > 0:
        warnings.append("digital_signatures_present")
    if total_acroform > 0:
        warnings.append("acroform_fields_present")

    report = {
        "total_pages": len(pages),
        "born_digital_page_count": counts["born_digital"],
        "scanned_page_count": counts["scanned_or_image_only"],
        "broken_tounicode_page_count": broken_pages,
        "mixed_page_count": counts["mixed_or_sparse"],
        "has_acroform": total_acroform > 0,
        "acroform_field_count": total_acroform,
        "has_digital_signature": total_signatures > 0,
        "signature_field_count": total_signatures,
        "source_link_count": total_links,
        "warnings": warnings,
        "pages": page_reports,
    }
    if ledger is not None and hasattr(ledger, "record_preflight"):
        ledger.record_preflight(report)
    if warnings:
        logger.warning(f"Document preflight warnings: {', '.join(warnings)}")
    return report


def audit_output_pdf(
    output_pdf_path: str | Path | None,
    expected_page_count: int | None = None,
    ledger: Any | None = None,
) -> dict[str, Any]:
    """Run lightweight post-write QA verification on the output PDF."""
    if not output_pdf_path:
        return {"checked": False, "reason": "no_output_path"}
    path = Path(output_pdf_path)
    if not path.exists():
        return {"checked": False, "reason": "output_file_missing", "path": str(path)}

    doc = None
    try:
        doc = pymupdf.open(path)
        actual_pages = len(doc)
        empty_pages: list[int] = []
        leaked_pages: list[int] = []
        replacement_char_pages: list[int] = []
        output_link_count = 0

        for idx in range(actual_pages):
            page = doc[idx]
            try:
                output_link_count += len(page.get_links())
            except Exception:
                pass
            try:
                text = page.get_text("text") or ""
            except Exception:
                text = ""
            try:
                contents = page.read_contents() or b""
            except Exception:
                contents = b""
            if not contents and not text.strip():
                empty_pages.append(idx)
            if _LEAKED_PLACEHOLDER_RE.search(text):
                leaked_pages.append(idx)
            if "\ufffd" in text:
                replacement_char_pages.append(idx)

        page_count_mismatch = (
            expected_page_count is not None and actual_pages < expected_page_count
        )
        qa = {
            "checked": True,
            "path": str(path),
            "actual_page_count": actual_pages,
            "expected_min_page_count": expected_page_count,
            "page_count_mismatch": page_count_mismatch,
            "empty_page_indices": empty_pages,
            "leaked_placeholder_pages": leaked_pages,
            "replacement_char_pages": replacement_char_pages,
            "output_link_count": output_link_count,
        }
        if ledger is not None and hasattr(ledger, "record_output_qa"):
            ledger.record_output_qa(qa)
        return qa
    except Exception as exc:
        logger.debug(f"Output PDF QA check skipped due to error: {exc}")
        return {"checked": False, "reason": str(exc)}
    finally:
        if doc is not None:
            doc.close()
