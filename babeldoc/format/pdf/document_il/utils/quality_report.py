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


def detect_leaked_placeholders(
    rendered_texts: list[str] | str,
    source_text: str = "",
) -> list[str]:
    """Return sorted unique internal placeholder tokens left in rendered output.

    Tokens that already appeared verbatim in *source_text* are ignored so
    literal code or text like ``{0}`` is not misclassified as a leak.
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
                if token not in source:
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
        }
        with self._lock:
            if block_key is None:
                self._seq += 1
                key = ("seq", self._seq)
            else:
                key = block_key
            self._entries[key] = entry

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
        summary = self.snapshot()
        clipped_content_count = sum(
            1
            for issue in layout_issues
            if issue.get("kind") in {
                "clipped_or_missing",
                "off_page",
                "page_overflow_unpaginated",
                "footnote_overflow",
            }
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
        needs_review = (
            summary["untranslated_count"] > 0
            or summary["incomplete_count"] > 0
            or summary["leaked_placeholder_blocks"] > 0
            or summary["missing_content_blocks"] > 0
            or (engine_untranslated_count or 0) > 0
            or (engine_incomplete_count or 0) > 0
            or unsupported_glyph_count > 0
            or clipped_content_count > 0
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
            },
            "glyph_substitutions": {
                "total_substitutions": sum(glyph_items.values()),
                "unsupported_replacement_count": unsupported_glyph_count,
                "by_reason": glyph_by_reason,
                "substitutions": glyph_list,
            },
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
