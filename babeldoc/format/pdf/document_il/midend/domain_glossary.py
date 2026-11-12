"""Domain-specific (QCB / IBAN banking) wording rules for English -> Arabic.

These rules are deliberately kept apart from the general PDF/translation
pipeline.  Everything in ``il_translator`` that is language- or layout-generic
stays there; wording that only makes sense for the Qatar Central Bank IBAN
standard (Annex 193) lives here.

Set the environment variable ``TRANSDOC_DOMAIN_GLOSSARY=0`` to disable them.
"""

from __future__ import annotations

import os
import re

_I = re.IGNORECASE


def _enabled() -> bool:
    return os.environ.get("TRANSDOC_DOMAIN_GLOSSARY", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }


# (pattern, replacement, flags) applied to the English source before NLLB.
_PRE_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r'\bIBAN\s+Standards\s+Ver\.?\s*2\.?\s*0\s*[-–—]\s*QATAR\b', _I),
     'معايير IBAN الإصدار 2.0 - قطر'),
    (re.compile(r'\bQatar\s+Central\s+Bank\b', _I), 'مصرف قطر المركزي'),
    (re.compile(r'\bQatar\s+Automated\s+Clearing\s+House\b', _I), 'غرفة المقاصة الآلية القطرية'),
    (re.compile(r'\bCheck\s+Digit\s+Generation\s+and\s+Validation\b', _I), 'توليد رقم التحقق والتحقق من صحته'),
    (re.compile(r'\bCheck\s+Digit\s+Validation\b', _I), 'التحقق من صحة رقم التحقق'),
    (re.compile(r'\bCheck\s+Digit\s+Generation\b', _I), 'توليد رقم التحقق'),
    (re.compile(r'\bGeneration\s+of\s+IBAN\b', _I), 'إنشاء رقم IBAN'),
    (re.compile(r'\bSwift\s+Code\s+and\s+Identifier\b', _I), 'رمز سويفت (SWIFT) ومعرّف البنك'),
    (re.compile(r'\(\s*First\s+4\s+characters?\s+of\s+BIC\s*\)', _I), '(أول 4 أحرف من رمز BIC)'),
    (re.compile(r'\(\s*e\.?g\.?\s+QA00\s+for\s+QATAR\s*\)', _I), '(مثل QA00 لدولة قطر)'),
    (re.compile(r'\(\s*29\s+characters?\s+for\s+QATAR\s*\)', _I), '(29 حرفاً لدولة قطر)'),
    (re.compile(r'\bStraight\s+Through\s+Processing\b', _I), 'المعالجة المباشرة دون تدخل يدوي'),
    (re.compile(r'\bActing\s+Director\b', _I), 'مدير بالإنابة'),
    (re.compile(r'\bPictorial\s+Flow\b', _I), 'المخطط التوضيحي للتدفق'),
    (re.compile(r'\bUpdate\s+History\b', _I), 'سجل التحديثات'),
    (re.compile(r'\bPresentation\s+Format\b', _I), 'صيغة العرض'),
    (re.compile(r'\bInternational\s+Envelope\b', _I), 'الغلاف الدولي'),
    (re.compile(r'\bFirst\s+Edition\b', _I), 'الطبعة الأولى'),
    (re.compile(r'^\s*(\d+(?:\.\d+)*\.?\s+)Validation\s*$', _I), r'\1التحقق من الصحة'),
]

_PRE_WHOLE_TEXT = [
    (re.compile(r'\s*Validation\s*', _I), 'التحقق من الصحة'),
    (re.compile(r'\s*Edition\s*', _I), 'الطبعة'),
]

_B = r'(?<![\u0621-\u064A])([وبلكف]?)'

# Fixes for known NLLB mistranslations of the same vocabulary.
_POST_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(_B + r'البنك\s+المركزي\s+القطري\b'), r'\1مصرف قطر المركزي'),
    (re.compile(_B + r'مركز\s+تصفية\s+قطر\s+الآلي\b'), r'\1غرفة المقاصة الآلية القطرية'),
    (re.compile(_B + r'تطهير(ها|ه)\s+من\s+خلال\s+QATCH\b'), r'\1مقاصت\2 عبر QATCH'),
    (re.compile(_B + r'التحقق\s+من\s+تصحيح\s+الأرقام\b'), r'\1التحقق من صحة رقم التحقق'),
    (re.compile(_B + r'التحقق\s+من\s+الرقم\s+المالي\b'), r'\1التحقق من صحة رقم التحقق'),
    (re.compile(_B + r'توليد\s+الرقم\s+المالي\b'), r'\1توليد رقم التحقق'),
    (re.compile(_B + r'رقمي\s+الشيك\b'), r'\1رقمي التحقق'),
    (re.compile(_B + r'رقما\s+الشيك\b'), r'\1رقما التحقق'),
    (re.compile(_B + r'أرقام\s+الشيك(?:ات)?\b'), r'\1أرقام التحقق'),
    (re.compile(_B + r'رقم\s+الشيك\b'), r'\1رقم التحقق'),
    (re.compile(_B + r'جيل\s+IBAN\b'), r'\1إنشاء رقم IBAN'),
    (re.compile(_B + r'الرمز\s+السريع\s+وتعريف\s+سريع\b'), r'\1رمز سويفت (SWIFT) ومعرّف البنك'),
    (re.compile(_B + r'الرمز\s+السريع\b'), r'\1رمز سويفت (SWIFT)'),
    (re.compile(_B + r'تعريف\s+سريع\b'), r'\1معرّف البنك'),
    (re.compile(_B + r'التحقق\s+من\s+المصادقة\b'), r'\1التحقق من الصحة'),
    (re.compile(_B + r'(?:استبعاد|استبعد|طرح)\s+الباقي\s+من\s+(\d+)\b'), r'\1اطرح الباقي من \2'),
    (re.compile(_B + r'(?:الصفر|الأصفار)\s+الرائدة\b'), r'\1الأصفار البادئة'),
    (re.compile(_B + r'الصفحة\s+اليسرى\s+مع\s+الصفر\b'), r'\1تُملأ من اليسار بأصفار'),
    (re.compile(_B + r'رقم\s+متكامل\s+موقع\b'), r'\1عدد صحيح ذو إشارة'),
    (re.compile(_B + r'الطلب\s+المصرفي\s+الرئيسي\b'), r'\1النظام المصرفي الأساسي'),
    (re.compile(_B + r'رقم\s+الحساب\s+المصري\b'), r'\1رقم الحساب المصرفي'),
    (re.compile(_B + r'حرف[اً]*\s+ألف[اً]*\s+للأرقام\b'), r'\1حرفاً من الحروف والأرقام'),
    (re.compile(r'\b(\d+\s+أو\s+\d+)\s+الأوراق\b'), r'\1 رقماً'),
    (re.compile(_B + r'مسؤولية\s+البنك\s+الوحيد\b'), r'\1المسؤولية الحصرية للبنك'),
    (re.compile(_B + r'مباشرة\s+من\s+خلال\s+المعالجة\b'), r'\1المعالجة المباشرة دون تدخل يدوي'),
    (re.compile(_B + r'نائب\s+مدير\b'), r'\1مدير بالإنابة'),
    (re.compile(_B + r'التدفق\s+الرسمي\b'), r'\1المخطط التوضيحي للتدفق'),
    (re.compile(_B + r'تحديث\s+التاريخ\b'), r'\1سجل التحديثات'),
    (re.compile(_B + r'نموذج\s+العرض\b'), r'\1صيغة العرض'),
    (re.compile(_B + r'الملف\s+الدولي\b'), r'\1الغلاف الدولي'),
]


def apply_pre_glossary(text: str) -> str:
    """Lock domain phrases in the English source before translation."""
    if not text or not _enabled():
        return text
    for pattern, replacement in _PRE_RULES:
        text = pattern.sub(replacement, text)
    for pattern, replacement in _PRE_WHOLE_TEXT:
        if pattern.fullmatch(text):
            return replacement
    return text


def apply_post_glossary(text: str) -> str:
    """Repair known mistranslations in the Arabic output."""
    if not text or not _enabled():
        return text
    for pattern, replacement in _POST_RULES:
        text = pattern.sub(replacement, text)
    return text
