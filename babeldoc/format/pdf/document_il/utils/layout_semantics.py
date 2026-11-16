"""P0-3: Reading order, cross-region merge guards, code/algorithm preservation, and figure token semantics."""
from __future__ import annotations

import re
from typing import Any

from babeldoc.format.pdf.document_il import il_version_1

_ALGORITHM_CAPTION_RE = re.compile(
    r"^\s*(?:algorithm|listing|program|procedure)\s+\d+(?:\.\d+)?(?:\s*[:.\-–—]|\s|$)",
    re.IGNORECASE,
)

_NUMBERED_INSTRUCTION_LINE_RE = re.compile(
    r"^\s*\d{1,3}\s*[:.)]\s*[A-Za-z][a-z]+(?:\s+[A-Za-z][a-z]+){1,}"
)

_CODE_DEF_OR_BLOCK_RE = re.compile(
    r"^\s*(?:"
    r"(?:def|async\s+def|class|function|procedure|fn|sub)\s+[A-Za-z_]\w*\s*(?:\(|:|\{)|"
    r"import\s+[A-Za-z_][\w.]*|from\s+[A-Za-z_][\w.]*\s+import\s+[A-Za-z_*]|"
    r"(?:public|private|protected|static|const|let|var)\s+[A-Za-z_]\w*\s*(?:=|:|\()|"
    r"(?:SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM|CREATE\s+TABLE)\b|"
    r"(?:return|yield|raise|throw)\b(?:\s+[\w\(\[\{\'\"\-]|\s*;?\s*$)|"
    r"#include\s*[<\"])"
)
_SQL_CLAUSE_RE = re.compile(
    r"\b(?:SELECT\s+.+?\s+FROM\s+[A-Za-z_][\w.]*|FROM\s+[A-Za-z_][\w.]*|WHERE\s+[A-Za-z_][\w.]*\s*(?:=|!=|<>|<|>|\bIN\b|\bLIKE\b|\bIS\b)|"
    r"(?:INNER|LEFT|RIGHT)?\s*JOIN\s+[A-Za-z_][\w.]*\s+ON\b|GROUP\s+BY\s+[A-Za-z_]|ORDER\s+BY\s+[A-Za-z_]|"
    r"INSERT\s+INTO\s+[A-Za-z_]|CREATE\s+TABLE\s+[A-Za-z_]|ALTER\s+TABLE\s+[A-Za-z_])\b",
    re.IGNORECASE,
)
_JSON_KEY_RE = re.compile(r'^\s*"[^"\n]{1,64}"\s*:\s*\S?')
_YAML_KV_RE = re.compile(
    r"^\s*(?:-\s+)?(?!(?:Name|Role|Phone|Email|Address|Website|Note|Title|Date|Invoice|Subtotal|Total|Warning|Summary)\b)"
    r"[a-z_][a-z0-9_.\-]{1,40}\s*:(?:\s+(?:true|false|null|\d[\d.]*|\"[^\"]*\"|\'[^\']*\'|\[[^\]]*\]|\S+[/_.\-]\S*)|\s*$)"
)
_HTML_TAG_RE = re.compile(
    r"(?:<!DOCTYPE\s+\w+|</[a-zA-Z][a-zA-Z0-9\-]{0,20}\s*>|<(?!(?:b|B)\d+\s*>)[a-zA-Z][a-zA-Z0-9\-]{1,20}(?:\s+[a-zA-Z_:][a-zA-Z0-9_.:-]*\s*=\s*(?:\"[^\"]*\"|\'[^\']*\')|\s*/)*\s*>)"
)
_DIFF_HEADER_RE = re.compile(
    r"^\s*(?:---\s+(?:a/|/dev/null|\S+\.\w+)|\+\+\+\s+(?:b/|/dev/null|\S+\.\w+)|@@\s+-\d+(?:,\d+)?\s+\+\d+(?:,\d+)?\s+@@|diff\s+--git\s+)"
)
_SHELL_LINE_RE = re.compile(
    r"^\s*(?:\$\s+(?:[A-Za-z_][\w.\-/]*(?:\s|$))|(?:export\s+[A-Za-z_][A-Za-z0-9_]*=|(?:curl|wget|git|pytest|python3?|pip|docker|kubectl|grep|awk|sed)\s+-\S*))"
)
_REGEX_LITERAL_RE = re.compile(r"^\s*/(?:[^/\n\\]|\\.)+/[gimsuvy]*\s*$")

_CODE_CONTROL_RE = re.compile(
    r"^\s*(?:\d+\s*[:.)]?\s*)?(?:"
    r"(?:function|procedure|def)\s+\w+\s*\(|"
    r"(?:for|while)\s*\([^)]*\)|"
    r"(?:for|while)\s+(?:each\s+)?[A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)?\s+(?:←|:=|=|\bin\b)\s*\S.*?\b(?:do|to)\b|"
    r"(?:for|while)\b.+\bdo\s*(?::|\{|$)|"
    r"if\b.+\bthen\b|"
    r"if\s*\([^)]+\)\s*(?:\{|:)|"
    r"else\s*(?:if\b.*?(?:\{|:|\bthen\b)|\{|:|$)|"
    r"elif\b.+:|"
    r"repeat\s*(?::|\{|$)|until\s*\(|return\b(?:\s+[\w\(\[\{\'\"\-]|\s*;?\s*$)|"
    r"end\s+(?:if|for|while|function|procedure)\b)",
    re.IGNORECASE,
)

_CODE_OP_RE = re.compile(
    r"(?:←|:=|=>|->|::|\+=|-=|\*=|==|!=|<=|>=|&&|\|\||(?:[()={}]|\breturn\b).*;\s*$|[\{\}]\s*$)"
)
_CODE_ASSIGN_RE = re.compile(r"^\s*[A-Za-z_]\w*(?:\[[^\]]+\]|\.[A-Za-z_]\w*)*\s*(?:←|:=|=)\s*\S")
_FUNCTION_CALL_OR_SNAKE_RE = re.compile(r"\b[a-z_][a-z0-9_]*_[a-z0-9_]+\b|\b[A-Za-z_]\w*\([^)\n]*\)")

_FIGURE_NUMERIC_OR_VAR_RE = re.compile(
    r"^(?:"
    r"[+\-−(]?\s*\d[\d,.\u066B\u066C]*(?:\.\d+)?(?:e[+\-]?\d+)?\s*[)%‰°]?"
    r"(?:\s*[-–/]\s*\d+(?:\.\d+)?)?"
    r"(?:\s*(?:ms|s|Hz|kHz|MHz|GHz|nm|mm|cm|m|km|KB|MB|GB|TB|%|px|pt))?|"
    r"[A-Za-z](?:_[A-Za-z0-9]+)?\s*[=<>≤≥≈]\s*[+\-]?\d+(?:\.\d+)?(?:e[+\-]?\d+)?|"
    r"\[(?:MASK|CLS|SEP|PAD|UNK|[A-Z]{2,8})\]"
    r")$",
    re.IGNORECASE,
)
_POS_OR_CODE_TOKEN_RE = re.compile(r"^(?:[A-Z]{1,6}(?:\$[A-Z0-9]+)?|<START>|<END>|<PAD>)$")


def _paragraph_box_valid(paragraph: il_version_1.PdfParagraph) -> bool:
    box = getattr(paragraph, "box", None)
    return box is not None and all(
        v is not None for v in (box.x, box.y, box.x2, box.y2)
    )


def _overlap_1d(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _box_center_inside(inner: il_version_1.Box, outer: il_version_1.Box, tol: float = 2.0) -> bool:
    cx = (inner.x + inner.x2) * 0.5
    cy = (inner.y + inner.y2) * 0.5
    return outer.x - tol <= cx <= outer.x2 + tol and outer.y - tol <= cy <= outer.y2 + tol


def is_algorithm_caption_text(text: str | None) -> bool:
    """Return True for captions such as ``Algorithm 1: Training``."""
    raw = " ".join((text or "").split())
    return bool(raw and _ALGORITHM_CAPTION_RE.match(raw))


def is_standalone_code_text(text: str | None, layout_label: str = "") -> bool:
    """Detect code, SQL, config, or unnumbered pseudocode safely without flagging prose."""
    raw = (text or "").strip()
    if not raw or is_algorithm_caption_text(raw):
        return False

    lbl = (layout_label or "").lower()
    if any(k in lbl for k in ("algorithm", "pseudocode", "code")) and "caption" not in lbl:
        return True

    lines = [ln for ln in raw.splitlines() if ln.strip()]
    if not lines:
        return False

    # Reject ordinary numbered natural-language instructions ("1: Open the document").
    if len(lines) >= 2 and all(_NUMBERED_INSTRUCTION_LINE_RE.match(ln) for ln in lines) and not _CODE_OP_RE.search(raw):
        return False

    def_hits = sum(1 for ln in lines if _CODE_DEF_OR_BLOCK_RE.match(ln))
    ctrl_hits = sum(1 for ln in lines if _CODE_CONTROL_RE.match(ln))
    op_hits = sum(1 for ln in lines if _CODE_OP_RE.search(ln))
    assign_hits = sum(1 for ln in lines if _CODE_ASSIGN_RE.match(ln))
    fn_hits = len(_FUNCTION_CALL_OR_SNAKE_RE.findall(raw))
    sql_hits = len(_SQL_CLAUSE_RE.findall(raw))
    json_hits = sum(1 for ln in lines if _JSON_KEY_RE.match(ln) or ln.strip() in {"{", "}", "},", "[", "]", "],"})
    yaml_hits = sum(1 for ln in lines if _YAML_KV_RE.match(ln))
    html_hits = len(_HTML_TAG_RE.findall(raw))
    diff_hits = sum(1 for ln in lines if _DIFF_HEADER_RE.match(ln))
    shell_hits = sum(1 for ln in lines if _SHELL_LINE_RE.match(ln))
    regex_hits = sum(1 for ln in lines if _REGEX_LITERAL_RE.match(ln))

    if len(lines) == 1:
        ln = lines[0]
        if _CODE_DEF_OR_BLOCK_RE.match(ln) and (
            "(" in ln or ":" in ln or "{" in ln or "SELECT" in ln.upper()
        ):
            return True
        if sql_hits >= 1 and (";" in ln or re.search(r"\b(?:WHERE|JOIN|GROUP\s+BY|ORDER\s+BY)\b", ln, re.I)):
            return True
        if regex_hits >= 1 or shell_hits >= 1 or diff_hits >= 1:
            return True
        if html_hits >= 2 and re.search(r"</[a-zA-Z][a-zA-Z0-9\-]*>", ln):
            return True
        if json_hits >= 1 and ln.strip().startswith("{") and ln.strip().endswith("}"):
            return True
        if assign_hits >= 1 and _CODE_OP_RE.search(ln) and not ln.endswith("."):
            return True
        return False

    if sql_hits >= 2:
        return True
    if json_hits >= max(2, int(len(lines) * 0.6)) and ("{" in raw or "}" in raw or re.search(r'^\s*"[^"\n]+"\s*:', raw, re.M)):
        return True
    if yaml_hits >= max(2, int(len(lines) * 0.6)) and not raw.endswith("."):
        return True
    if html_hits >= 2 and re.search(r"</[a-zA-Z][a-zA-Z0-9\-]*>", raw):
        return True
    if diff_hits >= 1 and (len(lines) >= 2 or any(ln.lstrip().startswith(("+", "-", "@@")) for ln in lines)):
        return True
    if shell_hits >= 1 and (shell_hits >= 2 or any("--" in ln or "|" in ln or "\\" in ln for ln in lines)):
        return True
    if regex_hits >= 1:
        return True

    code_lines = sum(
        1
        for ln in lines
        if _CODE_DEF_OR_BLOCK_RE.match(ln)
        or _CODE_CONTROL_RE.match(ln)
        or _CODE_OP_RE.search(ln)
        or _CODE_ASSIGN_RE.match(ln)
    )
    if def_hits >= 1 and (ctrl_hits + op_hits + assign_hits + sql_hits >= 1 or len(lines) <= 4):
        return True
    if code_lines >= 2 and (op_hits >= 1 or assign_hits >= 1 or ctrl_hits >= 2) and fn_hits >= 1:
        return True
    if code_lines >= max(3, int(len(lines) * 0.6)):
        return True
    return False


def classify_pseudocode_region_paragraphs(
    region_class: str,
    region_box: il_version_1.Box,
    members: list[il_version_1.PdfParagraph],
    get_line_text,
) -> tuple[bool, list[il_version_1.PdfParagraph], list[il_version_1.PdfParagraph]]:
    """Decide whether a layout region holds code/pseudocode and separate any caption paragraphs.

    Returns ``(is_code_region, body_members_to_preserve, caption_members_to_translate)``.
    """
    if not members:
        return False, [], []

    cname = (region_class or "").lower()
    explicit_code_region = cname in {"algorithm", "pseudocode", "code"}

    captions: list[il_version_1.PdfParagraph] = []
    body_candidates: list[il_version_1.PdfParagraph] = []
    for para in members:
        ptext = " ".join((para.unicode or "").split())
        if not ptext:
            lines_txt = []
            for comp in para.pdf_paragraph_composition or []:
                if comp.pdf_line:
                    lines_txt.append(get_line_text(comp.pdf_line.pdf_character))
            ptext = " ".join(" ".join(lines_txt).split())
        if is_algorithm_caption_text(ptext):
            captions.append(para)
        else:
            body_candidates.append(para)

    if not body_candidates:
        return False, [], []

    number_prefix = re.compile(r"^(\d+)(?:\s*[:.]\s*(?!\d)|\s+(?=\S)|\s*$)")
    line_numbers: set[int] = set()
    control_rows: set[int] = set()
    assignment_rows: set[int] = set()
    def_rows: set[int] = set()
    total_lines = 0
    gutter_right = region_box.x + (region_box.x2 - region_box.x) * 0.12

    for para in body_candidates:
        for comp in para.pdf_paragraph_composition or []:
            line = comp.pdf_line
            if not line or not line.box:
                continue
            total_lines += 1
            text = (get_line_text(line.pdf_character) or "").strip()
            marker = number_prefix.match(text) if line.box.x <= gutter_right else None
            if marker:
                line_numbers.add(int(marker[1]))
                text = text[marker.end():].strip()
            if _CODE_DEF_OR_BLOCK_RE.match(text):
                def_rows.add(round(line.box.y))
            if _CODE_CONTROL_RE.match(text):
                control_rows.add(round(line.box.y))
            if _CODE_ASSIGN_RE.match(text) or _CODE_OP_RE.search(text):
                assignment_rows.add(round(line.box.y))

    has_sequence = any(n + 1 in line_numbers and n + 2 in line_numbers for n in line_numbers)
    classic_numbered = (
        has_sequence
        and len(control_rows) >= 2
        and bool(assignment_rows or len(control_rows) >= 3)
    )
    unnumbered_code = (
        len(body_candidates) >= 1
        and total_lines >= 2
        and (
            (len(def_rows) >= 1 and (len(control_rows) + len(assignment_rows)) >= 1)
            or (len(control_rows) >= 2 and len(assignment_rows) >= 1)
            or (len(assignment_rows) >= 3 and len(control_rows) >= 1)
        )
    )
    is_table_container = cname in {"table", "wired_table", "wireless_table"}
    if is_table_container and not captions:
        # A genuine table layout without an Algorithm/Listing caption must not be
        # mistaken for unnumbered code unless it has both sequential line numbers
        # and explicit code definitions/assignments.
        if classic_numbered and (len(def_rows) >= 1 or len(assignment_rows) >= 2):
            return True, body_candidates, captions
        return False, [], []
    if explicit_code_region and (classic_numbered or unnumbered_code or (len(def_rows) + len(assignment_rows) + len(control_rows)) >= 2):
        return True, body_candidates, captions
    if classic_numbered or unnumbered_code:
        return True, body_candidates, captions
    return False, [], []


def paragraph_semantic_zone(
    paragraph: il_version_1.PdfParagraph,
    page: il_version_1.Page | None = None,
) -> str:
    """Classify a paragraph into its high-level layout/reading-order zone."""
    label = (paragraph.layout_label or "").lower()
    if "algorithm_caption" in label or "figure_caption" in label or "table_caption" in label or "caption" in label:
        return "caption"
    if "table_footnote" in label or "footnote" in label:
        return "footnote"
    if any(k in label for k in ("table_cell", "table_text", "wired_table", "wireless_table")):
        return "table"
    if any(k in label for k in ("page_header", "running_header")):
        return "header"
    if any(k in label for k in ("page_footer", "running_footer", "page_number")) or label == "footer":
        return "footer"
    if label == "header":
        return "header"
    if any(k in label for k in ("algorithm", "pseudocode", "code")):
        return "code"
    if any(k in label for k in ("reference", "bibliography", "ref_list", "works_cited")):
        return "bibliography"
    if any(k in label for k in ("figure_title", "chart_title")):
        return "heading"
    if any(k in label for k in ("figure_text", "axis_label", "axis_text", "chart_legend")):
        return "figure"
    if any(k in label for k in ("list", "bullet")):
        return "list"
    if any(k in label for k in ("title", "heading", "section")):
        return "heading"

    trace = paragraph.pipeline_trace or {}
    if trace.get("preserved_code"):
        return "code"
    if trace.get("in_figure_region"):
        return "figure"

    if page is not None and _paragraph_box_valid(paragraph):
        for layout in getattr(page, "page_layout", []) or []:
            cname = (getattr(layout, "class_name", "") or "").lower()
            lbox = getattr(layout, "box", None)
            if not lbox or not _box_center_inside(paragraph.box, lbox):
                continue
            if cname in {"table", "wired_table", "wireless_table"}:
                return "table"
            if cname in {"figure", "chart", "graph", "diagram", "image"}:
                return "figure"
            if cname in {"algorithm", "pseudocode", "code"}:
                return "code"
            if cname in {"reference", "references", "bibliography"}:
                return "bibliography"
            if "footnote" in cname:
                return "footnote"
            if "caption" in cname:
                return "caption"
    return "body"


def _enclosing_region_id(
    box: il_version_1.Box,
    page: il_version_1.Page | None,
) -> int | None:
    if page is None:
        return None
    for idx, layout in enumerate(getattr(page, "page_layout", []) or []):
        cname = (getattr(layout, "class_name", "") or "").lower()
        lbox = getattr(layout, "box", None)
        if not lbox:
            continue
        if cname in {
            "figure", "chart", "graph", "diagram", "image",
            "table", "wired_table", "wireless_table",
            "algorithm", "pseudocode", "code", "footnote",
        } and _box_center_inside(box, lbox):
            return idx
    return None


def can_merge_paragraphs(
    first: il_version_1.PdfParagraph,
    second: il_version_1.PdfParagraph,
    page: il_version_1.Page | None = None,
) -> bool:
    """Return False when merging would cross incompatible structural or column boundaries."""
    if not _paragraph_box_valid(first) or not _paragraph_box_valid(second):
        return False

    zone_a = paragraph_semantic_zone(first, page)
    zone_b = paragraph_semantic_zone(second, page)

    isolated_zones = {"caption", "footnote", "table", "code", "figure", "header", "footer", "bibliography"}
    if (zone_a in isolated_zones or zone_b in isolated_zones) and zone_a != zone_b:
        return False
    second_txt = (getattr(second, "unicode", None) or "").lstrip()
    if second_txt:
        # Never merge consecutive bibliography / numbered citation entries.
        if re.match(r"^\[\d{1,4}\]\s+\S", second_txt):
            return False
        if (zone_a == "bibliography" or zone_b == "bibliography") and re.match(
            r"^(?:\d{1,4}\.\s+[A-Z]|[A-Z][A-Za-z'\u2019\-]+,\s+[A-Z]\.)",
            second_txt,
        ):
            return False
    if zone_a == "figure" or zone_b == "figure" or zone_a == "table" or zone_b == "table":
        return False
    if zone_a == "caption" and zone_b == "caption":
        # Two captions separated vertically might be caption for Figure 1 and Figure 2.
        reg_a = _enclosing_region_id(first.box, page)
        reg_b = _enclosing_region_id(second.box, page)
        if reg_a != reg_b:
            return False

    if page is not None:
        reg_a = _enclosing_region_id(first.box, page)
        reg_b = _enclosing_region_id(second.box, page)
        if reg_a != reg_b:
            return False

    # Reject merges across distinct side-by-side columns separated by a gutter.
    b1, b2 = first.box, second.box
    h_ov = _overlap_1d(b1.x, b1.x2, b2.x, b2.x2)
    h_gap = max(b1.x - b2.x2, b2.x - b1.x2, 0.0)
    if h_ov <= 0.0 and h_gap > 24.0:
        # On the exact same baseline, small gaps can occur between sentence fragments;
        # beyond 24pt is a separate column or tab-separated field.
        v_ov = _overlap_1d(b1.y, b1.y2, b2.y, b2.y2)
        min_h = max(1.0, min(b1.y2 - b1.y, b2.y2 - b2.y))
        if v_ov >= min_h * 0.4 or h_gap > 45.0:
            return False

    return True


def annotate_page_figure_regions(
    page: il_version_1.Page,
    paragraphs: list[il_version_1.PdfParagraph] | None = None,
) -> list[dict[str, Any]]:
    """Tag figure/chart interior paragraphs and identify protected scientific tokens/data."""
    src_paragraphs = list(paragraphs if paragraphs is not None else (page.pdf_paragraph or []))
    if not src_paragraphs:
        return []

    figure_layouts: list[tuple[int, il_version_1.Box]] = []
    for idx, layout in enumerate(getattr(page, "page_layout", []) or []):
        cname = (getattr(layout, "class_name", "") or "").lower()
        lbox = getattr(layout, "box", None)
        if lbox and cname in {"figure", "chart", "graph", "diagram", "image"}:
            figure_layouts.append((idx, lbox))

    # Group paragraphs per figure region (or explicit figure_text label).
    region_members: dict[int, list[il_version_1.PdfParagraph]] = {}
    for p in src_paragraphs:
        if not _paragraph_box_valid(p):
            continue
        lbl = (p.layout_label or "").lower()
        if any(k in lbl for k in ("caption", "title")):
            continue
        matched_idx: int | None = None
        for ridx, fbox in figure_layouts:
            if _box_center_inside(p.box, fbox):
                matched_idx = ridx
                break
        if matched_idx is None and any(k in lbl for k in ("figure_text", "axis_label", "axis_text", "chart_legend")):
            matched_idx = -1
        if matched_idx is not None:
            if p.pipeline_trace is None:
                p.pipeline_trace = {}
            p.pipeline_trace["in_figure_region"] = True
            p.pipeline_trace["figure_region_index"] = matched_idx
            region_members.setdefault(matched_idx, []).append(p)

    preserved_records: list[dict[str, Any]] = []
    for ridx, members in region_members.items():
        # Detect clustered word-level diagram / syntax / activation examples:
        # >= 3 short isolated 1-2 word tokens inside the same figure region.
        short_word_tokens = [
            p
            for p in members
            if 1 <= len((p.unicode or "").split()) <= 2
            and not (p.unicode or "").strip().endswith((".", "!", "?"))
        ]
        word_cluster_diagram = len(short_word_tokens) >= 3

        for p in members:
            raw = " ".join((p.unicode or "").split())
            if not raw:
                continue
            preserve = should_preserve_figure_paragraph(
                p,
                word_cluster_diagram=word_cluster_diagram,
            )
            if preserve:
                if p.pipeline_trace is None:
                    p.pipeline_trace = {}
                p.pipeline_trace["preserved_figure_data"] = True
                preserved_records.append(
                    {
                        "page": page.page_number,
                        "kind": "figure_data_or_token",
                        "region_id": ridx,
                        "debug_id": getattr(p, "debug_id", None),
                        "text_preview": raw[:80],
                    }
                )
    return preserved_records


def should_preserve_figure_paragraph(
    paragraph: il_version_1.PdfParagraph,
    *,
    word_cluster_diagram: bool = False,
) -> bool:
    """Return True if a figure/diagram item holds immutable data, ticks, or word-example tokens."""
    lbl = (paragraph.layout_label or "").lower()
    if any(k in lbl for k in ("caption", "title")):
        return False

    trace = paragraph.pipeline_trace or {}
    if trace.get("preserved_figure_data"):
        return True

    in_figure = bool(trace.get("in_figure_region")) or any(
        k in lbl for k in ("figure_text", "axis_label", "axis_text", "chart_legend")
    )
    if not in_figure:
        return False

    raw = " ".join((paragraph.unicode or "").split())
    if not raw:
        return False

    if _FIGURE_NUMERIC_OR_VAR_RE.fullmatch(raw) or _POS_OR_CODE_TOKEN_RE.fullmatch(raw):
        return True

    # Single-token word-level scientific example inside a clustered diagram (>=3 short tokens).
    words = raw.split()
    if word_cluster_diagram and len(words) == 1 and re.fullmatch(r"[A-Za-z][A-Za-z'\-]{0,20}", raw):
        return True

    # Short axis coordinate / series variable notation ("x=0.5", "k=3", "t_0")
    if len(words) <= 2 and re.search(r"[=<>_^{}\[\]]", raw) and len(raw) <= 24:
        return True

    return False


def _order_body_band_columns(band: list[il_version_1.PdfParagraph]) -> list[tuple[int, il_version_1.PdfParagraph]]:
    """Order paragraphs in a vertical band across 1..N X-separated columns recursively."""
    if not band:
        return []
    if len(band) == 1:
        return [(0, band[0])]

    # Sort horizontally by left edge to inspect contiguous X intervals for column gutters.
    by_x = sorted(band, key=lambda p: (p.box.x, -p.box.y2))
    intervals: list[tuple[float, float, list[il_version_1.PdfParagraph]]] = []
    for p in by_x:
        if not intervals:
            intervals.append((p.box.x, p.box.x2, [p]))
            continue
        g_x0, g_x1, g_list = intervals[-1]
        # Treat horizontal separation >= 12pt as a potential column gutter, EXCEPT
        # if both items share a very narrow same-line row where indentation or inline fragments occur
        # without stacked multi-line column neighbors.
        if p.box.x - g_x1 >= 12.0:
            intervals.append((p.box.x, p.box.x2, [p]))
        else:
            intervals[-1] = (min(g_x0, p.box.x), max(g_x1, p.box.x2), g_list + [p])

    if len(intervals) == 1:
        ordered = sorted(
            band,
            key=lambda p: (-round(p.box.y2, 1), round(p.box.x, 1)),
        )
        return [(0, p) for p in ordered]

    # Verify intervals actually represent stacked columns (not merely two short tokens on one line).
    max_items_in_any_col = max(len(g[2]) for g in intervals)
    any_multi_vertical = any(
        len({round(p.box.y2, 0) for p in g[2]}) >= 2 for g in intervals
    )
    if max_items_in_any_col <= 1 and not any_multi_vertical:
        ordered = sorted(
            band,
            key=lambda p: (-round(p.box.y2, 1), round(p.box.x, 1)),
        )
        return [(0, p) for p in ordered]

    result: list[tuple[int, il_version_1.PdfParagraph]] = []
    for col_idx, (_, _, col_items) in enumerate(intervals):
        col_sorted = sorted(
            col_items,
            key=lambda p: (-round(p.box.y2, 1), round(p.box.x, 1)),
        )
        for item in col_sorted:
            result.append((col_idx, item))
    return result


def compute_page_reading_order(
    page: il_version_1.Page,
    paragraphs: list[il_version_1.PdfParagraph] | None = None,
) -> tuple[list[il_version_1.PdfParagraph], dict[str, Any]]:
    """Compute column-aware human reading order and record provenance in ``pipeline_trace``."""
    src = list(paragraphs if paragraphs is not None else (page.pdf_paragraph or []))
    if not src:
        return [], {"total_paragraphs": 0, "columns_detected": 1}

    valid = [p for p in src if _paragraph_box_valid(p)]
    invalid = [p for p in src if not _paragraph_box_valid(p)]
    if not valid:
        return src, {"total_paragraphs": len(src), "columns_detected": 1}

    headers: list[il_version_1.PdfParagraph] = []
    body: list[il_version_1.PdfParagraph] = []
    footnotes: list[il_version_1.PdfParagraph] = []
    footers: list[il_version_1.PdfParagraph] = []

    for p in valid:
        zone = paragraph_semantic_zone(p, page)
        if zone == "header":
            headers.append(p)
        elif zone == "footnote":
            footnotes.append(p)
        elif zone == "footer":
            footers.append(p)
        else:
            body.append(p)

    # Determine effective content X span to recognize full-width spanning elements
    # (titles, abstracts, wide figures/tables) that cut the page into horizontal bands.
    crop = page.cropbox.box if page.cropbox and page.cropbox.box else None
    all_x0 = min(p.box.x for p in valid)
    all_x1 = max(p.box.x2 for p in valid)
    content_width = max(
        crop.x2 - crop.x if crop else 0.0,
        all_x1 - all_x0,
        1.0,
    )

    body_ordered: list[tuple[int, int, il_version_1.PdfParagraph]] = []
    max_cols = 1
    if body:
        # Sort top-to-bottom by top edge (descending y2).
        body_desc = sorted(body, key=lambda p: (-p.box.y2, p.box.x))
        # Partition into vertical Y-bands: a block spanning >= 58% of content width
        # starts/separates its own full-width band if vertically disjoint from column clusters.
        y_bands: list[list[il_version_1.PdfParagraph]] = []
        current_band: list[il_version_1.PdfParagraph] = []
        current_is_spanning = False
        band_min_y = 0.0

        for p in body_desc:
            p_w = p.box.x2 - p.box.x
            is_spanning = (p_w / content_width) >= 0.58 and len(body_desc) >= 3
            if not current_band:
                current_band = [p]
                current_is_spanning = is_spanning
                band_min_y = p.box.y
                continue

            # Cut boundary when switching between spanning header/banner/figure and columns
            # without vertical overlap.
            if (is_spanning != current_is_spanning) and p.box.y2 <= band_min_y + 1.0:
                y_bands.append(current_band)
                current_band = [p]
                current_is_spanning = is_spanning
                band_min_y = p.box.y
            elif current_is_spanning and is_spanning and p.box.y2 <= band_min_y + 1.0:
                current_band.append(p)
                band_min_y = min(band_min_y, p.box.y)
            else:
                current_band.append(p)
                band_min_y = min(band_min_y, p.box.y)
        if current_band:
            y_bands.append(current_band)

        for band_idx, band in enumerate(y_bands):
            ordered_cols = _order_body_band_columns(band)
            for col_idx, p in ordered_cols:
                max_cols = max(max_cols, col_idx + 1)
                body_ordered.append((band_idx, col_idx, p))

    ordered_entries: list[tuple[str, int, int, il_version_1.PdfParagraph]] = []
    for p in sorted(headers, key=lambda x: (-round(x.box.y2, 1), round(x.box.x, 1))):
        ordered_entries.append(("header", 0, 0, p))
    for band_idx, col_idx, p in body_ordered:
        ordered_entries.append((paragraph_semantic_zone(p, page), band_idx, col_idx, p))
    for p in sorted(footnotes, key=lambda x: (-round(x.box.y2, 1), round(x.box.x, 1))):
        ordered_entries.append(("footnote", len(body_ordered), 0, p))
    for p in sorted(footers, key=lambda x: (-round(x.box.y2, 1), round(x.box.x, 1))):
        ordered_entries.append(("footer", len(body_ordered) + 1, 0, p))
    for p in invalid:
        ordered_entries.append((paragraph_semantic_zone(p, page), len(body_ordered) + 2, 0, p))

    final_paragraphs: list[il_version_1.PdfParagraph] = []
    for idx, (zone, band_idx, col_idx, p) in enumerate(ordered_entries):
        if p.pipeline_trace is None:
            p.pipeline_trace = {}
        p.pipeline_trace["reading_order"] = idx
        p.pipeline_trace["reading_band"] = band_idx
        p.pipeline_trace["column_index"] = col_idx
        p.pipeline_trace["reading_region"] = zone
        final_paragraphs.append(p)

    return final_paragraphs, {
        "total_paragraphs": len(final_paragraphs),
        "columns_detected": max_cols,
    }
