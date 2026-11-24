"""Domain-specific (QCB / IBAN banking) wording rules for English -> Arabic.

These rules are deliberately kept apart from the general PDF/translation
pipeline.  Everything in ``il_translator`` that is language- or layout-generic
stays there; wording that only makes sense for the Qatar Central Bank IBAN
standard (Annex 193) lives here.

Set the environment variable ``TRANSDOC_DOMAIN_GLOSSARY=0`` to disable them.
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Iterable

_I = re.IGNORECASE
_GLOSSARY_JSON_PATH = Path(__file__).with_name("terminology_glossary.json")


def _load_structured_glossary() -> dict[str, Any]:
    if not _GLOSSARY_JSON_PATH.exists():
        return {}
    try:
        data = json.loads(_GLOSSARY_JSON_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


_GLOSSARY_DATA = _load_structured_glossary()
_GLOSSARY_PRE_RULES: list[tuple[str, re.Pattern, str]] = [
    (
        str(item.get("domain", "any") or "any"),
        re.compile(
            str(item["pattern"]),
            _I if item.get("ignore_case", True) else 0,
        ),
        str(item["replacement"]),
    )
    for item in (_GLOSSARY_DATA.get("pre_rules") or [])
    if item.get("pattern") and "replacement" in item
]
_GLOSSARY_POST_RULES: list[tuple[re.Pattern, re.Pattern, str]] = [
    (
        re.compile(str(item["source_pattern"]), _I),
        re.compile(str(item["pattern"])),
        str(item["replacement"]),
    )
    for item in (_GLOSSARY_DATA.get("post_rules") or [])
    if item.get("source_pattern") and item.get("pattern") and "replacement" in item
]
_TRACKED_GLOSSARY_TERMS: list[dict[str, Any]] = [
    {
        "source_term": str(item["source_term"]),
        "source_re": re.compile(str(item["source_pattern"]), _I),
        "canonical": str(item.get("canonical") or item["source_term"]),
        "variant_res": [re.compile(str(vp)) for vp in (item.get("variant_patterns") or []) if vp],
    }
    for item in (_GLOSSARY_DATA.get("tracked_terms") or [])
    if item.get("source_term") and item.get("source_pattern")
]


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
    # Scientific / NLP / Deep Learning terminology (JMLR & ML papers)
    (re.compile(r'^\s*(\d+(?:\.\d+)*\.?\s+)Linguistic\s+Correlation\s+Analysis\s*$', _I), r'\1تحليل الارتباط اللغوي'),
    (re.compile(r'^\s*(\d+(?:\.\d+)*\.?\s+)Grid\s+Search\s*$', _I), r'\1البحث الشبكي (Grid Search)'),
    (re.compile(r'^\s*(\d+(?:\.\d+)*\.?\s+)Neuron\s+Ranking\s+Algorithm\s*$', _I), r'\1خوارزمية ترتيب الخلايا العصبية'),
    (re.compile(r'^\s*(\d+(?:\.\d+)*\.?\s+)Minimal\s+Neuron\s+Selection\s*$', _I), r'\1اختيار الحد الأدنى من الخلايا العصبية'),
    (re.compile(r'^\s*(\d+(?:\.\d+)*\.?\s+)Minimal\s+Neuron\s+Subset\s*$', _I), r'\1المجموعة الجزئية الدنيا من الخلايا العصبية'),
    (re.compile(r'^\s*(\d+(?:\.\d+)*\.?\s+)Selectivity\s*$', _I), r'\1الانتقائية'),
    (re.compile(r'^\s*(\d+(?:\.\d+)*\.?\s+)Ablation\s+Study\s*$', _I), r'\1دراسة الاستئصال (Ablation Study)'),
    (re.compile(r'^\s*(\d+(?:\.\d+)*\.?\s+)Experimental\s+Setup\s*$', _I), r'\1إعداد التجارب'),
    (re.compile(r'^\s*(\d+(?:\.\d+)*\.?\s+)Qualitative\s+Evaluation\s*$', _I), r'\1التقييم النوعي'),
    (re.compile(r'\(\s*a\s*\)\s*Superlative\s+Adjective\s+Neuron\s*\(\s*ROBERTA\s*\)\s*[-–—]\s*Layer\s*(\d+)\s*:\s*(\d+)', _I),
     r'(أ) خلية عصبية لصيغ التفضيل العليا (RoBERTa) – الطبقة \1: \2'),
    (re.compile(r'\(\s*b\s*\)\s*Gerund\s+Verb\s+Neuron\s*\(\s*ROBERTA\s*\)\s*[-–—]\s*Layer\s*(\d+)\s*:\s*(\d+)', _I),
     r'(ب) خلية عصبية للأفعال المصدرية (RoBERTa) – الطبقة \1: \2'),
    (re.compile(r'\(\s*a\s*\)\s*Person\s+Name\s+Neuron\s*\(\s*XLNet\s*\)\s*[-–—]\s*Layer\s*(\d+)\s*:\s*(\d+)', _I),
     r'(أ) خلية عصبية لأسماء الأشخاص (XLNet) – الطبقة \1: \2'),
    (re.compile(r'\(\s*b\s*\)\s*Place\s+Neuron\s*\(\s*XLNet\s*\)\s*[-–—]\s*Layer\s*(\d+)\s*:\s*(\d+)', _I),
     r'(ب) خلية عصبية للأماكن (XLNet) – الطبقة \1: \2'),
    (re.compile(r'\bretaining\s+the\s+(top,\s*random,?\s*and\s*bottom)\s+(\d+(?:\.\d+)?%)\s+neurons\b', _I),
     r'retaining \2 of the \1 neurons'),
    (re.compile(r'\bSelecting\s+(all,\s*top,\s*random\s+and\s+bottom)\s+(\d+(?:\.\d+)?%)\s+neurons\b', _I),
     r'Selecting \2 of \1 neurons'),
    # Institutional / policy / UN report terminology
    (re.compile(r'\bCountry\s+programme\s+document\b', _I), 'وثيقة البرنامج القطري'),
    (re.compile(r'\bProgramme\s+rationale\b', _I), 'الأساس المنطقي للبرنامج'),
    (re.compile(r'\bProgramme\s+priorities\s+and\s+partnerships\b', _I), 'أولويات البرنامج والشراكات'),
    (re.compile(r'\bSummary\s+budget\s+table\b', _I), 'جدول الميزانية الموجز'),
    (re.compile(r'\bUNSDCF\s+outcomes?\b', _I), 'حصائل إطار الأمم المتحدة للتعاون من أجل التنمية المستدامة (UNSDCF)'),
    (re.compile(r'\bUNICEF\s+outcomes?\b', _I), 'حصائل اليونيسف'),
    (re.compile(r'\bDistr\.\s*:\s*Limited\b', _I), 'توزيع: محدود'),
    (re.compile(r'\bDistr\.\s*:\s*General\b', _I), 'توزيع: عام'),
    (re.compile(r"\bUnited\s+Nations\s+Children['\u2019]s\s+Fund\b", _I), 'منظمة الأمم المتحدة للطفولة (اليونيسف)'),
    (re.compile(r'\bExecutive\s+Board\b', _I), 'المجلس التنفيذي'),
    (re.compile(r'\bEconomic\s+and\s+Social\s+Council\b', _I), 'المجلس الاقتصادي والاجتماعي'),
    (re.compile(
        r'\bUnited\s+Nations\s+Sustainable\s+Development\s+Cooperation\s+Framework\b',
        _I,
    ), 'إطار الأمم المتحدة للتعاون في مجال التنمية المستدامة'),
    (re.compile(r'\bEarly\s+Childhood\s+Development\b', _I), 'تنمية الطفولة المبكرة'),
    (re.compile(r'\bChild\s+Welfare\s+Commission\b', _I), 'هيئة رعاية الطفولة'),
    (re.compile(r'^\s*Qty\s*$', _I), 'الكمية'),
    (re.compile(r'^\s*ibid\.?\s*$', _I), 'المرجع نفسه'),
]

_PRE_WHOLE_TEXT = [
    (re.compile(r'\s*Summary\s*', _I), 'موجز'),
    (re.compile(r'\s*Validation\s*', _I), 'التحقق من الصحة'),
    (re.compile(r'\s*Edition\s*', _I), 'الطبعة'),
    (re.compile(r'\s*Discovering\s+Salient\s+Neurons\s+in\s+deep\s+NLP\s+models\s*', _I),
     'اكتشاف الخلايا العصبية البارزة في نماذج معالجة اللغات الطبيعية (NLP) العميقة'),
    (re.compile(r'\s*Abstract\s*', _I), 'الملخص'),
    (re.compile(r'\s*Salient\s+Neurons\s*', _I), 'الخلايا العصبية البارزة'),
    (re.compile(r'\s*Probes\s*', _I), 'المسبارات'),
    (re.compile(r'\s*Trained\s*', _I), 'مدرَّب'),
    (re.compile(r'\s*Model\s*', _I), 'نموذج'),
    (re.compile(r'\s*extraction\s*', _I), 'استخراج'),
    (re.compile(r'\s*from\s+weight\s+distribution\s*', _I), 'من توزيع الأوزان'),
    (re.compile(r'\s*Past\s+tense\s+verb\s*', _I), 'فعل ماضٍ'),
    (re.compile(r'\s*Verb\s+phrase\s*', _I), 'عبارة فعلية'),
    (re.compile(r'\s*Chunking\s*', _I), 'التجزئة'),
    (re.compile(r'\s*Chunk\s*', _I), 'التجزئة'),
    (re.compile(r'\s*Suffix\s*', _I), 'اللاحقة'),
    (re.compile(r'\s*Words\s*', _I), 'الكلمات'),
    (re.compile(r'\s*POS\s*'), 'POS'),
    (re.compile(r'\s*Probeless\s*', _I), 'Probeless'),
    (re.compile(r'\s*Wt\.\s*Mag\.\s*', _I), 'حجم الوزن'),
    (re.compile(r'\s*\(\s*Beginning\s*\)\s*', _I), '(البداية)'),
    (re.compile(r'\s*subject\s*'), 'فاعل'),
    (re.compile(r'\s*object\s*'), 'مفعول'),
    (re.compile(r'\s*case\s*'), 'حالة'),
    (re.compile(r'\s*det\s*'), 'det'),
    (re.compile(r'\s*obl\s*'), 'obl'),
    (re.compile(r'\s*nmod\s*'), 'nmod'),
    (re.compile(r'\s*the\s*'), 'the'),
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
    (re.compile(_B + r'نتائج\s+المعهد\s+الدولي\s+للصحة\s+والتنمية\b'), r'\1حصائل إطار التعاون (UNSDCF)'),
    (re.compile(_B + r'أسباب\s+البرنامج\b'), r'\1الأساس المنطقي للبرنامج'),
]


_SCIENTIFIC_CONTEXT = re.compile(
    r"\b(?:NLP|neural|neuron|neurons|linguistic|probe|probes|selectivity|"
    r"ablation|RoBERTa|XLNet|BERT|syntax|suffixation)\b",
    _I,
)

# These repairs are intentionally source-gated. Several broken Arabic outputs
# (for example "elections" for "selectivity" or "blind test" for an unseen
# test set) are perfectly valid in unrelated documents.
_SCIENTIFIC_POST_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(_B + r'الخلايا\s+العصبية\s+الصلبة\b'), r'\1الخلايا العصبية البارزة'),
    (re.compile(_B + r'الخلايا\s+النفسية\b'), r'\1الخلايا العصبية'),
    (re.compile(_B + r'الخلية\s+النفسية\b'), r'\1الخلية العصبية'),
    (re.compile(_B + r'العصابات\s+العصبية\b'), r'\1الخلايا العصبية'),
    (re.compile(_B + r'بخلايا\s+العصابات\b'), r'\1بالخلايا العصبية'),
    (re.compile(_B + r'تحليل\s+(?:التواصل\s+اللغوي|العلاقة\s+اللغوية)\b'), r'\1تحليل الارتباط اللغوي'),
    (re.compile(_B + r'دقة\s+(?:القرآن|النبيذ)\b'), r'\1دقة المرجع المثالي (Oracle)'),
    (re.compile(_B + r'(?:الانتخابية|الانتخابات|الانتخابيات)\b'), r'\1الانتقائية'),
    (re.compile(_B + r'نقطة\s+التسجيل\b'), r'\1POS'),
    (re.compile(_B + r'(?:الفضائل|المصطلحات)\s+العظمى\b'), r'\1صيغ التفضيل العليا'),
    (re.compile(_B + r'المفعولات\s+الجيروندية\b'), r'\1الأفعال المصدرية (Gerund)'),
    (re.compile(_B + r'(?:الموقف|وضع)\s+العصبية\b'), r'\1خلية عصبية للموقع'),
    (re.compile(r'(?<![\u0621-\u064A])العثور(?:\s+على)?\s*:'), 'النتيجة:'),
    (re.compile(_B + r'ثلاث\s+ركضات\b'), r'\g<1>3 جولات تشغيل'),
    (re.compile(_B + r'اختبار\s+(?:الأعمى|العمياء)\b'), r'\1الاختبار غير المرئي'),
    (re.compile(_B + r'مجموعة\s+اختبار\s+العمياء\b'), r'\1مجموعة الاختبار غير المرئية'),
    (re.compile(_B + r'دراسة\s+(?:إزالة|التجاوز)\b'), r'\1دراسة الاستئصال'),
    (re.compile(_B + r'كأفضل\s+رئيس\b'), r'\1كأسوأ رئيس'),
    (re.compile(r'(?<![A-Za-z])(?:w\.r\.t\.?|t\.r\.w\.?)\s+الملكية\b', _I), 'فيما يتعلق بالخاصية'),
    (re.compile(r'(?<![A-Za-z])(?:w\.r\.t\.?|t\.r\.w\.?)(?![A-Za-z])', _I), 'فيما يتعلق بـ'),
]


_DOMAIN_CUES: dict[str, re.Pattern] = {
    "scientific_nlp": re.compile(
        r"\b(?:NLP|neural|neuron|neurons|transformer|BERT|RoBERTa|XLNet|"
        r"ablation|selectivity|linguistics?|classifier|theorem|algorithm|"
        r"hyperparameter|benchmark|abstract|arxiv|doi|corpus|tokenization)\b",
        _I,
    ),
    "financial_banking": re.compile(
        r"\b(?:IBAN|BBAN|SWIFT|BIC|central\s+bank|clearing\s+house|repo\s+rate|"
        r"monetary|inflation|liquidity|gdp|balance\s+of\s+payments|MOD\s*97|"
        r"treasury|remittance|settlement|fiscal|banking)\b",
        _I,
    ),
    "invoice_form": re.compile(
        r"\b(?:invoice|purchase\s+order|bill\s+to|ship\s+to|subtotal|"
        r"tax\s+id|vat\s+no|receipt|due\s+date|payment\s+terms|authorized\s+signature|"
        r"applicant|form\s+no|remit\s+to)\b",
        _I,
    ),
    "legal_contract": re.compile(
        r"\b(?:whereas|hereinafter|indemnify|jurisdiction|governing\s+law|"
        r"arbitration|confidentiality|liability|termination|agreement|clause)\b",
        _I,
    ),
    "policy_un": re.compile(
        r"\b(?:unicef|unsdcf|executive\s+board|economic\s+and\s+social\s+council|"
        r"country\s+programme|sustainable\s+development\s+cooperation|"
        r"united\s+nations\s+children)\b",
        _I,
    ),
}

_ABBREV_DEF_RE = re.compile(
    r"([A-Za-z][A-Za-z\-',]*(?:\s+(?:of|and|for|the|in|on|to|[A-Za-z][A-Za-z\-',]*)){0,7})\s*\(\s*([A-Z][A-Z0-9]{1,9})\s*\)"
)
_IGNORED_ABBREVS = {"USA", "USD", "EUR", "GBP", "SAR", "AED", "QAR", "PDF", "URL", "DOI"}
_INTERNAL_PLACEHOLDER_RE = re.compile(r"\{\d{1,3}\}|<\s*/?\s*b\s*\d+\s*>")


def _abbrev_matches_expansion(expansion: str, acronym: str) -> bool:
    """Return True when an uppercase acronym plausibly abbreviates ``expansion``."""
    if not acronym or acronym in _IGNORED_ABBREVS:
        return False
    words = [w for w in re.split(r"[\s\-,]+", expansion.strip()) if w]
    content_words = [
        w for w in words if w.lower() not in {"of", "and", "for", "the", "in", "on", "to"}
    ]
    if len(content_words) < 2:
        return False
    initials = "".join(w[0].upper() for w in content_words if w[0].isalpha())
    all_initials = "".join(w[0].upper() for w in words if w[0].isalpha())
    acro_letters = "".join(c for c in acronym if c.isalpha())
    if len(acro_letters) < 2:
        return False
    if acro_letters == initials or acro_letters == all_initials:
        return True
    # Subsequence check inside content initials / uppercase letters of expansion
    exp_upper = re.sub(r"[^A-Za-z]", "", expansion).upper()
    pos = 0
    for ch in acro_letters:
        found = exp_upper.find(ch, pos)
        if found < 0:
            return False
        pos = found + 1
    return acro_letters[0] == content_words[0][0].upper()


def classify_document_domain(texts: Iterable[str]) -> str:
    """Classify the overall document domain from its paragraph texts."""
    scores: dict[str, int] = {name: 0 for name in _DOMAIN_CUES}
    distinct_hits: dict[str, set[str]] = {name: set() for name in _DOMAIN_CUES}
    for raw in texts:
        if not raw:
            continue
        for domain, pattern in _DOMAIN_CUES.items():
            matches = pattern.findall(raw)
            if matches:
                scores[domain] += len(matches)
                for m in matches:
                    distinct_hits[domain].add(m.lower().strip())
    ranked = sorted(
        scores.keys(),
        key=lambda d: (len(distinct_hits[d]), scores[d]),
        reverse=True,
    )
    best = ranked[0] if ranked else "general"
    if len(distinct_hits.get(best, ())) >= 2 or scores.get(best, 0) >= 3:
        return best
    return "general"


_LEADING_EXPANSION_STOPWORDS = {
    "the", "a", "an", "and", "or", "on", "in", "of", "for", "to", "by", "with", "from", "at", "as",
}


def _trim_matching_expansion(raw_span: str, acronym: str) -> str | None:
    """Return the suffix of ``raw_span`` whose words match ``acronym``, or None."""
    tokens = [t for t in raw_span.strip().split() if t]
    for start in range(len(tokens)):
        if tokens[start].strip(" ,;:-").lower() in _LEADING_EXPANSION_STOPWORDS:
            continue
        candidate = " ".join(tokens[start:]).strip(" ,;:-")
        if _abbrev_matches_expansion(candidate, acronym):
            return candidate
    return None


def extract_document_abbreviations(texts: Iterable[str]) -> dict[str, str]:
    """Extract first-occurrence ``Expansion (ACRONYM)`` definitions per document."""
    abbreviations: dict[str, str] = {}
    for raw in texts:
        if not raw:
            continue
        for match in _ABBREV_DEF_RE.finditer(raw):
            expansion = " ".join(match.group(1).split()).strip(" ,;:-")
            acronym = match.group(2).strip()
            if acronym in abbreviations:
                continue
            matched_exp = _trim_matching_expansion(expansion, acronym)
            if matched_exp is not None:
                abbreviations[acronym] = matched_exp
    return abbreviations


def _abbreviations_indicate_scientific(abbreviations: dict[str, str] | None) -> bool:
    if not abbreviations:
        return False
    combined = " ".join(f"{k} {v}" for k, v in abbreviations.items()).lower()
    return any(
        cue in combined
        for cue in ("part-of-speech", "natural language", "neural", "transformer", "linguistic")
    )


class DocumentTranslationMemory:
    """Thread-safe document-level translation memory for consistent repeated segments."""

    def __init__(self, max_entries: int = 4096) -> None:
        self._lock = threading.Lock()
        self._entries: dict[tuple[str, str, str, str], str] = {}
        self._max_entries = max_entries
        self._hits = 0
        self._misses = 0
        self._term_variants: dict[str, dict[str, int]] = {}
        self._term_canonical: dict[str, str] = {}
        self._source_conflicts: dict[str, set[str]] = {}

    @staticmethod
    def _normalize_key(text: str) -> str:
        return re.sub(r"\s+", " ", (text or "").strip())

    def record_term_observations(
        self,
        source_text: str,
        translated_text: str,
        *,
        domain: str | None = None,
        abbreviations: dict[str, str] | None = None,
    ) -> None:
        """Track surface forms of recurring high-impact terms to detect inconsistencies."""
        if not source_text or not translated_text:
            return
        with self._lock:
            for spec in _TRACKED_GLOSSARY_TERMS:
                if not spec["source_re"].search(source_text):
                    continue
                term = spec["source_term"]
                self._term_canonical.setdefault(term, spec["canonical"])
                for pat in spec["variant_res"]:
                    if pat.search(translated_text):
                        counts = self._term_variants.setdefault(term, {})
                        counts[pat.pattern] = counts.get(pat.pattern, 0) + 1
                        break
            for acro, exp in (abbreviations or {}).items():
                if not acro or not re.search(rf"\b{re.escape(acro)}\b", source_text):
                    continue
                if re.search(rf"\b{re.escape(acro)}\b", translated_text):
                    counts = self._term_variants.setdefault(acro, {})
                    counts[acro] = counts.get(acro, 0) + 1
                    self._term_canonical.setdefault(acro, exp or acro)

    def conflicts(self) -> list[dict[str, Any]]:
        """Return terms or repeated source segments observed with conflicting translations."""
        with self._lock:
            out: list[dict[str, Any]] = []
            for term, counts in sorted(self._term_variants.items()):
                if len(counts) >= 2:
                    out.append(
                        {
                            "source_term": term,
                            "canonical": self._term_canonical.get(term, term),
                            "variants": sorted(counts.keys()),
                            "variant_counts": dict(counts),
                        }
                    )
            for norm_src, variants in sorted(self._source_conflicts.items()):
                if len(variants) >= 2:
                    out.append(
                        {
                            "source_term": norm_src[:100],
                            "canonical": sorted(variants)[0],
                            "variants": sorted(variants),
                        }
                    )
            return out

    @classmethod
    def is_eligible(cls, text: str) -> bool:
        norm = cls._normalize_key(text)
        if not norm or len(norm) > 600:
            return False
        if _INTERNAL_PLACEHOLDER_RE.search(norm):
            return False
        return bool(re.search(r"[A-Za-z\u0600-\u06FF]", norm))

    def lookup(
        self,
        source_text: str,
        *,
        lang_in: str = "",
        lang_out: str = "",
        engine_name: str = "",
    ) -> str | None:
        if not self.is_eligible(source_text):
            return None
        key = (
            (lang_in or "").lower(),
            (lang_out or "").lower(),
            (engine_name or "").lower(),
            self._normalize_key(source_text),
        )
        with self._lock:
            hit = self._entries.get(key)
            if hit is not None:
                self._hits += 1
            else:
                self._misses += 1
            return hit

    def store(
        self,
        source_text: str,
        translated_text: str,
        *,
        lang_in: str = "",
        lang_out: str = "",
        engine_name: str = "",
    ) -> None:
        if not self.is_eligible(source_text) or not translated_text:
            return
        norm_src = self._normalize_key(source_text)
        norm_tgt = translated_text.strip()
        if not norm_tgt or norm_tgt == norm_src:
            return
        if _INTERNAL_PLACEHOLDER_RE.search(norm_tgt):
            return
        key = (
            (lang_in or "").lower(),
            (lang_out or "").lower(),
            (engine_name or "").lower(),
            norm_src,
        )
        with self._lock:
            existing = self._entries.get(key)
            if existing is not None and existing.strip() != norm_tgt:
                self._source_conflicts.setdefault(norm_src, {existing.strip()}).add(norm_tgt)
            if len(self._entries) >= self._max_entries and key not in self._entries:
                return
            self._entries.setdefault(key, translated_text)

    def stats(self) -> dict[str, Any]:
        conflicts = self.conflicts()
        with self._lock:
            return {
                "entries": len(self._entries),
                "hits": self._hits,
                "misses": self._misses,
                "terminology_conflict_count": len(conflicts),
                "terminology_conflicts": conflicts,
            }


def apply_pre_glossary(
    text: str,
    domain: str | None = None,
    abbreviations: dict[str, str] | None = None,
) -> str:
    """Lock domain phrases in the English source before translation."""
    if not text or not _enabled():
        return text
    stripped = text.strip()
    if abbreviations and stripped in abbreviations:
        # Preserve document-defined acronyms verbatim unless handled below.
        if re.fullmatch(r"[A-Z][A-Z0-9]{1,9}", stripped):
            return stripped
    for pattern, replacement in _PRE_RULES:
        text = pattern.sub(replacement, text)
    for rule_domain, pattern, replacement in _GLOSSARY_PRE_RULES:
        if rule_domain == "any" or rule_domain == domain:
            text = pattern.sub(replacement, text)
    for pattern, replacement in _PRE_WHOLE_TEXT:
        if pattern.fullmatch(text):
            return replacement
    return text


def apply_post_glossary(
    text: str,
    source_text: str | None = None,
    domain: str | None = None,
    abbreviations: dict[str, str] | None = None,
) -> str:
    """Repair known mistranslations in the Arabic output."""
    if not text or not _enabled():
        return text
    for pattern, replacement in _POST_RULES:
        text = pattern.sub(replacement, text)
    if source_text:
        for src_pat, dst_pat, replacement in _GLOSSARY_POST_RULES:
            if src_pat.search(source_text):
                text = dst_pat.sub(replacement, text)
        if abbreviations:
            for acro in abbreviations:
                if len(acro) >= 3 and re.search(rf"\b{re.escape(acro)}\b", source_text):
                    # Normalize accidental title/lowercase Latin variant of document acronym
                    text = re.sub(
                        rf"\b{re.escape(acro.capitalize())}\b",
                        acro,
                        text,
                    )
        src_lower = source_text.lower()
        if "country programme document" in src_lower:
            text = re.sub(
                r'\bوثيقة\s+(?:برنامج\s+البلد|البلدان\s+البرنامج|برنامج\s+الدول)\b',
                'وثيقة البرنامج القطري',
                text,
            )
        if re.search(r'\bcountry\s+programme\b', src_lower):
            text = re.sub(
                _B + r'(?:البرنامج\s+الدولي|برنامج\s+البلد(?:ان)?|برنامج\s+الدول)\b',
                r'\1البرنامج القطري',
                text,
            )
        if re.search(r"\bexecutive\s+summary\b", src_lower):
            text = re.sub(_B + r"(?:الملخص|ملخص)\s+التنفيذي\b", r"\1الموجز التنفيذي", text)
        if re.search(r'\bprogramme\s+priorities\s+and\s+partnerships\b', src_lower):
            text = re.sub(_B + r'أولويات\s+البرنامج\s+والشركات\b', r'\1أولويات البرنامج والشراكات', text)
        if re.search(r'\bunicef\s+outcomes?\b', src_lower):
            text = re.sub(_B + r'(?:جزء|أجزاء)\s+من\s+اليونيسف\b', r'\1حصائل اليونيسف', text)
            text = re.sub(_B + r'نتائج\s+اليونيسف\b', r'\1حصائل اليونيسف', text)
        if re.search(r'\bvulnerable\b', src_lower) and (
            domain == "policy_un"
            or re.search(r'\b(?:children|groups|situations?|families|communities|populations?|people)\b', src_lower)
        ):
            # Arabic contracts the preposition ``لـ`` with the definite article:
            # ``لـ + الفئات`` becomes ``للفئات``.  Handle that common form before
            # the generic optional-prefix rules below.
            text = re.sub(
                r'(?<![\u0621-\u064A])للفئات\s+الضعيفة\b',
                'للفئات الأكثر هشاشة',
                text,
            )
            text = re.sub(_B + r'(?:الأوضاع|أوضاع)\s+الضعيفة\b', r'\1الأوضاع الهشّة', text)
            text = re.sub(_B + r'الفئات\s+الضعيفة\b', r'\1الفئات الأكثر هشاشة', text)
            text = re.sub(_B + r'الأطفال\s+الضعفاء\b', r'\1الأطفال الأكثر هشاشة', text)
        if re.search(r'\bmonitoring\b', src_lower) and (
            domain == "policy_un"
            or re.search(r'\b(?:programme|evaluation|reporting|framework|results?|unicef)\b', src_lower)
        ):
            text = re.sub(_B + r'المراقبة\s+والتقييم\b', r'\1الرصد والتقييم', text)
            text = re.sub(_B + r'المراقبة\s+والإبلاغ\b', r'\1الرصد والإبلاغ', text)
            text = re.sub(_B + r'إطار\s+المراقبة\b', r'\1إطار الرصد', text)
        if re.search(r'\breturnees?\b', src_lower):
            text = re.sub(_B + r'(?:المعتادين|العائدات)\b', r'\1العائدين', text)
        if re.search(r'\bearly\s+childhood\s+development\b', src_lower):
            text = re.sub(_B + r'تطوير\s+الطفولة\s+المبكرة\b', r'\1تنمية الطفولة المبكرة', text)
        if re.search(r'\bpre[\s-]primary\b', src_lower):
            text = re.sub(
                _B + r'(?:التعليم\s+)?(?:ما\s+)?قبل\s+(?:الأولي|الأولية|الأساسي)\b',
                r'\1التعليم ما قبل الابتدائي',
                text,
            )
        if re.search(r'\bwasting\b', src_lower) and (
            domain == "policy_un"
            or re.search(r'\b(?:nutrition|malnutrition|stunting|children|under[\s-]+(?:5|five)|acute|mics|unicef)\b', src_lower)
        ):
            text = re.sub(_B + r'(?:الإهدار|التبذير|النفايات)\b', r'\1الهزال', text)
        if re.search(r'\bstunting\b', src_lower) and (
            domain == "policy_un"
            or re.search(r'\b(?:nutrition|malnutrition|wasting|children|under[\s-]+(?:5|five)|mics|unicef)\b', src_lower)
        ):
            text = re.sub(_B + r'(?:التأخير|التوقف\s+عن\s+النمو|التعثر)\b', r'\1التقزّم', text)
        if re.search(r'\bunder[\s-]+(?:5|five)\b', src_lower):
            text = re.sub(r'(?:دون|تحت)\s+(?:سن\s+)?0\b', 'دون سن الخامسة', text)
        if re.search(r'\bdo\s+no\s+harm\b', src_lower):
            text = re.sub(
                _B + r'(?:لن\s+يفعل\s+أي\s+شيء\s+الضرر|لا\s+يفعل\s+أي\s+ضرر|عدم\s+فعل\s+الضرر)\b',
                r'\1عدم إلحاق الضرر',
                text,
            )
        if re.search(r'\bMICS\b', source_text or ""):
            text = re.sub(_B + r'(?:ميكس|مايكس|Mics)\b', r'\1MICS', text)
        if re.search(r'\bchild\s+welfare\s+commission\b', src_lower):
            text = re.sub(_B + r'(?:لجنة|مفوضية)\s+رعاية\s+(?:الطفل|الطفولة)\b', r'\1هيئة رعاية الطفولة', text)
        if re.search(r'\bibid\.?', src_lower):
            text = re.sub(r'(?:إيبيد|\(الـ\s*\(الـ\))', 'المرجع نفسه', text)
        if re.search(r'\bglossary\b', src_lower):
            text = re.sub(_B + r'(?:الجلسة|مسرد\s+الجلسة)\b', r'\1قائمة المصطلحات', text)
        if re.search(r'\bpayer\b', src_lower) and not re.search(r'\bpayee\b', src_lower):
            text = re.sub(_B + r'(?:المدفوع|الدافع\s+له)\b', r'\1الدافع', text)
        if (domain == "invoice_form" or re.search(r'\binvoice\b', src_lower)) and not re.search(r'\bnoti(?:ce|fication)\b', src_lower):
            text = re.sub(_B + r'الإشعار\b', r'\1الفاتورة', text)
        if (
            re.search(r'\bclearing\b', src_lower)
            and (domain == "financial_banking" or re.search(r'\b(?:clearing\s+house|ach|qatch|settlement|iban|payment|bank|cheque|check)\b', src_lower))
            and not re.search(r'\b(?:cache|forest|brush|land|throat)\b', src_lower)
        ):
            text = re.sub(_B + r'(?:التنظيف|تنظيف|التصفية|تصفية)\b', r'\1المقاصة', text)
        if re.search(r'\bcheck\s+digit\b', src_lower):
            text = re.sub(_B + r'(?:تولد\s+المال\s+التحقق|تحديدات\s+الاختبار)\b', r'\1رقم التحقق', text)
        if re.search(r'\bqty\b', src_lower):
            text = re.sub(_B + r'القيادة\b', r'\1الكمية', text)
        if re.search(r'\bequation\b', src_lower) and not re.search(r'\bequality\b', src_lower):
            text = re.sub(_B + r'(?:التساوي|المساواة)\b', r'\1المعادلة', text)
        if (
            re.search(r'\bagents?\b', src_lower)
            and re.search(r'\b(?:ai|llm|autonomous|software|multi-agent|tool|agentic|bot)\b', src_lower)
            and not re.search(r'\b(?:change|real\s+estate|travel|insurance)\b', src_lower)
        ):
            text = re.sub(_B + r'عامل\b', r'\1وكيل برمجي', text)
        if (
            re.search(r'\bstrings?\b', src_lower)
            and re.search(r'\b(?:utf-?8|ascii|unicode|text|character|char|code|parse|json|yaml|sql|bytes?|tokens?|regex|concatenat)\b', src_lower)
            and not re.search(r'\b(?:guitar|violin|piano|harp|orchestra|music|instrument)\b', src_lower)
        ):
            text = re.sub(_B + r'(?:أوتار|خيوط)\b', r'\1سلاسل نصية', text)
        if re.search(r'\bsalient\s+neurons?\b', src_lower):
            text = re.sub(_B + r'الخلايا\s+العصبية\s+الصلبة\b', r'\1الخلايا العصبية البارزة', text)
        if re.search(r'\binterjections?\b', src_lower):
            text = re.sub(_B + r'(?:التقاطعات|تقاطعات)\b', r'\1صيغ التعجب', text)
        if re.search(r'\bsyntax\b', src_lower) and "translation" not in src_lower:
            text = re.sub(_B + r'الترجمة\b', r'\1البنية النحوية', text)
    sci_active = (
        domain == "scientific_nlp"
        or (source_text is not None and bool(_SCIENTIFIC_CONTEXT.search(source_text)))
        or _abbreviations_indicate_scientific(abbreviations)
    )
    if sci_active:
        pos_def = (abbreviations or {}).get("POS", "").lower()
        pos_is_retail = any(k in pos_def for k in ("point of sale", "point-of-sale"))
        for pattern, replacement in _SCIENTIFIC_POST_RULES:
            if pos_is_retail and replacement == r'\1POS':
                continue
            text = pattern.sub(replacement, text)
    return text
