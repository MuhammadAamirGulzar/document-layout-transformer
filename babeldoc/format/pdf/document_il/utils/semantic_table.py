"""P0-2: Canonical semantic table model, span inference, header linking, and coherence checks."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from babeldoc.format.pdf.document_il import il_version_1

_NUMERIC_CELL_RE = re.compile(
    r"^(?:[$€£¥₹]|US\$|QAR|EUR|USD|SAR|AED)?\s*"
    r"[+\-−(]?\s*\d[\d,.\u066B\u066C\s]*(?:\.\d+)?\s*[)%‰°]?(?:\s*[-–/]\s*\d+(?:\.\d+)?)?"
    r"(?:\s*(?:[$€£¥₹%]|USD|EUR|QAR|ms|s|kg|km|MB|GB|M|B|K))?$",
    re.IGNORECASE,
)
_CODE_CELL_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.\-/]{0,15}$")
_TABLE_CLASS_NAMES = {"table", "wired_table", "wireless_table"}
_TABLE_CELL_MARKERS = (
    "table_cell",
    "table_text",
    "wired_table_cell",
    "wireless_table_cell",
)


@dataclass
class SemanticTableCell:
    """Canonical representation of one reconstructed or clustered table cell."""

    cell_id: str
    table_id: str
    row_index: int
    col_index: int
    row_span: int
    col_span: int
    is_header: bool
    cell_kind: str
    protected_value: bool
    source_text: str
    bbox: tuple[float, float, float, float]
    header_cell_ids: list[str] = field(default_factory=list)
    row_header_cell_id: str | None = None
    paragraph_debug_id: Any = None

    def to_trace_dict(self) -> dict[str, Any]:
        return {
            "table_id": self.table_id,
            "cell_id": self.cell_id,
            "row_index": self.row_index,
            "col_index": self.col_index,
            "row_span": self.row_span,
            "col_span": self.col_span,
            "is_header": self.is_header,
            "cell_kind": self.cell_kind,
            "protected_value": self.protected_value,
            "header_cell_ids": list(self.header_cell_ids),
            "row_header_cell_id": self.row_header_cell_id,
        }


@dataclass
class SemanticTable:
    """Canonical structural model of a table detected or clustered on a page."""

    table_id: str
    page_number: int
    layout_kind: str
    bbox: tuple[float, float, float, float]
    num_rows: int
    num_cols: int
    is_coherent: bool
    confidence: float
    validation_issues: list[str] = field(default_factory=list)
    cells: list[SemanticTableCell] = field(default_factory=list)

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "table_id": self.table_id,
            "page": self.page_number,
            "layout_kind": self.layout_kind,
            "bbox": [round(v, 2) for v in self.bbox],
            "num_rows": self.num_rows,
            "num_cols": self.num_cols,
            "cell_count": len(self.cells),
            "header_cell_count": sum(1 for c in self.cells if c.is_header),
            "protected_numeric_count": sum(1 for c in self.cells if c.protected_value),
            "is_coherent": self.is_coherent,
            "confidence": round(self.confidence, 3),
            "validation_issues": list(self.validation_issues),
        }


def is_table_cell_paragraph(paragraph: il_version_1.PdfParagraph) -> bool:
    label = (paragraph.layout_label or "").lower()
    return any(marker in label for marker in _TABLE_CELL_MARKERS)


def classify_table_cell_value(
    text: str | None,
    *,
    is_header: bool = False,
) -> tuple[str, bool]:
    """Classify a table cell's semantic role and whether it holds a protected literal/numeric value."""
    raw = (text or "").strip()
    if is_header:
        return "header", False
    if not raw:
        return "text", False
    if _NUMERIC_CELL_RE.fullmatch(raw):
        return "numeric", True
    if raw in {"N/A", "n/a", "-", "—", "--", "✓", "✗", "☑", "☐"}:
        return "code", True
    if len(raw) <= 12 and _CODE_CELL_RE.fullmatch(raw) and any(ch.isdigit() for ch in raw):
        return "code", True
    if len(raw) == 1 and raw.isalpha() and raw.isupper():
        return "code", True
    return "text", False


def _overlap_1d(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _box_intersection_ratio(a: il_version_1.Box, b: il_version_1.Box) -> float:
    iw = _overlap_1d(a.x, a.x2, b.x, b.x2)
    ih = _overlap_1d(a.y, a.y2, b.y, b.y2)
    if iw <= 0.0 or ih <= 0.0:
        return 0.0
    area_a = max((a.x2 - a.x) * (a.y2 - a.y), 1e-3)
    area_b = max((b.x2 - b.x) * (b.y2 - b.y), 1e-3)
    return (iw * ih) / min(area_a, area_b)


def _derive_atomic_intervals(
    spans: list[tuple[float, float]],
    *,
    tol: float = 2.0,
) -> list[tuple[float, float]]:
    """Collapse a list of 1D intervals into atomic non-overlapping sorted bands.

    Wide spanning headers (`col_span > 1`) or multi-row labels (`row_span > 1`)
    naturally drop out of atomic partitioning: an interval that strictly covers
    two or more mutually disjoint narrower intervals does not merge them into
    one giant band.
    """
    if not spans:
        return []

    atomic_spans: list[tuple[float, float]] = []
    for idx, (s0, s1) in enumerate(spans):
        if s1 - s0 <= 0.5:
            continue
        contained = [
            (o0, o1)
            for j, (o0, o1) in enumerate(spans)
            if j != idx and o0 >= s0 - tol and o1 <= s1 + tol and (o1 - o0) < (s1 - s0) - tol
        ]
        # Check whether at least two mutually disjoint narrower spans fit inside this span
        has_disjoint_children = False
        if len(contained) >= 2:
            sorted_c = sorted(contained)
            first_end = sorted_c[0][1]
            for c0, _ in sorted_c[1:]:
                if c0 >= first_end - tol:
                    has_disjoint_children = True
                    break
        if not has_disjoint_children:
            atomic_spans.append((s0, s1))

    if not atomic_spans:
        atomic_spans = [(s0, s1) for s0, s1 in spans if s1 - s0 > 0.5]

    groups: list[list[tuple[float, float]]] = []
    for s0, s1 in sorted(atomic_spans):
        placed = False
        for grp in groups:
            g0 = min(x[0] for x in grp)
            g1 = max(x[1] for x in grp)
            ov = _overlap_1d(s0, s1, g0, g1)
            min_len = max(1.0, min(s1 - s0, g1 - g0))
            if ov > 0.35 * min_len:
                grp.append((s0, s1))
                placed = True
                break
        if not placed:
            groups.append([(s0, s1)])

    bands = [
        (min(x[0] for x in grp), max(x[1] for x in grp))
        for grp in groups
    ]
    bands.sort(key=lambda b: b[0])
    return bands


def _covered_band_indices(
    lo: float,
    hi: float,
    bands: list[tuple[float, float]],
    *,
    tol: float = 2.0,
) -> list[int]:
    if not bands:
        return [0]
    covered = []
    for idx, (b0, b1) in enumerate(bands):
        ov = _overlap_1d(lo, hi, b0, b1)
        band_len = max(1.0, b1 - b0)
        span_len = max(1.0, hi - lo)
        if ov > 0.35 * min(band_len, span_len) or (b0 >= lo - tol and b1 <= hi + tol):
            covered.append(idx)
    if covered:
        return covered
    mid = (lo + hi) * 0.5
    best_idx = min(range(len(bands)), key=lambda i: abs((bands[i][0] + bands[i][1]) * 0.5 - mid))
    return [best_idx]


@dataclass
class _VirtualCell:
    """Internal holder unifying real PdfParagraph cells and preserved numeric clusters."""

    box: il_version_1.Box
    text: str
    layout_label: str
    paragraph: il_version_1.PdfParagraph | None
    is_centered: bool = False


def build_semantic_table(
    *,
    table_id: str,
    page_number: int,
    layout_kind: str,
    table_box: il_version_1.Box,
    paragraphs: list[il_version_1.PdfParagraph],
    preserved_numeric_boxes: list[tuple[il_version_1.Box, str]] | None = None,
) -> tuple[SemanticTable, dict[int, SemanticTableCell]]:
    """Construct a SemanticTable and per-paragraph metadata mapping from cell paragraphs."""
    items: list[_VirtualCell] = []
    for p in paragraphs:
        if p.box is None or any(v is None for v in (p.box.x, p.box.y, p.box.x2, p.box.y2)):
            continue
        lbl = (p.layout_label or "").lower()
        items.append(
            _VirtualCell(
                box=p.box,
                text=(p.unicode or "").strip(),
                layout_label=lbl,
                paragraph=p,
                is_centered="centered" in lbl or "header" in lbl,
            )
        )
    for nbox, ntext in preserved_numeric_boxes or []:
        items.append(
            _VirtualCell(
                box=nbox,
                text=ntext.strip(),
                layout_label="preserved_numeric_cell",
                paragraph=None,
            )
        )

    bbox_tuple = (table_box.x, table_box.y, table_box.x2, table_box.y2)
    if not items:
        return (
            SemanticTable(
                table_id=table_id,
                page_number=page_number,
                layout_kind=layout_kind,
                bbox=bbox_tuple,
                num_rows=0,
                num_cols=0,
                is_coherent=False,
                confidence=0.0,
                validation_issues=["empty_table_region"],
                cells=[],
            ),
            {},
        )

    # Row bands sorted TOP-TO-BOTTOM (descending center Y), col bands LEFT-TO-RIGHT.
    row_bands_asc = _derive_atomic_intervals([(it.box.y, it.box.y2) for it in items])
    row_bands = list(reversed(row_bands_asc))
    col_bands = _derive_atomic_intervals([(it.box.x, it.box.x2) for it in items])

    num_rows = len(row_bands)
    num_cols = len(col_bands)

    # Pairwise physical overlap check among paragraphs in the table
    heavy_overlaps = 0
    total_pairs = 0
    for idx_a, a in enumerate(items):
        for b in items[idx_a + 1:]:
            total_pairs += 1
            if _box_intersection_ratio(a.box, b.box) > 0.35:
                heavy_overlaps += 1

    prelim: list[tuple[_VirtualCell, int, int, int, int]] = []
    slot_counts: dict[tuple[int, int], int] = {}
    for item in items:
        r_indices = _covered_band_indices(item.box.y, item.box.y2, row_bands)
        c_indices = _covered_band_indices(item.box.x, item.box.x2, col_bands)
        r0, r1 = min(r_indices), max(r_indices)
        c0, c1 = min(c_indices), max(c_indices)
        r_span = r1 - r0 + 1
        c_span = c1 - c0 + 1
        prelim.append((item, r0, c0, r_span, c_span))
        for rr in range(r0, r1 + 1):
            for cc in range(c0, c1 + 1):
                slot_counts[(rr, cc)] = slot_counts.get((rr, cc), 0) + 1

    slot_collisions = sum(
        1
        for item, r0, c0, r_span, c_span in prelim
        if any(
            slot_counts.get((rr, cc), 0) > 1
            for rr in range(r0, r0 + r_span)
            for cc in range(c0, c0 + c_span)
        )
    )

    issues: list[str] = []
    if heavy_overlaps > 0 and (heavy_overlaps >= 2 or (len(items) >= 3 and heavy_overlaps / max(1, len(items)) >= 0.25)):
        issues.append("overlapping_grid_cells")
    if len(items) >= 3 and slot_collisions > 0 and (slot_collisions / len(items)) > 0.30:
        issues.append("ambiguous_slot_assignment")
    if layout_kind == "irregular" and len(items) >= 2 and num_cols <= 1 and heavy_overlaps > 0:
        issues.append("unreconstructable_grid")

    is_coherent = len(issues) == 0 and num_rows >= 1 and num_cols >= 1
    if layout_kind in {"wired", "grouped", "booktabs"} and not issues:
        base_conf = 0.95
    elif is_coherent and (num_rows >= 2 or num_cols >= 2 or len(items) == 1):
        base_conf = 0.85
    else:
        base_conf = 0.35
    confidence = max(0.05, min(1.0, base_conf - 0.2 * len(issues)))

    # Identify header rows: rows near the top where cells are centered, labeled
    # header, span > 1 col above narrower columns, or contain non-numeric headers
    # followed by rows that introduce numeric data.
    row_cells_map: dict[int, list[tuple[_VirtualCell, int, int, int, int]]] = {}
    for entry in prelim:
        row_cells_map.setdefault(entry[1], []).append(entry)

    first_numeric_row: int | None = None
    for r_idx in range(num_rows):
        entries = row_cells_map.get(r_idx, [])
        if any(classify_table_cell_value(e[0].text)[0] == "numeric" for e in entries):
            first_numeric_row = r_idx
            break

    header_rows: set[int] = set()
    if num_rows >= 2:
        for r_idx in range(num_rows):
            entries = row_cells_map.get(r_idx, [])
            if not entries:
                continue
            has_explicit_header = any(
                "header" in e[0].layout_label or e[0].is_centered
                for e in entries
            )
            has_colspan = any(e[4] > 1 for e in entries)
            all_non_numeric = all(
                classify_table_cell_value(e[0].text)[0] != "numeric"
                for e in entries
            )
            if r_idx == 0 and (has_explicit_header or has_colspan or all_non_numeric):
                header_rows.add(r_idx)
            elif (
                r_idx > 0
                and r_idx == max(header_rows, default=-1) + 1
                and first_numeric_row is not None
                and r_idx < first_numeric_row
                and all_non_numeric
                and (has_explicit_header or any(e[4] > 1 for e in row_cells_map.get(r_idx - 1, [])))
            ):
                header_rows.add(r_idx)

    # Order deterministically top-to-bottom, left-to-right
    prelim.sort(key=lambda e: (e[1], e[2], e[0].box.x))

    semantic_cells: list[SemanticTableCell] = []
    para_cell_map: dict[int, SemanticTableCell] = {}
    used_cell_ids: set[str] = set()

    for item, r0, c0, r_span, c_span in prelim:
        base_id = f"{table_id}:r{r0}c{c0}"
        cell_id = base_id
        suffix = 1
        while cell_id in used_cell_ids:
            suffix += 1
            cell_id = f"{base_id}#{suffix}"
        used_cell_ids.add(cell_id)

        is_header = (r0 in header_rows) or ("header" in item.layout_label)
        cell_kind, protected_val = classify_table_cell_value(item.text, is_header=is_header)
        sem_cell = SemanticTableCell(
            cell_id=cell_id,
            table_id=table_id,
            row_index=r0,
            col_index=c0,
            row_span=r_span,
            col_span=c_span,
            is_header=is_header,
            cell_kind=cell_kind,
            protected_value=protected_val,
            source_text=item.text,
            bbox=(item.box.x, item.box.y, item.box.x2, item.box.y2),
            paragraph_debug_id=getattr(item.paragraph, "debug_id", None) if item.paragraph else None,
        )
        semantic_cells.append(sem_cell)
        if item.paragraph is not None:
            para_cell_map[id(item.paragraph)] = sem_cell

    # Link column headers (all header cells strictly above spanning the cell's columns)
    # and optional row header (column 0 non-numeric label on the same row).
    header_cells = [c for c in semantic_cells if c.is_header]
    row_first_cell: dict[int, SemanticTableCell] = {}
    for c in semantic_cells:
        if c.col_index == 0 and c.row_index not in row_first_cell and not c.is_header and c.cell_kind == "text":
            row_first_cell[c.row_index] = c

    for cell in semantic_cells:
        matched_headers = [
            hdr.cell_id
            for hdr in header_cells
            if hdr.row_index < cell.row_index
            and not (
                hdr.col_index + hdr.col_span <= cell.col_index
                or hdr.col_index >= cell.col_index + cell.col_span
            )
        ]
        cell.header_cell_ids = matched_headers
        if not cell.is_header and cell.col_index > 0:
            row_hdr = row_first_cell.get(cell.row_index)
            if row_hdr is not None and row_hdr.cell_id != cell.cell_id:
                cell.row_header_cell_id = row_hdr.cell_id

    # Attach canonical metadata into each owning PdfParagraph.pipeline_trace
    for item, *_ in prelim:
        if item.paragraph is None:
            continue
        sem_cell = para_cell_map[id(item.paragraph)]
        if item.paragraph.pipeline_trace is None:
            item.paragraph.pipeline_trace = {}
        trace = item.paragraph.pipeline_trace
        trace["semantic_table"] = {
            **sem_cell.to_trace_dict(),
            "layout_kind": layout_kind if is_coherent else "irregular",
            "table_coherent": is_coherent,
            "table_confidence": round(confidence, 3),
            "validation_issues": list(issues),
        }
        trace["table_id"] = sem_cell.table_id
        trace["cell_id"] = sem_cell.cell_id
        trace["row_index"] = sem_cell.row_index
        trace["col_index"] = sem_cell.col_index
        trace["row_span"] = sem_cell.row_span
        trace["col_span"] = sem_cell.col_span
        trace["is_header"] = sem_cell.is_header
        trace["header_cell_ids"] = list(sem_cell.header_cell_ids)
        trace["table_coherent"] = is_coherent
        if not is_coherent:
            trace["preserve_irregular_table"] = True

    table = SemanticTable(
        table_id=table_id,
        page_number=page_number,
        layout_kind=layout_kind if is_coherent else "irregular",
        bbox=bbox_tuple,
        num_rows=num_rows,
        num_cols=num_cols,
        is_coherent=is_coherent,
        confidence=confidence,
        validation_issues=issues,
        cells=semantic_cells,
    )
    return table, para_cell_map


def collect_page_table_regions(
    page: il_version_1.Page,
    paragraphs: list[il_version_1.PdfParagraph] | None = None,
) -> list[tuple[il_version_1.Box, list[il_version_1.PdfParagraph], str]]:
    """Group table cells/paragraphs into distinct table regions on a page."""
    source_paragraphs = list(paragraphs if paragraphs is not None else (page.pdf_paragraph or []))
    groups: list[tuple[il_version_1.Box, list[il_version_1.PdfParagraph], str]] = []
    assigned: set[int] = set()

    for layout in getattr(page, "page_layout", []) or []:
        lbox = getattr(layout, "box", None)
        cname = (getattr(layout, "class_name", "") or "").lower()
        if not lbox or cname not in _TABLE_CLASS_NAMES:
            continue
        members: list[il_version_1.PdfParagraph] = []
        for p in source_paragraphs:
            if not p.box or any(v is None for v in (p.box.x, p.box.y, p.box.x2, p.box.y2)):
                continue
            ov_x = _overlap_1d(p.box.x, p.box.x2, lbox.x, lbox.x2)
            ov_y = _overlap_1d(p.box.y, p.box.y2, lbox.y, lbox.y2)
            area = max((p.box.x2 - p.box.x) * (p.box.y2 - p.box.y), 1.0)
            cx = (p.box.x + p.box.x2) * 0.5
            cy = (p.box.y + p.box.y2) * 0.5
            inside_center = lbox.x - 1.0 <= cx <= lbox.x2 + 1.0 and lbox.y - 1.0 <= cy <= lbox.y2 + 1.0
            if (ov_x * ov_y) / area > 0.5 or inside_center:
                members.append(p)
                assigned.add(id(p))
        if members:
            kind = "wired" if any("wired" in (p.layout_label or "").lower() for p in members) else cname
            groups.append((lbox, members, kind))

    # Fallback: cluster remaining table-labeled paragraphs not enclosed by a table PageLayout
    remaining = [
        p for p in source_paragraphs
        if id(p) not in assigned
        and p.box is not None
        and all(v is not None for v in (p.box.x, p.box.y, p.box.x2, p.box.y2))
        and is_table_cell_paragraph(p)
    ]
    if remaining:
        remaining.sort(key=lambda c: (-c.box.y2, c.box.x))
        clusters: list[tuple[il_version_1.Box, list[il_version_1.PdfParagraph]]] = []
        for cell in remaining:
            cbox = cell.box
            placed = False
            for idx, (gbox, gmembers) in enumerate(clusters):
                v_gap = max(0.0, gbox.y - cbox.y2) if cbox.y2 < gbox.y else max(0.0, cbox.y - gbox.y2)
                min_h = max(1.0, min(gbox.y2 - gbox.y, cbox.y2 - cbox.y))
                h_ov = _overlap_1d(gbox.x, gbox.x2, cbox.x, cbox.x2)
                near_h = h_ov > 0.0 or min(abs(cbox.x - gbox.x2), abs(gbox.x - cbox.x2)) <= 36.0
                if v_gap <= max(18.0, min_h * 1.6) and near_h:
                    gmembers.append(cell)
                    clusters[idx] = (
                        il_version_1.Box(
                            min(gbox.x, cbox.x), min(gbox.y, cbox.y),
                            max(gbox.x2, cbox.x2), max(gbox.y2, cbox.y2),
                        ),
                        gmembers,
                    )
                    placed = True
                    break
            if not placed:
                clusters.append((il_version_1.Box(cbox.x, cbox.y, cbox.x2, cbox.y2), [cell]))
        for gbox, gmembers in clusters:
            kind = "wired" if any("wired" in (p.layout_label or "").lower() for p in gmembers) else "wireless"
            groups.append((gbox, gmembers, kind))

    return groups


def _group_preserved_numeric_boxes(
    page: il_version_1.Page,
    table_box: il_version_1.Box,
    existing_paragraphs: list[il_version_1.PdfParagraph],
) -> list[tuple[il_version_1.Box, str]]:
    """Recover read-only cell boxes for numeric glyphs preserved in ``page.pdf_character``."""
    chars = [
        ch
        for ch in getattr(page, "pdf_character", []) or []
        if ch.box is not None
        and (ch.char_unicode or "").strip()
        and table_box.x - 1.0 <= (ch.box.x + ch.box.x2) * 0.5 <= table_box.x2 + 1.0
        and table_box.y - 1.0 <= (ch.box.y + ch.box.y2) * 0.5 <= table_box.y2 + 1.0
    ]
    if not chars:
        return []

    chars.sort(key=lambda c: (-round(c.box.y2, 1), c.box.x))
    clusters: list[list[il_version_1.PdfCharacter]] = []
    for ch in chars:
        placed = False
        for grp in reversed(clusters):
            last = grp[-1]
            v_ov = _overlap_1d(ch.box.y, ch.box.y2, last.box.y, last.box.y2)
            min_h = max(1.0, min(ch.box.y2 - ch.box.y, last.box.y2 - last.box.y))
            h_gap = ch.box.x - last.box.x2
            if v_ov >= min_h * 0.4 and -2.0 <= h_gap <= max(4.5, min_h * 0.9):
                grp.append(ch)
                placed = True
                break
        if not placed:
            clusters.append([ch])

    result: list[tuple[il_version_1.Box, str]] = []
    para_boxes = [p.box for p in existing_paragraphs if p.box is not None]
    for grp in clusters:
        nbox = il_version_1.Box(
            min(c.box.x for c in grp),
            min(c.box.y for c in grp),
            max(c.box.x2 for c in grp),
            max(c.box.y2 for c in grp),
        )
        if any(_box_intersection_ratio(nbox, pbox) > 0.5 for pbox in para_boxes):
            continue
        text = "".join((c.char_unicode or "") for c in grp).strip()
        if text:
            result.append((nbox, text))
    return result


def annotate_page_semantic_tables(
    page: il_version_1.Page,
    paragraphs: list[il_version_1.PdfParagraph] | None = None,
) -> list[SemanticTable]:
    """Identify tables on ``page``, infer row/col/span/header links, and tag paragraphs."""
    src_paragraphs = list(paragraphs if paragraphs is not None else (page.pdf_paragraph or []))
    regions = collect_page_table_regions(page, src_paragraphs)
    if not regions:
        return []

    page_num = page.page_number if page.page_number is not None else 0
    tables: list[SemanticTable] = []
    for idx, (tbox, members, kind) in enumerate(regions):
        preserved = _group_preserved_numeric_boxes(page, tbox, members)
        table, _ = build_semantic_table(
            table_id=f"p{page_num}_t{idx}",
            page_number=page_num,
            layout_kind=kind,
            table_box=tbox,
            paragraphs=members,
            preserved_numeric_boxes=preserved,
        )
        tables.append(table)
    return tables
