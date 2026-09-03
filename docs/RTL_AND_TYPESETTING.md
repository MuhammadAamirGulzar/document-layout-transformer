# RTL Layout & Typesetting Deep-Dive

## 1. The Core RTL Layout Challenge in PDFs
PDF is an absolute-coordinate display format: each character is placed at an explicit `(x, y)` position on the page. Unlike web browsers (CSS `direction: rtl`), PDF viewers have no concept of automatic text flow or RTL line breaking.

Translating a Left-to-Right (LTR) English PDF into Right-to-Left (RTL) Arabic requires custom geometry transformations at every layer:

```
LTR Layout (English):
[Left Margin] -> [Line 1: Word 1  Word 2  Word 3] -----------------> [Right Margin]
                 [Line 2: Word 4  Word 5]

RTL Layout (Arabic):
[Left Margin] <----------------- [Line 1: 3 كلمة  2 كلمة  1 كلمة] <- [Right Margin]
                                 [Line 2: 5 كلمة  4 كلمة]
```

---

## 2. The Per-Line BiDi Algorithm

### The Flawed Approach (Full Paragraph BiDi)
A naive approach runs the Unicode Bidirectional Algorithm (`bidi.algorithm.get_display`) on the entire translated paragraph string before laying it out:
```python
# BROKEN: Never do this on multi-line text!
full_reshaped = arabic_reshaper.reshape(paragraph.trans_unicode)
visual_text = bidi.algorithm.get_display(full_reshaped, base_dir='R')
```
**Why this breaks**: `get_display` inverts the entire character stream for a single continuous line. When the typesetting engine calculates character widths and breaks lines, words are sliced across lines in reverse order, and numbers or Latin words inside Arabic sentences get split or flipped across line boundaries.

### The Correct Per-Line BiDi Pipeline (`typesetting.py`)
1. **Glyph Shaping in Translator**:
   Only contextual character shaping (joining letters: Initial, Medial, Final, Isolated) is performed using `arabic_reshaper.reshape`. No BiDi level reordering is done here.
2. **Tokenization & Line Breaking**:
   `Typesetting._layout_typesetting_units` splits the paragraph into word tokens. It measures token widths against `effective_line_width` (accounting for margins and indents) and groups tokens into lines:
   ```python
   lines_tokens: list[list[TypesettingUnit]] = []
   ```
3. **Per-Line Visual Reordering**:
   For each line individually, `get_bidi_indices_and_chars(line_str, base_dir='R')` computes the exact visual character order for *that specific line*:
   ```python
   bidi_map = get_bidi_indices_and_chars(line_str, base_dir='R')
   visual_units = []
   for orig_idx, mirrored_char in bidi_map:
       if orig_idx < len(line):
           unit = copy.copy(line[orig_idx])
           if mirrored_char and unit.unicode and unit.unicode != mirrored_char:
               unit.unicode = mirrored_char
           visual_units.append(unit)
   ```
4. **Right-to-Left Placement**:
   The line's starting X coordinate is calculated from the right edge (`box.x2 - current_indent - visual_line_w`). Characters are then rendered from that point.

---

## 3. Bullet Points & Hanging Indents (Item 2)

In RTL documents, bullet points (•, -, numbering) must appear at the **far right edge** of the first line, and all subsequent wrapped lines of the same paragraph must be indented inward from the right margin.

```
[Left Margin]                                            [Right Margin]
              نص الفقرة السطر الأول يبدأ مباشرة بعد النقطة •
                 وهنا السطر الثاني يدخل للداخل بمسافة بادئة
                 وكذلك السطر الثالث يدخل للداخل بمسافة بادئة
```

### Implementation in `typesetting.py`
- **Detection**: Checks if the first token starts with bullet characters (`•`, `-`, `*`, `\u2022`, `\u25E6`, `\u25AA`, `\u25CF`), Microsoft Word Private Use Area (PUA) Wingdings bullets (`\uF0B7`, `\uF0A7`, `\uF076`, `\uF0D8`, or general PUA `\uE000-\uF8FF`), digits followed by a period (`1.`, `2.`), or if `layout_label` contains `list_item` or `bullet_item`.
- **Token Isolation**: Bullet points are split into dedicated standalone tokens during tokenization, preventing them from adhering or colliding with subsequent Arabic letters (e.g. preventing `ابتولديها` word corruptions).
- **Indent Calculation**:
  ```python
  bullet_indent = max(first_token_w + (space_width * 2.5), 14.0)
  ```
- **Line Wrapping**: Line 0 uses full width; lines `1+` subtract `bullet_indent` from available width.
- **Placement**:
  ```python
  if is_bullet_para and line_idx > 0:
      line_start_x = (box.x2 - bullet_indent) - visual_line_w
  else:
      line_start_x = box.x2 - visual_line_w
  ```

---

## 4. Running Headers, Footers & Page Numbers (Item 4)

In an LTR book or report, the document title is often top-left and the page number is top-right. In RTL, these positions must mirror across the page's vertical midline:

```
LTR Original:
[Header Title (Left)] ---------------------------------- [Page 12 (Right)]

RTL Mirrored:
[Page 12 (Left)] ---------------------------------- [Header Title (Right)]
```

### Implementation:
- `Typesetting._is_header_or_footer(paragraph, page)` detects paragraphs with labels `page_header`, `page_footer`, `page_number`, or bounding boxes within top/bottom 8% of the page.
- `Typesetting._calculate_rtl_margin_shift(paragraph, page)` computes:
  ```python
  shift_x = page_box.x + page_box.x2 - para_box.x2 - para_box.x
  ```
- Shift is strictly clamped to ensure content remains within safe margins (5% of page width) and never overlaps decorative page border rectangles or sidebars.

---

## 5. Chronological Timeline Axis Preservation (Item 9)

In charts (bar charts, line graphs) showing economic trends over time (e.g. `Jan 2021 Feb 2021 ... Dec 2021`), the chart graphics progress from left to right.
If the axis labels are flipped by the Arabic BiDi algorithm, the timeline would read `Dec 2021 ... Jan 2021` from left to right, reversing the meaning of the curves!

### Detection & Handling:
```python
months_pattern = r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|يناير|فبراير|مارس|أبريل|مايو|يونيو|يوليو|أغسطس|سبتمبر|أكتوبر|نوفمبر|ديسمبر|أوج)\b'
is_chronological_axis = len(re.findall(months_pattern, line_str, re.I)) >= 3

if is_chronological_axis:
    # Preserve LTR order matching the graphical chart curves
    visual_units = line
    line_start_x = box.x
```

---

## 6. Typography Hierarchy & Optical Font Scaling

### Font Family & Bold Weights (`fontmap.py` & `embedding_assets_metadata.py`)
- Configured `AR_FONT_FAMILY`:
  - `GoNotoKurrent-Regular.ttf` for regular text
  - `GoNotoKurrent-Bold.ttf` for bold text
- Weight mapping: If the original font has `is_bold=True` or its name contains `["bold", "black", "heavy", "semibold", "demibold"]`, it maps directly to `GoNotoKurrent-Bold.ttf`.

### Optical Scaling Factor (`1.08x`)
Noto Kurrent Arabic glyphs have lower ascender/cap heights relative to Latin typography at the same point size. Typesetting applies an optical compensation:
```python
optical_factor = 1.08 if self.is_arabic else 1.0
font_size = style.font_size * optical_factor
```

### Hierarchy Protection & Scale Floors
Body text scale is adjusted to fit paragraphs (`mode_scale`), but headings must not be shrunk:
- **Exempt from mode_scale**: Paragraphs with `title`, `heading`, `caption`, `table`, `footnote` are exempt from body text scale clamping.
- **Minimum Scale Floors**:
  - Titles / Headings: `scale >= 0.88`
  - Figure Captions: `scale >= 0.82`
  - Footnotes: `scale >= 0.75`
  - Table Cells: `scale >= 0.65`
  - Plain Text Body: `scale >= 0.72`

---

## 7. Isolated Latin Token & Table Cell LTR Protection (`is_isolated_latin`)

### Problem
In dense tables containing alphanumeric codes (such as the 54-country IBAN participants table or SWIFT code lists), table cells often contain isolated 2-letter ISO country codes (`AL`, `BA`, `CZ`, `GB`, `SA`, `AE`), 4-letter bank identifiers (`QNBA`, `CBQA`), or SWIFT BICs (`ABQQQAQAXXX`).

When `get_bidi_indices_and_chars(line_str, base_dir='R')` was executed across lines without Arabic context, BiDi level resolution under an RTL base direction treated neutral and boundary spaces in ways that reversed isolated 2-letter tokens (e.g. `BA` -> `A B`, `CZ` -> `Z C`, `GB` -> `B G`, `SA` -> `A S`, `AE` -> `E A`).

### Implementation in `typesetting.py`
```python
has_arabic_chars = any(
    (0x0600 <= ord(c) <= 0x06FF) or (0xFB50 <= ord(c) <= 0xFEFF)
    for c in line_str
)
is_isolated_latin = (not has_arabic_chars) and bool(
    re.match(r'^[A-Za-z0-9\s\-–—/._\(\)+:=#\$\€\£\¥\%\*]+$', line_str.strip())
)

if is_chronological_axis or is_isolated_latin:
    # Retain native LTR character order byte-for-byte
    visual_units = line
    line_start_x = box.x
elif line_str.strip():
    bidi_map = get_bidi_indices_and_chars(line_str, base_dir='R')
    ...
```
This guarantees:
1. Pure Latin codes, equations, and timelines never undergo RTL BiDi character flipping.
2. Table cell contents remain horizontally positioned at their expected column bounds.

