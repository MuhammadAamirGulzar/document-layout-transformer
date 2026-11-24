"""Checks that each engine stage lights up its OWN row on the page (B-36)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

PAGE = Path(__file__).resolve().parents[1] / "frontend/index.html"

STAGES = [
    {"name": "Parse PDF and Create Intermediate Representation", "display": "* Parsing PDF Structure"},
    {"name": "DetectScannedFile", "display": "* Detecting Scanned Content"},
    {"name": "Parse Page Layout", "display": "* Analyzing Page Layout"},
    {"name": "Parse Paragraphs", "display": "* Finding Paragraphs"},
    {"name": "Parse Formulas and Styles", "display": "* Processing Formulas & Styles"},
    {"name": "Translate Paragraphs", "display": "* Translating Content"},
    {"name": "Typesetting", "display": "* Typesetting Document"},
    {"name": "Add Fonts", "display": "* Mapping Fonts"},
    {"name": "Generate drawing instructions", "display": "* Drawing Instructions"},
    {"name": "Subset font", "display": "* Subsetting Fonts"},
    {"name": "Save PDF", "display": "* Saving Document"},
]
NAMES = [s["name"] for s in STAGES]
HARNESS = r"""
const stages = __STAGES__;
const rows = [];
const stagesContainer = { children: rows };
function build() {
  rows.length = 0;
  for (const s of stages) {
    const parts = {
      '.stage-name': { textContent: s.display },
      '.stage-icon': { textContent: '' },
      '.stage-status': { textContent: 'Pending' },
    };
    rows.push({ id: 'stage-' + s.name.replace(/\s+/g, '-'), className: 'stage-item pending', querySelector(sel) { return parts[sel]; }, parts });
  }
}
__FUNC__
const out = {};
build();
findAndUpdateStage('Translate Paragraphs', '* Translating Content', 'active');
out.t1 = rows.map(r => r.className);
build();
findAndUpdateStage('Translate Paragraphs', '', 'completed');
out.t2 = rows.map(r => [r.className, r.parts['.stage-status'].textContent]);
out.t3 = [];
for (const s of stages) { build(); findAndUpdateStage(s.name, '', 'active'); out.t3.push(rows.map(r => r.className)); }
build();
findAndUpdateStage('Totally Unknown', '* Mapping Fonts', 'active');
out.t4 = rows.map(r => r.className);
console.log(JSON.stringify(out));
"""


def _text():
    return PAGE.read_text(encoding="utf-8")


def _extract_function(text, name):
    start = text.index("function %s(" % name)
    i = text.index("{", start)
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[start:j + 1]
    raise ValueError("unbalanced braces")


def _run_node(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    js = HARNESS.replace("__STAGES__", json.dumps(STAGES)).replace(
        "__FUNC__", _extract_function(_text(), "findAndUpdateStage"))
    f = tmp_path / "harness.js"
    f.write_text(js, encoding="utf-8")
    p = subprocess.run([node, str(f)], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout.strip().splitlines()[-1])


def test_function_looks_up_the_row_by_stage_id():
    body = _extract_function(_text(), "findAndUpdateStage")
    assert "const exactId = " in body
    assert "'stage-'" in body


def test_exact_row_skips_the_fuzzy_conditions():
    body = _extract_function(_text(), "findAndUpdateStage")
    assert "stageEl === exactStageEl" in body


def test_translate_stage_lights_its_own_row(tmp_path):
    out = _run_node(tmp_path)
    own = NAMES.index("Translate Paragraphs")
    expected = ["stage-item pending"] * len(NAMES)
    expected[own] = "stage-item active"
    assert out["t1"] == expected


def test_completed_with_empty_display_name_marks_own_row(tmp_path):
    out = _run_node(tmp_path)
    own = NAMES.index("Translate Paragraphs")
    assert out["t2"][own] == ["stage-item completed", "Complete"]
    others = [r for i, r in enumerate(out["t2"]) if i != own]
    assert others == [["stage-item pending", "Pending"]] * (len(NAMES) - 1)


def test_every_stage_lights_only_its_own_row(tmp_path):
    out = _run_node(tmp_path)
    for i, row_classes in enumerate(out["t3"]):
        expected = ["stage-item pending"] * len(NAMES)
        expected[i] = "stage-item active"
        assert row_classes == expected, NAMES[i]


def test_unknown_stage_still_matches_by_display_name(tmp_path):
    out = _run_node(tmp_path)
    own = NAMES.index("Add Fonts")
    expected = ["stage-item pending"] * len(NAMES)
    expected[own] = "stage-item active"
    assert out["t4"] == expected
