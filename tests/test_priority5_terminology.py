"""Priority 5 acceptance tests: reusable terminology glossary, consistency, and conflict tracking."""
from __future__ import annotations

import json
from pathlib import Path

from babeldoc.format.pdf.document_il.midend.domain_glossary import (
    DocumentTranslationMemory,
    apply_post_glossary,
    apply_pre_glossary,
    classify_document_domain,
    extract_document_abbreviations,
)
from babeldoc.format.pdf.document_il.utils.quality_report import ContentLedger


def test_terminology_glossary_json_exists_and_loads_cleanly():
    glossary_path = (
        Path(__file__).resolve().parents[1]
        / "babeldoc"
        / "format"
        / "pdf"
        / "document_il"
        / "midend"
        / "terminology_glossary.json"
    )
    assert glossary_path.exists(), "terminology_glossary.json must exist as a reusable structured resource"
    payload = json.loads(glossary_path.read_text(encoding="utf-8"))
    assert payload.get("pre_rules")
    assert payload.get("post_rules")
    assert payload.get("tracked_terms")


def test_multisentence_policy_geographic_and_acronym_consistency():
    docs = [
        "The United Nations Children's Fund (UNICEF) works with the Child Welfare Commission across Baghdad, Basra, Ninewa, Anbar, and the Kurdistan Region.",
        "Under the United Nations Sustainable Development Cooperation Framework (UNSDCF), Multi-Indicator Cluster Survey (MICS) data guides the Summary budget table.",
        "UNICEF, WHO, and UNDP coordinate Country programme priorities and partnerships with national authorities.",
    ]
    domain = classify_document_domain(docs)
    abbrevs = extract_document_abbreviations(docs)
    assert domain == "policy_un"
    assert "UNICEF" in abbrevs
    assert "UNSDCF" in abbrevs
    assert "MICS" in abbrevs

    # Pre-glossary locks institutional & geographic entities cleanly across multi-sentence input
    pre = apply_pre_glossary(
        "In Ninewa and the Kurdistan Region, the Child Welfare Commission reviewed the Summary budget table for MICS.",
        domain=domain,
        abbreviations=abbrevs,
    )
    assert "نينوى" in pre
    assert "إقليم كردستان" in pre
    assert "هيئة رعاية الطفولة" in pre
    assert "جدول الميزانية الموجز" in pre
    assert "MICS" in pre

    # Post-glossary repairs common NLLB phonetic/semantic drift across full Arabic sentences
    drifted_ar = (
        "أشارت يونسيف ومنظمة الصحة العالمية وبرنامج الأمم المتحدة الإنمائي في نيينوى واربيل "
        "إلى بيانات ميكس في وثيقة برنامج البلد لمراقبة وتقييم الأطفال الضعفاء."
    )
    repaired = apply_post_glossary(
        drifted_ar,
        source_text=(
            "UNICEF, WHO, and UNDP noted in Ninewa and Erbil that MICS data in the "
            "Country programme document monitors vulnerable children."
        ),
        domain=domain,
        abbreviations=abbrevs,
    )
    assert "اليونيسف" in repaired
    assert "نينوى" in repaired
    assert "أربيل" in repaired
    assert "MICS" in repaired
    assert "وثيقة البرنامج القطري" in repaired
    assert "الأطفال الأكثر هشاشة" in repaired


def test_translation_memory_tracks_and_reports_terminology_conflicts():
    tm = DocumentTranslationMemory()
    tm.record_term_observations(
        "UNICEF supports Ninewa and the Kurdistan Region budget.",
        "تدعم اليونيسف ميزانية نينوى وإقليم كردستان.",
        domain="policy_un",
        abbreviations={"UNICEF": "United Nations Children's Fund"},
    )
    # Second paragraph uses conflicting variant for Kurdistan Region & Ninewa & UNICEF!
    tm.record_term_observations(
        "UNICEF expands programmes in Ninewa and the Kurdistan Region.",
        "توسع يونسيف البرامج في نيينوى واقليم كوردستان.",
        domain="policy_un",
        abbreviations={"UNICEF": "United Nations Children's Fund"},
    )
    stats = tm.stats()
    assert stats["terminology_conflict_count"] >= 2
    conflicts = tm.conflicts()
    conflict_terms = {c["source_term"] for c in conflicts}
    assert "Ninewa" in conflict_terms or "UNICEF" in conflict_terms

    # Wire into ContentLedger and verify quality_report summary exposes conflict count
    ledger = ContentLedger()
    ledger.record_terminology("policy_un", {"UNICEF": "United Nations Children's Fund"}, stats)
    report = ledger.build_report()
    assert report["summary"]["terminology_conflict_count"] >= 2
    assert len(report["terminology"]["terminology_conflicts"]) >= 2
