"""M3: per-block content ledger, placeholder leak checks, and quality_report.json."""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any

DISPOSITIONS = (
    "translated",
    "passthrough",
    "unchanged",
    "untranslated-exception",
    "content-filter",
    "skipped-text-none",
    "incomplete",
)

_LEAKED_PLACEHOLDER_PATTERNS = (
    re.compile(r"</?b\d+>", re.IGNORECASE),
    re.compile(r"</?style(?:\s+id\s*=\s*['\"]?\d+['\"]?)?>", re.IGNORECASE),
    re.compile(r"\{\s*v\s*\d+\s*\}", re.IGNORECASE),
    re.compile(r"\{\d+\}"),
)


_REGEX_ATOM_BEFORE_QUANTIFIER_RE = re.compile(
    r"(?:"
    r"\[[^\]]+\]"
    r"|\\[dDwWsSbB.\\+*?^$()\[\]{}|]"
    r"|\\x[0-9a-fA-F]{2}"
    r"|\\u[0-9a-fA-F]{4}"
    r")$"
)
_REGEX_GROUP_OR_TOKEN_BEFORE_QUANTIFIER_RE = re.compile(
    r"(?:\([^()\n]{1,40}\)|[A-Za-z0-9_.])\$?$"
)
_REGEX_CONTEXT_CUE_RE = re.compile(
    r"(?:\\[dDwWsSbB]|\[\^?[A-Za-z0-9_\-\\]+\]|\^\s*(?:\[|\\|\()|\$\s*$)"
)


def is_regex_quantifier_match(
    text: str,
    match: re.Match[str],
    *,
    is_code_region: bool = False,
) -> bool:
    """Return True when a ``{n}`` match is a regex repetition quantifier such as ``\\d{4}`` or ``[A-Z]{2}``."""
    token = match.group(0)
    if not re.fullmatch(r"\{\d+\}", token):
        return False
    start, end = match.start(), match.end()
    prefix = text[max(0, start - 64) : start]
    # In RTL-rendered text, BiDi can place the quantifier visually to the left of its atom.
    suffix = text[end : min(len(text), end + 64)]
    if _REGEX_ATOM_BEFORE_QUANTIFIER_RE.search(prefix):
        return True
    if re.match(r"^(?:\[[^\]]+\]|\\[dDwWsSbB])", suffix):
        return True
    window = prefix + token + suffix
    if (
        (is_code_region or bool(_REGEX_CONTEXT_CUE_RE.search(window)))
        and _REGEX_GROUP_OR_TOKEN_BEFORE_QUANTIFIER_RE.search(prefix)
    ):
        return True
    return False


def detect_leaked_placeholders(
    rendered_texts: list[str] | str,
    source_text: str = "",
    *,
    is_code_region: bool = False,
) -> list[str]:
    """Return sorted unique internal placeholder tokens left in rendered output.

    Tokens that already appeared verbatim in *source_text* or that form a
    recognized regular-expression quantifier (such as ``\\d{4}`` or ``[A-Z]{2}``)
    are ignored so literal code or regex patterns are not misclassified as leaks.
    """
    if isinstance(rendered_texts, str):
        texts = [rendered_texts]
    else:
        texts = [t for t in rendered_texts if t]
    source = source_text or ""
    leaked: set[str] = set()
    for text in texts:
        for pattern in _LEAKED_PLACEHOLDER_PATTERNS:
            for match in pattern.finditer(text):
                token = match.group(0)
                if token in source:
                    continue
                if is_regex_quantifier_match(text, match, is_code_region=is_code_region):
                    continue
                leaked.add(token)
    return sorted(leaked)


class ContentLedger:
    """Thread-safe ledger tracking every paragraph's translation disposition."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[Any, dict[str, Any]] = {}
        self._seq = 0
        self._glyph_substitutions: dict[tuple[str, str, str], int] = {}
        self._unsupported_glyph_count = 0
        self._layout_issues: list[dict[str, Any]] = []
        self._tables: dict[tuple[Any, str], dict[str, Any]] = {}
        self._preserved_regions: list[dict[str, Any]] = []
        self._reading_order_assigned_count: int = 0
        self._preflight: dict[str, Any] | None = None
        self._terminology: dict[str, Any] | None = None
        self._output_qa: dict[str, Any] | None = None
        self._pagination_metrics: dict[str, int] = {
            "sparse_continuation_pages": 0,
            "clipped_fragment_count": 0,
            "detached_group_count": 0,
        }

    def record_pagination_metrics(
        self,
        *,
        sparse_continuation_pages: int = 0,
        clipped_fragment_count: int = 0,
        detached_group_count: int = 0,
    ) -> None:
        """Record document-level pagination cohesion and continuation metrics."""
        with self._lock:
            self._pagination_metrics = {
                "sparse_continuation_pages": max(0, int(sparse_continuation_pages)),
                "clipped_fragment_count": max(0, int(clipped_fragment_count)),
                "detached_group_count": max(0, int(detached_group_count)),
            }

    def record_preserved_regions(self, regions: list[dict[str, Any]] | None) -> None:
        """Record protected code/algorithm and figure-data regions preserved verbatim."""
        if not regions:
            return
        with self._lock:
            self._preserved_regions.extend(dict(item) for item in regions)

    def record_reading_order(self, assigned_count: int) -> None:
        """Track total paragraphs assigned canonical reading order provenance."""
        if assigned_count <= 0:
            return
        with self._lock:
            self._reading_order_assigned_count += int(assigned_count)

    def record_tables(self, tables: list[dict[str, Any]] | None) -> None:
        """Record canonical semantic table summaries discovered during processing."""
        if not tables:
            return
        with self._lock:
            for item in tables:
                table_id = str(item.get("table_id", ""))
                page = item.get("page")
                if not table_id:
                    continue
                self._tables[(page, table_id)] = dict(item)

    def record_layout_issue(
        self,
        *,
        page: int | None,
        kind: str,
        detail: str,
        debug_id: Any = None,
        severity: str = "warning",
    ) -> None:
        """Record an unresolved layout, overflow, or clipping issue."""
        item = {
            "page": page,
            "kind": kind,
            "detail": detail,
            "debug_id": debug_id,
            "severity": severity,
        }
        with self._lock:
            self._layout_issues.append(item)

    def record_glyph_substitutions(self, summary: dict[str, Any] | None) -> None:
        """Accumulate glyph substitution statistics from typesetter passes."""
        if not summary:
            return
        with self._lock:
            for item in summary.get("substitutions") or []:
                source = str(item.get("source", ""))
                replacement = str(item.get("replacement", ""))
                reason = str(item.get("reason", "unknown"))
                count = int(item.get("count", 0) or 0)
                if not source or count <= 0:
                    continue
                key = (source, replacement, reason)
                self._glyph_substitutions[key] = self._glyph_substitutions.get(key, 0) + count
                if reason == "unsupported_replacement":
                    self._unsupported_glyph_count += count

    def record(
        self,
        *,
        block_key: Any = None,
        page: int | None = None,
        debug_id: int | None = None,
        layout_label: str = "unknown",
        disposition: str,
        reason: str | None = None,
        source_text: str = "",
        translated_text: str = "",
        leaked_placeholders: list[str] | None = None,
        missing_placeholders: list[str] | None = None,
        engine: str = "",
        validation_reason: str | None = None,
        retry_attempted: bool = False,
        retry_outcome: str | None = None,
    ) -> None:
        if disposition not in DISPOSITIONS:
            raise ValueError(f"Unknown disposition: {disposition!r}")
        entry = {
            "page": page,
            "debug_id": debug_id,
            "layout_label": layout_label or "unknown",
            "disposition": disposition,
            "reason": reason,
            "source_len": len(source_text),
            "trans_len": len(translated_text),
            "source_preview": source_text[:120],
            "leaked_placeholders": list(leaked_placeholders or []),
            "missing_placeholders": list(missing_placeholders or []),
            "engine": engine,
            "validation_reason": validation_reason,
            "retry_attempted": bool(retry_attempted),
            "retry_outcome": retry_outcome,
        }
        with self._lock:
            if block_key is None:
                self._seq += 1
                key = ("seq", self._seq)
            else:
                key = block_key
            self._entries[key] = entry

    def record_preflight(self, preflight_report: dict[str, Any]) -> None:
        """Store document/page preflight classification telemetry."""
        with self._lock:
            self._preflight = dict(preflight_report)

    def record_terminology(
        self,
        domain: str,
        abbreviations: dict[str, str] | None = None,
        tm_stats: dict[str, Any] | None = None,
    ) -> None:
        """Store document-level domain, abbreviation disambiguation, and TM stats."""
        stats = dict(tm_stats or {})
        conflicts = list(stats.get("terminology_conflicts") or [])
        conflict_count = int(stats.get("terminology_conflict_count", len(conflicts)) or 0)
        with self._lock:
            self._terminology = {
                "domain": domain,
                "abbreviations": dict(abbreviations or {}),
                "abbreviation_count": len(abbreviations or {}),
                "translation_memory": stats,
                "terminology_conflict_count": conflict_count,
                "terminology_conflicts": conflicts,
            }

    def record_output_qa(self, qa_report: dict[str, Any]) -> None:
        """Store post-write PDF sanity audit telemetry."""
        with self._lock:
            self._output_qa = dict(qa_report)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            entries = list(self._entries.values())
        counts = {name: 0 for name in DISPOSITIONS}
        leaked_blocks = 0
        missing_content_blocks = 0
        for entry in entries:
            counts[entry["disposition"]] += 1
            if entry["leaked_placeholders"] or entry["missing_placeholders"]:
                leaked_blocks += 1
            if entry["reason"] == "empty_translation":
                missing_content_blocks += 1
        return {
            "total_blocks": len(entries),
            "counts": counts,
            "untranslated_count": (
                counts["untranslated-exception"] + counts["content-filter"]
            ),
            "incomplete_count": counts["incomplete"],
            "leaked_placeholder_blocks": leaked_blocks,
            "missing_content_blocks": missing_content_blocks,
        }

    def build_report(
        self,
        *,
        input_file: str | None = None,
        lang_in: str | None = None,
        lang_out: str | None = None,
        engine: str | None = None,
        engine_untranslated_count: int | None = None,
        engine_incomplete_count: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            entries = list(self._entries.values())
            glyph_items = dict(self._glyph_substitutions)
            unsupported_glyph_count = self._unsupported_glyph_count
            layout_issues = list(self._layout_issues)
            tables = list(self._tables.values())
            preserved_regions = list(self._preserved_regions)
            reading_order_assigned_count = self._reading_order_assigned_count
            preflight = dict(self._preflight) if self._preflight is not None else None
            terminology = dict(self._terminology) if self._terminology is not None else None
            output_qa = dict(self._output_qa) if self._output_qa is not None else None
            pagination_metrics = dict(self._pagination_metrics)
        summary = self.snapshot()
        code_algorithm_preserved_count = sum(
            1 for r in preserved_regions if r.get("kind") == "code_or_algorithm"
        )
        figure_preserved_count = sum(
            1 for r in preserved_regions if r.get("kind") == "figure_data_or_token"
        )
        clipped_content_count = sum(
            1
            for issue in layout_issues
            if issue.get("kind") in {
                "clipped_or_missing",
                "off_page",
                "page_overflow_unpaginated",
                "footnote_overflow",
                "contained_region_overflow",
            }
        )
        region_containment_issue_count = sum(
            1
            for issue in layout_issues
            if issue.get("kind") in {"region_escape", "region_collision", "contained_region_overflow"}
        )
        table_cell_overflow_count = sum(
            1
            for issue in layout_issues
            if issue.get("kind") in {"table_cell_overflow", "table_cell_collision"}
        )
        irregular_table_count = sum(
            1 for table in tables if not table.get("is_coherent", True)
        )
        flagged = [
            entry
            for entry in entries
            if entry["disposition"] in {
                "untranslated-exception",
                "content-filter",
                "incomplete",
                "unchanged",
            }
            or (
                entry["disposition"] == "skipped-text-none"
                and entry["source_len"] > 0
            )
        ]
        broken_tounicode_pages = int(
            (preflight or {}).get("broken_tounicode_page_count", 0) or 0
        )
        qa_leaked_pages = len((output_qa or {}).get("leaked_placeholder_pages") or [])
        qa_page_mismatch = bool((output_qa or {}).get("page_count_mismatch", False))
        needs_review = (
            summary["untranslated_count"] > 0
            or summary["incomplete_count"] > 0
            or summary["leaked_placeholder_blocks"] > 0
            or summary["missing_content_blocks"] > 0
            or (engine_untranslated_count or 0) > 0
            or (engine_incomplete_count or 0) > 0
            or unsupported_glyph_count > 0
            or clipped_content_count > 0
            or region_containment_issue_count > 0
            or irregular_table_count > 0
            or table_cell_overflow_count > 0
            or broken_tounicode_pages > 0
            or qa_leaked_pages > 0
            or qa_page_mismatch
        )
        glyph_by_reason: dict[str, int] = {}
        glyph_list = []
        for (src, dst, reason), count in sorted(
            glyph_items.items(),
            key=lambda item: (-item[1], ord(item[0][0]), item[0][2]),
        ):
            glyph_by_reason[reason] = glyph_by_reason.get(reason, 0) + count
            glyph_list.append(
                {
                    "source": src,
                    "source_codepoint": f"U+{ord(src):04X}",
                    "replacement": dst,
                    "replacement_codepoint": f"U+{ord(dst):04X}",
                    "reason": reason,
                    "count": count,
                }
            )
        clipped_fragment_count = max(
            int(pagination_metrics.get("clipped_fragment_count", 0) or 0),
            sum(
                1
                for issue in layout_issues
                if issue.get("kind") in {
                    "clipped_or_missing",
                    "off_page",
                    "page_overflow_unpaginated",
                    "footnote_overflow",
                }
            ),
        )
        detached_group_count = max(
            int(pagination_metrics.get("detached_group_count", 0) or 0),
            sum(1 for issue in layout_issues if issue.get("kind") == "detached_group"),
        )
        sparse_continuation_pages = int(
            pagination_metrics.get("sparse_continuation_pages", 0) or 0
        )
        return {
            "schema_version": "1.0",
            "status": "needs_review" if needs_review else "clean",
            "document": {
                "input_file": str(input_file) if input_file else None,
                "lang_in": lang_in,
                "lang_out": lang_out,
                "engine": engine,
            },
            "summary": {
                **summary,
                "engine_untranslated_count": engine_untranslated_count,
                "engine_incomplete_count": engine_incomplete_count,
                "glyph_substitution_count": sum(glyph_items.values()),
                "unsupported_glyph_count": unsupported_glyph_count,
                "layout_issue_count": len(layout_issues),
                "clipped_content_count": clipped_content_count,
                "clipped_fragment_count": clipped_fragment_count,
                "sparse_continuation_pages": sparse_continuation_pages,
                "detached_group_count": detached_group_count,
                "region_containment_issue_count": region_containment_issue_count,
                "table_count": len(tables),
                "irregular_table_count": irregular_table_count,
                "table_cell_overflow_count": table_cell_overflow_count,
                "code_algorithm_preserved_count": code_algorithm_preserved_count,
                "figure_preserved_count": figure_preserved_count,
                "reading_order_assigned_count": reading_order_assigned_count,
                "broken_tounicode_page_count": broken_tounicode_pages,
                "detected_domain": (terminology or {}).get("domain"),
                "translation_memory_hits": (
                    ((terminology or {}).get("translation_memory") or {}).get("hits", 0)
                ),
                "terminology_conflict_count": int(
                    (terminology or {}).get("terminology_conflict_count", 0) or 0
                ),
                "validation_retry_count": sum(1 for e in entries if e.get("retry_attempted")),
                "validation_retry_succeeded_count": sum(1 for e in entries if e.get("retry_outcome") == "succeeded"),
                "validation_retry_failed_count": sum(1 for e in entries if e.get("retry_outcome") == "failed"),
                "validation_retry_partial_count": sum(1 for e in entries if e.get("retry_outcome") == "partial"),
                "source_text_retained_count": sum(
                    1
                    for e in entries
                    if e.get("disposition") == "incomplete"
                    and (
                        e.get("retry_outcome") in {"partial", "failed"}
                        or e.get("validation_reason") in {"partial_span_recovery", "source_text_retained", "protected_content_mismatch"}
                    )
                ),
            },
            "preflight": preflight,
            "terminology": terminology,
            "output_qa": output_qa,
            "glyph_substitutions": {
                "total_substitutions": sum(glyph_items.values()),
                "unsupported_replacement_count": unsupported_glyph_count,
                "by_reason": glyph_by_reason,
                "substitutions": glyph_list,
            },
            "tables": tables,
            "preserved_regions": preserved_regions,
            "layout_issues": layout_issues,
            "flagged_blocks": flagged,
            "blocks": entries,
        }

    def write_json(self, output_path: str | Path, **report_kwargs: Any) -> Path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.build_report(**report_kwargs)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path
