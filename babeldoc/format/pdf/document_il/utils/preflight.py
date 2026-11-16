"""Document preflight classification and output PDF QA audit helpers (P1)."""

from __future__ import annotations

import logging
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pymupdf

from babeldoc.format.pdf.document_il.utils.layout_semantics import paragraph_semantic_zone

logger = logging.getLogger(__name__)

_CID_CHAR_RE = re.compile(r"^\(cid:\d+\)$")
_LEAKED_PLACEHOLDER_RE = re.compile(r"(?:\{\d{1,3}\}|<\s*/?\s*b\s*\d+\s*>)")
_IMMUTABLE_STRUCT_ID_RE = re.compile(
    r"\b(?=[A-Z0-9\-]*\d)[A-Z0-9]{2,}(?:-[A-Z0-9]{1,}){2,}\b|"
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}|"
    r"https?://[^\s<>\"\u201d\u2019)]+"
)
_NUMERIC_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?(?![A-Za-z0-9_])")


def _normalize_digits_for_audit(text: str) -> str:
    trans = str.maketrans("٠١٢٣٤٥٦٧٨٩٪", "0123456789%")
    return (text or "").translate(trans)


def _extract_immutable_audit_tokens(text: str) -> Counter[str]:
    norm = _normalize_digits_for_audit(text)
    tokens: Counter[str] = Counter()
    for m in _IMMUTABLE_STRUCT_ID_RE.finditer(norm):
        tokens[m.group(0).rstrip(".,;:!?")] += 1
    for m in _NUMERIC_TOKEN_RE.finditer(norm):
        val = m.group(0)
        if len(val) >= 2 or "." in val or "%" in val:
            tokens[val] += 1
    return tokens


def _has_degenerate_arabic(text: str) -> bool:
    ar_chars = re.findall(r"[\u0600-\u06FF]", text or "")
    if len(ar_chars) < 20:
        return False
    diac = re.findall(r"[\u064B-\u065F\u0670]", text or "")
    if len(diac) / max(1, len(ar_chars)) > 0.22:
        return True
    words = re.findall(r"[\u0621-\u064AA-Za-z0-9]{2,}", text or "")
    if len(words) >= 9:
        trigrams = Counter(tuple(words[i : i + 3]) for i in range(len(words) - 2))
        if trigrams and trigrams.most_common(1)[0][1] >= 4:
            return True
    return False


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
    *,
    source_pdf_path: str | Path | None = None,
    il_document: Any | None = None,
) -> dict[str, Any]:
    """Run post-write QA verification on the actual saved output PDF."""
    if not output_pdf_path:
        return {"checked": False, "reason": "no_output_path"}
    path = Path(output_pdf_path)
    if not path.exists():
        return {"checked": False, "reason": "output_file_missing", "path": str(path)}

    doc = None
    src_doc = None
    try:
        doc = pymupdf.open(path)
        actual_pages = len(doc)
        empty_pages: list[int] = []
        leaked_pages: list[int] = []
        replacement_char_pages: list[int] = []
        degenerate_pages: list[int] = []
        off_page_spans: list[dict[str, Any]] = []
        overlap_collisions: list[dict[str, Any]] = []
        output_link_count = 0
        out_full_text_parts: list[str] = []

        for idx in range(actual_pages):
            page = doc[idx]
            prect = page.rect
            try:
                output_link_count += len(page.get_links())
            except Exception:
                pass
            try:
                text = page.get_text("text") or ""
            except Exception:
                text = ""
            out_full_text_parts.append(text)
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
            if _has_degenerate_arabic(text):
                degenerate_pages.append(idx)

            # Inspect line-level span geometry in the rendered PDF (disable mediabox clip so off-page escapes surface)
            line_boxes: list[tuple[int, float, float, float, float, str]] = []
            try:
                dict_flags = getattr(pymupdf, "TEXTFLAGS_DICT", 0) & ~getattr(pymupdf, "TEXT_MEDIABOX_CLIP", 0)
                pdict = page.get_text("dict", flags=dict_flags) or {}
            except Exception:
                pdict = {}
            for blk in pdict.get("blocks", []):
                if blk.get("type", 0) != 0:
                    continue
                bno = blk.get("number", -1)
                for line in blk.get("lines", []):
                    spans = line.get("spans", [])
                    line_txt = "".join(s.get("text", "") for s in spans).strip()
                    if not line_txt:
                        continue
                    lx0, ly0, lx1, ly1 = line.get("bbox", (0.0, 0.0, 0.0, 0.0))
                    if (lx1 - lx0) < 1.0 or (ly1 - ly0) < 1.0:
                        continue
                    # Check off-page / clipped spans
                    for sp in spans:
                        stxt = (sp.get("text") or "").strip()
                        if not stxt:
                            continue
                        sx0, sy0, sx1, sy1 = sp.get("bbox", (0.0, 0.0, 0.0, 0.0))
                        if (
                            sx0 < -2.5
                            or sx1 > prect.width + 2.5
                            or sy0 < -2.5
                            or sy1 > prect.height + 2.5
                        ):
                            off_page_spans.append({
                                "page": idx + 1,
                                "region_type": "page_span",
                                "text": stxt[:60],
                                "bbox": [round(sx0, 1), round(sy0, 1), round(sx1, 1), round(sy1, 1)],
                            })
                    line_boxes.append((bno, lx0, ly0, lx1, ly1, line_txt[:40]))

            # Cross-line unexpected overlaps in actual rendered PDF (regardless of MuPDF block clustering)
            for a_i in range(len(line_boxes)):
                _b_a, ax0, ay0, ax1, ay1, ta = line_boxes[a_i]
                area_a = max(1.0, (ax1 - ax0) * (ay1 - ay0))
                ha = max(1.0, ay1 - ay0)
                for b_j in range(a_i + 1, len(line_boxes)):
                    _b_b, bx0, by0, bx1, by1, tb = line_boxes[b_j]
                    h_ov = max(0.0, min(ax1, bx1) - max(ax0, bx0))
                    v_ov = max(0.0, min(ay1, by1) - max(ay0, by0))
                    hb = max(1.0, by1 - by0)
                    if h_ov > 4.0 and v_ov > max(3.0, 0.40 * min(ha, hb)):
                        inter = h_ov * v_ov
                        area_b = max(1.0, (bx1 - bx0) * (by1 - by0))
                        if inter > 0.25 * min(area_a, area_b):
                            overlap_collisions.append({
                                "page": idx + 1,
                                "region_type": "rendered_block_overlap",
                                "text_a": ta,
                                "text_b": tb,
                                "overlap_area": round(inter, 1),
                            })

        region_escapes: list[dict[str, Any]] = []
        table_cell_crossings: list[dict[str, Any]] = []
        if il_document is not None:
            for il_page in getattr(il_document, "page", None) or []:
                pno = getattr(il_page, "page_number", None)
                p_idx = (pno + 1) if isinstance(pno, int) else None
                for para in getattr(il_page, "pdf_paragraph", None) or []:
                    trace = getattr(para, "pipeline_trace", None) or {}
                    zone = paragraph_semantic_zone(para, il_page)
                    did = getattr(para, "debug_id", None)
                    if trace.get("table_cell_overflow") or trace.get("table_cell_collision"):
                        table_cell_crossings.append({
                            "page": p_idx,
                            "region_type": "table",
                            "source_block": did,
                            "detail": "table_cell_overflow" if trace.get("table_cell_overflow") else "table_cell_collision",
                        })
                    if zone in {"code", "figure"} and para.box is not None:
                        comps = getattr(para, "pdf_paragraph_composition", None) or []
                        cxs = []
                        for c in comps:
                            bx = c.pdf_character.box if c.pdf_character and c.pdf_character.box else (c.pdf_formula.box if c.pdf_formula else None)
                            if bx:
                                cxs.append((bx.x, bx.x2))
                        if cxs:
                            gx0 = min(x[0] for x in cxs)
                            gx2 = max(x[1] for x in cxs)
                            if gx0 < para.box.x - 4.0 or gx2 > para.box.x2 + 4.0:
                                region_escapes.append({
                                    "page": p_idx,
                                    "region_type": zone,
                                    "source_block": did,
                                    "detail": f"glyphs=[{gx0:.1f},{gx2:.1f}] outside box=[{para.box.x:.1f},{para.box.x2:.1f}]",
                                })

        missing_immutable_tokens: list[dict[str, Any]] = []
        if source_pdf_path and Path(source_pdf_path).exists():
            src_doc = pymupdf.open(source_pdf_path)
            src_text = "\n".join((p.get_text("text") or "") for p in src_doc)
            out_text = "\n".join(out_full_text_parts)
            src_counts = _extract_immutable_audit_tokens(src_text)
            out_counts = _extract_immutable_audit_tokens(out_text)
            for tok, cnt in src_counts.items():
                if out_counts.get(tok, 0) < cnt:
                    missing_immutable_tokens.append({
                        "token": tok,
                        "expected_count": cnt,
                        "observed_count": out_counts.get(tok, 0),
                    })

        page_count_mismatch = (
            expected_page_count is not None and actual_pages < expected_page_count
        )
        total_qa_issues = (
            len(empty_pages)
            + len(leaked_pages)
            + len(replacement_char_pages)
            + len(degenerate_pages)
            + len(off_page_spans)
            + len(overlap_collisions)
            + len(region_escapes)
            + len(table_cell_crossings)
            + len(missing_immutable_tokens)
            + (1 if page_count_mismatch else 0)
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
            "degenerate_pages": degenerate_pages,
            "off_page_spans": off_page_spans[:50],
            "overlap_collisions": overlap_collisions[:50],
            "region_escapes": region_escapes[:50],
            "table_cell_crossings": table_cell_crossings[:50],
            "missing_immutable_tokens": missing_immutable_tokens[:50],
            "total_qa_issues": total_qa_issues,
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
        if src_doc is not None:
            src_doc.close()
