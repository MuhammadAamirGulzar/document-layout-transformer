import logging
import random
import re
import statistics

import numpy as np

from babeldoc.babeldoc_exception.BabelDOCException import ExtractTextError
from babeldoc.format.pdf.document_il import Box
from babeldoc.format.pdf.document_il import Document
from babeldoc.format.pdf.document_il import Page
from babeldoc.format.pdf.document_il import PdfCharacter
from babeldoc.format.pdf.document_il import PdfLine
from babeldoc.format.pdf.document_il import PdfParagraph
from babeldoc.format.pdf.document_il import PdfParagraphComposition
from babeldoc.format.pdf.document_il import PdfRectangle
from babeldoc.format.pdf.document_il.utils.fontmap import FontMapper
from babeldoc.format.pdf.document_il.utils.formular_helper import (
    collect_page_formula_font_ids,
)
from babeldoc.format.pdf.document_il.utils.layout_helper import (
    HEIGHT_NOT_USFUL_CHAR_IN_CHAR,
)
from babeldoc.format.pdf.document_il.utils.layout_helper import SPACE_REGEX
from babeldoc.format.pdf.document_il.utils.layout_helper import Layout
from babeldoc.format.pdf.document_il.utils.layout_helper import add_space_dummy_chars
from babeldoc.format.pdf.document_il.utils.layout_helper import build_layout_index
from babeldoc.format.pdf.document_il.utils.layout_helper import calculate_iou_for_boxes
from babeldoc.format.pdf.document_il.utils.layout_helper import get_char_unicode_string
from babeldoc.format.pdf.document_il.utils.layout_helper import get_character_layout
from babeldoc.format.pdf.document_il.utils.layout_helper import is_bullet_point
from babeldoc.format.pdf.document_il.utils.layout_helper import is_bullet_or_list_marker
from babeldoc.format.pdf.document_il.utils.layout_helper import could_be_list_marker_start
from babeldoc.format.pdf.document_il.utils.layout_helper import (
    is_character_in_formula_layout,
)
from babeldoc.format.pdf.document_il.utils.layout_helper import is_text_layout
from babeldoc.format.pdf.document_il.utils.paragraph_helper import is_cid_paragraph
from babeldoc.format.pdf.document_il.utils.style_helper import INDIGO
from babeldoc.format.pdf.document_il.utils.style_helper import WHITE
from babeldoc.format.pdf.translation_config import TranslationConfig

logger = logging.getLogger(__name__)

# Base58 alphabet (Bitcoin style, without numbers 0, O, I, l)
BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def generate_base58_id(length: int = 5) -> str:
    """Generate a random base58 ID of specified length."""
    return "".join(random.choice(BASE58_ALPHABET) for _ in range(length))


def is_line_bold(line: PdfLine | None, font_dict: dict | None = None) -> bool:
    """Check if a line is predominantly bold text."""
    if not line or not line.pdf_character:
        return False
    bold_count = 0
    total_count = 0
    for char in line.pdf_character:
        if not char.char_unicode or char.char_unicode.isspace():
            continue
        total_count += 1
        font_id = getattr(char.pdf_style, "font_id", None) if char.pdf_style else None
        if font_dict and font_id in font_dict:
            f = font_dict[font_id]
            f_name = (getattr(f, "name", "") or "").lower()
            if getattr(f, "bold", False) or any(
                b in f_name for b in ["bold", "black", "heavy", "semibold", "demibold"]
            ):
                bold_count += 1
        elif char.pdf_style:
            f_id_lower = (str(font_id) if font_id else "").lower()
            if any(
                b in f_id_lower
                for b in ["bold", "black", "heavy", "semibold", "demibold"]
            ):
                bold_count += 1
    return total_count > 0 and (bold_count / total_count) >= 0.45


def get_line_dominant_font_size(line: PdfLine | None) -> float:
    """Get the median or dominant font size of non-space characters in a line."""
    if not line or not line.pdf_character:
        return 0.0
    sizes = [
        char.pdf_style.font_size
        for char in line.pdf_character
        if char.pdf_style
        and char.pdf_style.font_size
        and char.char_unicode
        and not char.char_unicode.isspace()
    ]
    if not sizes:
        return 0.0
    try:
        return statistics.median(sizes)
    except Exception:
        return sum(sizes) / len(sizes)


def is_para_bold(para: PdfParagraph, font_dict: dict | None = None) -> bool:
    """Check if the first line of a paragraph is bold."""
    if not para or not para.pdf_paragraph_composition:
        return False
    for comp in para.pdf_paragraph_composition:
        if comp.pdf_line:
            return is_line_bold(comp.pdf_line, font_dict)
        elif comp.pdf_character:
            dummy_line = PdfLine(pdf_character=[comp.pdf_character])
            return is_line_bold(dummy_line, font_dict)
    return False


class ParagraphFinder:
    stage_name = "Parse Paragraphs"

    # å®šä¹‰é¡¹ç›®ç¬¦å·çš„æ­£åˆ™è¡¨è¾¾å¼æ¨¡å¼

    def __init__(self, translation_config: TranslationConfig):
        self.translation_config = translation_config
        self.detailed_logger = None
        self.font_mapper = FontMapper(translation_config)

    def _preprocess_formula_layouts(self, page: Page):
        """
        Identifies 'formula' layouts that do not significantly overlap with any text layouts
        and re-labels them as 'isolate_formula'.
        """
        # Use a simplified Layout object for is_text_layout check
        text_layouts = [
            layout
            for layout in page.page_layout
            if is_text_layout(Layout(layout.id, layout.class_name))
        ]
        formula_layouts = [
            layout for layout in page.page_layout if layout.class_name == "formula"
        ]

        if not text_layouts or not formula_layouts:
            return

        for formula_layout in formula_layouts:
            is_isolated = True
            for text_layout in text_layouts:
                iou = calculate_iou_for_boxes(formula_layout.box, text_layout.box)
                if iou >= 0.5:
                    is_isolated = False
                    break

            if is_isolated:
                formula_layout.class_name = "isolate_formula"

    def add_text_fill_background(self, page: Page):
        layout_map = {layout.id: layout for layout in page.page_layout}
        for paragraph in page.pdf_paragraph:
            layout_id = paragraph.layout_id
            if layout_id is None:
                continue
            layout = layout_map[layout_id]
            if paragraph.box is None:
                continue
            x1, y1, x2, y2 = (
                paragraph.box.x,
                paragraph.box.y,
                paragraph.box.x2,
                paragraph.box.y2,
            )
            layout_box = layout.box
            if layout_box.x < x1:
                x1 = layout_box.x
            if layout_box.y < y1:
                y1 = layout_box.y
            if layout_box.x2 > x2:
                x2 = layout_box.x2
            if layout_box.y2 > y2:
                y2 = layout_box.y2
            assert x2 > x1 and y2 > y1
            page.pdf_rectangle.append(
                PdfRectangle(
                    box=Box(x1, y1, x2, y2),
                    fill_background=True,
                    graphic_state=WHITE,
                    debug_info=False,
                    xobj_id=paragraph.xobj_id,
                )
            )

    def update_paragraph_data(self, paragraph: PdfParagraph, update_unicode=False):
        if not paragraph.pdf_paragraph_composition:
            return

        chars = []
        for composition in paragraph.pdf_paragraph_composition:
            if composition.pdf_line:
                chars.extend(composition.pdf_line.pdf_character)
            elif composition.pdf_formula:
                chars.extend(composition.pdf_formula.pdf_character)
            elif composition.pdf_character:
                chars.append(composition.pdf_character)
            elif composition.pdf_same_style_unicode_characters:
                continue
            else:
                logger.error(
                    "Unexpected composition type"
                    " in PdfParagraphComposition. "
                    "This type only appears in the IL "
                    "after the translation is completed.",
                )
                continue

        if update_unicode and chars:
            paragraph.unicode = get_char_unicode_string(chars)
        if not chars:
            return
        # æ›´æ–°è¾¹ç•Œæ¡†
        min_x = min(char.visual_bbox.box.x for char in chars)
        min_y = min(char.visual_bbox.box.y for char in chars)
        max_x = max(char.visual_bbox.box.x2 for char in chars)
        max_y = max(char.visual_bbox.box.y2 for char in chars)
        paragraph.box = Box(min_x, min_y, max_x, max_y)
        paragraph.vertical = chars[0].vertical
        paragraph.xobj_id = chars[0].xobj_id

        paragraph.first_line_indent = False
        if (
            paragraph.pdf_paragraph_composition
            and paragraph.pdf_paragraph_composition[0].pdf_line
            and paragraph.pdf_paragraph_composition[0]
            .pdf_line.pdf_character[0]
            .visual_bbox.box.x
            - paragraph.box.x
            > 1
        ):
            paragraph.first_line_indent = True

    def update_line_data(self, line: PdfLine):
        min_x = min(char.visual_bbox.box.x for char in line.pdf_character)
        min_y = min(char.visual_bbox.box.y for char in line.pdf_character)
        max_x = max(char.visual_bbox.box.x2 for char in line.pdf_character)
        max_y = max(char.visual_bbox.box.y2 for char in line.pdf_character)
        line.box = Box(min_x, min_y, max_x, max_y)

    def add_debug_info(self, page: Page):
        if not self.translation_config.debug:
            return
        for paragraph in page.pdf_paragraph:
            for composition in paragraph.pdf_paragraph_composition:
                if composition.pdf_line:
                    line = composition.pdf_line
                    page.pdf_rectangle.append(
                        PdfRectangle(
                            box=line.box,
                            fill_background=False,
                            graphic_state=INDIGO,
                            debug_info=True,
                            line_width=0.2,
                        )
                    )

    def process(self, document):
        if self.detailed_logger:
            self.detailed_logger.log_step("Paragraph Finding Started")

        with self.translation_config.progress_monitor.stage_start(
            self.stage_name,
            len(document.page),
        ) as pbar:
            if not document.page:
                return
            for page in document.page:
                self.translation_config.raise_if_cancelled()
                self.process_page(page)
                pbar.advance()

            total_paragraph_count = 0
            for page in document.page:
                total_paragraph_count += len(page.pdf_paragraph)
            if total_paragraph_count == 0:
                raise ExtractTextError("The document contains no paragraphs.")

            if self.check_cid_paragraph(document):
                raise ExtractTextError("The document contains too many CID paragraphs.")
            
        for page_idx, page in enumerate(document.page):
            if self.detailed_logger and hasattr(page, 'pdf_paragraph'):
                for para_idx, para in enumerate(page.pdf_paragraph[:10]):  # First 10
                    para_info = {
                        'page': page_idx + 1,
                        'paragraph_id': para_idx + 1,
                        'text': para.unicode if hasattr(para, 'unicode') and para.unicode else '',
                        'char_count': len(para.unicode) if hasattr(para, 'unicode') and para.unicode else 0,
                        'layout_label': para.layout_label if hasattr(para, 'layout_label') else 'unknown',
                        'box': str(para.box) if hasattr(para, 'box') else 'N/A'
                    }
                    self.detailed_logger.log_step(
                        f"Paragraph Detected (Page {page_idx+1}, Para {para_idx+1})",
                        data=para_info
                    )

    def check_cid_paragraph(self, doc: Document):
        cid_para_count = 0
        para_total = 0
        for page in doc.page:
            para_total += len(page.pdf_paragraph)
            for para in page.pdf_paragraph:
                if is_cid_paragraph(para):
                    cid_para_count += 1
        return cid_para_count / para_total > 0.8

    def bbox_overlap(self, bbox1: Box, bbox2: Box) -> bool:
        return (
            bbox1.x < bbox2.x2
            and bbox1.x2 > bbox2.x
            and bbox1.y < bbox2.y2
            and bbox1.y2 > bbox2.y
        )

    def process_page(self, page: Page):
        layout_index, layout_map = build_layout_index(page)
        # é¢„å¤„ç†å…¬å¼å¸ƒå±€çš„æ ‡ç­¾
        self._preprocess_formula_layouts(page)

        # ç¬¬ä¸€æ­¥ï¼šæ ¹æ® layout åˆ›å»º paragraphs
        # åœ¨è¿™ä¸€æ­¥ä¸­ï¼Œpage.pdf_character ä¸­çš„å­—ç¬¦ä¼šè¢«ç§»é™¤
        paragraphs = self._group_characters_into_paragraphs(
            page, layout_index, layout_map
        )
        page.pdf_paragraph = paragraphs

        page_level_formula_font_ids, xobj_specific_formula_font_ids = (
            collect_page_formula_font_ids(
                page, self.translation_config.formular_font_pattern
            )
        )

        # for para in paragraphs:
        #     if not para.debug_id:
        #         continue
        #     new_line = PdfLine(
        #         pdf_character=[x.pdf_character for x in para.pdf_paragraph_composition]
        #     )
        #     self.update_line_data(new_line)
        #     para.pdf_paragraph_composition = [
        #         PdfParagraphComposition(pdf_line=new_line)
        #     ]

        # ç¬¬äºŒæ­¥ï¼šå°†æ®µè½å†…çš„å­—ç¬¦æ‹†åˆ†ä¸ºè¡Œ
        for paragraph in paragraphs:
            if (
                paragraph.xobj_id
                and paragraph.xobj_id in xobj_specific_formula_font_ids
            ):
                current_formula_font_ids = xobj_specific_formula_font_ids[
                    paragraph.xobj_id
                ]
            else:
                current_formula_font_ids = page_level_formula_font_ids
            self._split_paragraph_into_lines(paragraph, current_formula_font_ids)

        # ç¬¬ä¸‰æ­¥ï¼šå¤„ç†æ®µè½ä¸­çš„ç©ºæ ¼
        for paragraph in paragraphs:
            add_space_dummy_chars(paragraph)
            self.process_paragraph_spacing(paragraph)
            self.update_paragraph_data(paragraph)

        # ç¬¬å››æ­¥ï¼šè®¡ç®—æ‰€æœ‰è¡Œå®½åº¦çš„ä¸­ä½æ•°
        median_width = self.calculate_median_line_width(paragraphs)

        # ç¬¬äº”æ­¥ï¼šå¤„ç†ç‹¬ç«‹æ®µè½
        # Build font lookup dictionary for style inspection
        font_dict = {}
        if page.pdf_font:
            font_dict.update({f.font_id: f for f in page.pdf_font if f.font_id})
        if page.pdf_xobject:
            for xobj in page.pdf_xobject:
                if xobj.pdf_font:
                    font_dict.update({f.font_id: f for f in xobj.pdf_font if f.font_id})

        self.process_independent_paragraphs(paragraphs, median_width, font_dict=font_dict)

        # Fallback line detection intentionally favors small text regions. In a
        # ruled table that can collapse several adjacent cells into one line.
        # Recover the original cells from the table's vector grid before any
        # translation or RTL positioning occurs.
        self.reconstruct_wired_table_cells(page, paragraphs)

        # æ–°å¢žåŽå¤„ç†ï¼šåˆå¹¶å¸¦è¡Œå·äº¤æ›¿çš„æ­£æ–‡æ®µè½ï¼ˆa æ­£æ–‡ã€b è¡Œå·ã€c æ­£æ–‡ -> åˆå¹¶ a ä¸Ž cï¼Œä¿ç•™ bï¼‰
        if getattr(self.translation_config, "merge_alternating_line_numbers", True):
            self.merge_alternating_line_number_paragraphs(paragraphs)

        # Merge hyphenated words across lines and multi-line continuation fragments
        self.merge_hyphenated_and_continuation_paragraphs(paragraphs, font_dict=font_dict)
        # Some legacy PDFs draw a leading word separately from the remainder of
        # the same prose line (for example, ``All`` + ``banks are required``).
        # Repair it before translation so it cannot become an orphan Arabic word.
        self.merge_detached_leading_word_fragments(paragraphs)
        self.merge_same_line_sentence_fragments(paragraphs)

        # Some PDFs expose each visual line of a definition as an independent
        # paragraph, but the extraction order is not necessarily top-to-bottom.
        # Revisit aligned text fragments in geometric order before translation.
        self.coalesce_stacked_text_fragments(paragraphs, font_dict=font_dict)

        # Large source blocks sometimes contain several semantic records (for
        # example ``Step 3`` ... ``Step 4`` or prose + ``3.2 Validation`` +
        # prose).  Split those records on their original visual lines before
        # translation so MT and reflow cannot fold independent rows together.
        self.split_embedded_structured_lines(paragraphs)
        # Run once more after structural splitting: the split pass can expose
        # adjacent style runs that were previously hidden inside a larger
        # paragraph object.
        self.merge_same_line_sentence_fragments(paragraphs)

        # Splitting and fallback layout detection can temporarily make two
        # paragraphs reference the same PdfCharacter object. Rendering both is
        # the direct cause of black overprinted text. Establish one owner for
        # every extracted character before any paragraph is translated.
        self.purge_duplicate_character_ownership(paragraphs)

        # PDF content-stream order is not guaranteed to match visual reading
        # order. For an LTR source, sort every reconstructed line by geometry
        # before rebuilding paragraph Unicode. This repairs sequences such as
        # "th29 - th ... from the 5" at their source rather than with a phrase-
        # specific translation workaround.
        if "AR" not in (self.translation_config.lang_in or "").upper():
            for paragraph in paragraphs:
                for composition in paragraph.pdf_paragraph_composition or []:
                    if composition.pdf_line:
                        composition.pdf_line.pdf_character.sort(
                            key=self._get_char_sort_key
                        )
                        self.update_line_data(composition.pdf_line)

        for paragraph in paragraphs:
            fixed_table_box = (
                paragraph.box
                if "table_cell_fixed" in (paragraph.layout_label or "")
                else None
            )
            self.update_paragraph_data(paragraph, update_unicode=True)
            if fixed_table_box is not None:
                paragraph.box = fixed_table_box

        # Section labels in government and financial PDFs are not reliably
        # bold.  Treat a short numbered label ("3.2 Validation", "2. Scope")
        # as a heading even when font metadata is incomplete.  This gives the
        # typesetter a protected lane instead of allowing the following body
        # paragraph to reflow through the label.
        self.classify_numbered_section_headings(paragraphs)
        self.classify_structured_text_blocks(paragraphs)

        if self.translation_config.ocr_workaround:
            self.add_text_fill_background(page)
            # since this is ocr file,
            # image characters are not needed
            page.pdf_character = []

        self.fix_overlapping_paragraphs(page)

        # Run the same-line repair once more after role classification and
        # overlap normalization.  Those stages can expose fragments that were
        # previously hidden behind a broad title/body box.
        self.merge_detached_leading_word_fragments(paragraphs)
        self.merge_same_line_sentence_fragments(paragraphs)

        # ç¬¬å…­æ­¥ï¼šå¯¹æ¯ä¸€è¡Œçš„å­—ç¬¦è¿›è¡ŒæŽ’åº
        # self._sort_characters_in_lines(page)

        self.add_debug_info(page)

        # æ–°é˜¶æ®µï¼šè®¾ç½®æ®µè½çš„ renderorder ä¸ºæ‰€æœ‰ç»„æˆéƒ¨åˆ†ä¸­ renderorder æœ€å°çš„
        self._set_paragraph_render_order(page)

    def _set_paragraph_render_order(self, page: Page):
        """
        è®¾ç½®æ®µè½çš„ renderorder ä¸ºæ®µè½æ‰€æœ‰ç»„æˆéƒ¨åˆ†ä¸­ renderorder æœ€å°çš„å€¼
        """
        for paragraph in page.pdf_paragraph:
            min_render_order = 9999999999999999

            # éåŽ†æ®µè½çš„æ‰€æœ‰ç»„æˆéƒ¨åˆ†
            for composition in paragraph.pdf_paragraph_composition:
                # æ£€æŸ¥ PdfLine ä¸­çš„å­—ç¬¦
                if composition.pdf_line:
                    for char in composition.pdf_line.pdf_character:
                        if (
                            hasattr(char, "render_order")
                            and char.render_order is not None
                        ):
                            min_render_order = min(min_render_order, char.render_order)

                # æ£€æŸ¥å•ä¸ªå­—ç¬¦
                elif composition.pdf_character:
                    char = composition.pdf_character
                    if hasattr(char, "render_order") and char.render_order is not None:
                        min_render_order = min(min_render_order, char.render_order)

                # æ£€æŸ¥å…¬å¼ä¸­çš„å­—ç¬¦
                elif composition.pdf_formula:
                    for char in composition.pdf_formula.pdf_character:
                        if (
                            hasattr(char, "render_order")
                            and char.render_order is not None
                        ):
                            min_render_order = min(min_render_order, char.render_order)

            # å¦‚æžœæ‰¾åˆ°äº†æœ‰æ•ˆçš„ renderorderï¼Œè®¾ç½®æ®µè½çš„ renderorder
            if min_render_order != 9999999999999999:
                paragraph.render_order = min_render_order

    def is_isolated_formula(self, char: PdfCharacter):
        return char.char_unicode in (
            "(cid:122)",
            "(cid:123)",
            "(cid:124)",
            "(cid:125)",
        )

    def _paragraph_text_ascii(self, p: PdfParagraph) -> str:
        parts: list[str] = []
        for comp in p.pdf_paragraph_composition or []:
            if comp.pdf_line:
                for ch in comp.pdf_line.pdf_character or []:
                    if ch.char_unicode is not None:
                        parts.append(ch.char_unicode)
            elif comp.pdf_character and comp.pdf_character.char_unicode is not None:
                parts.append(comp.pdf_character.char_unicode)
        return "".join(parts)

    def _is_ascii_digit_or_space_paragraph(self, p: PdfParagraph) -> bool:
        text = self._paragraph_text_ascii(p)
        if not text:
            return True
        has_digit = False
        for c in text:
            if c.isdigit() and ord(c) < 128:
                has_digit = True
                continue
            if c.isspace():
                continue
            return False
        return True if has_digit or text.strip() == "" else False

    @staticmethod
    def _same_layout_and_xobj(a: PdfParagraph, c: PdfParagraph) -> bool:
        return (
            a.layout_id is not None
            and c.layout_id is not None
            and a.layout_id == c.layout_id
            and a.xobj_id is not None
            and c.xobj_id is not None
            and a.xobj_id == c.xobj_id
        )

    def merge_alternating_line_number_paragraphs(self, paragraphs: list[PdfParagraph]):
        # a ä»£è¡¨æ­£æ–‡
        # l ä»£è¡¨è¡Œå·
        if not paragraphs or len(paragraphs) < 3:
            return
        i = 0
        while i < len(paragraphs) - 2:
            a = paragraphs[i]
            # åžæŽ‰ä¸€ä¸ªæˆ–å¤šä¸ªè¿žç»­çš„è¡Œå·æ®µ l
            j = i + 1
            saw_l = False
            while j < len(paragraphs) and self._is_ascii_digit_or_space_paragraph(
                paragraphs[j]
            ):
                saw_l = True
                j += 1
            # çŽ°åœ¨ j æŒ‡å‘å€™é€‰çš„ c
            if saw_l and j < len(paragraphs):
                c = paragraphs[j]
                if self._same_layout_and_xobj(a, c):
                    a.pdf_paragraph_composition.extend(c.pdf_paragraph_composition)
                    self.update_paragraph_data(a)
                    del paragraphs[j]
                    # ä¸ç§»åŠ¨ iï¼Œç»§ç»­å°è¯•æŠŠæ›´å¤šæ­£æ–‡æŽ¥åˆ° aï¼Œå®žçŽ° a l+ a l+ a ... é“¾å¼åˆå¹¶
                    continue
            i += 1

    def coalesce_stacked_text_fragments(
        self,
        paragraphs: list[PdfParagraph],
        font_dict: dict | None = None,
    ) -> None:
        """Merge unambiguously contiguous body-text lines despite list order.

        This is intentionally narrower than generic paragraph reflow: tables,
        headings, bullets, and completed sentences are excluded.  It primarily
        repairs glossary/definition pages where every source line was emitted
        as a separate paragraph and therefore translated and laid out on the
        same target baseline.
        """
        if len(paragraphs) < 2:
            return

        self._coalesce_inline_text_fragments(paragraphs, font_dict)

        body_labels = {"paragraph", "plain text", "text", "body_text"}
        candidates = [
            paragraph
            for paragraph in paragraphs
            if paragraph.box
            and (paragraph.layout_label or "").lower() in body_labels
            and not is_para_bold(paragraph, font_dict)
        ]

        # Align only fragments within the same xobject and text column.  The
        # x bucket keeps unrelated left/right columns out of the same sequence.
        candidates.sort(
            key=lambda p: (
                getattr(p, "xobj_id", 0),
                round(p.box.x / 12.0),
                -p.box.y2,
            )
        )
        consumed: set[int] = set()
        for index, upper in enumerate(candidates):
            if id(upper) in consumed or upper not in paragraphs:
                continue
            for lower in candidates[index + 1 :]:
                if id(lower) in consumed or lower not in paragraphs:
                    continue
                if getattr(upper, "xobj_id", 0) != getattr(lower, "xobj_id", 0):
                    break
                if abs(upper.box.x - lower.box.x) > 14.0:
                    break

                upper_text = (upper.unicode or "").rstrip()
                lower_text = (lower.unicode or "").lstrip()
                gap = upper.box.y - lower.box.y2
                overlap = max(0.0, min(upper.box.x2, lower.box.x2) - max(upper.box.x, lower.box.x))
                aligned = overlap >= 0.60 * min(upper.box.x2 - upper.box.x, lower.box.x2 - lower.box.x)
                is_continuation = (
                    upper_text
                    and lower_text
                    and lower_text[0].islower()
                    and not upper_text.endswith((".", ":", "!", "?", "؛", ";"))
                    and aligned
                    and -5.0 <= gap <= 25.0
                )
                if not is_continuation:
                    # The next candidate belongs to a later independent block.
                    if gap > 25.0:
                        break
                    continue

                if not upper_text.endswith(" ") and not lower_text.startswith(" "):
                    from babeldoc.format.pdf.document_il.il_version_1 import VisualBbox
                    adjacent_chars = (
                        self._paragraph_source_characters(upper)
                        + self._paragraph_source_characters(lower)
                    )
                    space_style = next(
                        (char.pdf_style for char in adjacent_chars if char.pdf_style),
                        upper.pdf_style,
                    )
                    space_box = Box(upper.box.x2, lower.box.y2, upper.box.x2 + 3.0, lower.box.y2 + 10.0)
                    space_char = PdfCharacter(
                        char_unicode=" ", box=space_box, visual_bbox=VisualBbox(box=space_box),
                        vertical=False, xobj_id=getattr(upper, "xobj_id", 0),
                        pdf_style=space_style,
                    )
                    lower.pdf_paragraph_composition.insert(
                        0, PdfParagraphComposition(pdf_character=space_char)
                    )
                upper.pdf_paragraph_composition.extend(lower.pdf_paragraph_composition)
                self.update_paragraph_data(upper, update_unicode=True)
                paragraphs.remove(lower)
                consumed.add(id(lower))

    def _coalesce_inline_text_fragments(
        self,
        paragraphs: list[PdfParagraph],
        font_dict: dict | None = None,
    ) -> None:
        """Join body fragments split by a style boundary on one source line."""
        body_labels = {"paragraph", "plain text", "text", "body_text"}

        def lines(paragraph: PdfParagraph) -> list[PdfLine]:
            return [
                composition.pdf_line
                for composition in paragraph.pdf_paragraph_composition or []
                if composition.pdf_line and composition.pdf_line.pdf_character
            ]

        changed = True
        while changed:
            changed = False
            candidates = [
                paragraph
                for paragraph in paragraphs
                if paragraph.box
                and (paragraph.layout_label or "").lower() in body_labels
                and not is_para_bold(paragraph, font_dict)
                and lines(paragraph)
            ]
            for left in candidates:
                left_lines = lines(left)
                if not left_lines:
                    continue
                left_line = left_lines[-1]
                for right in candidates:
                    if right is left or right not in paragraphs:
                        continue
                    if getattr(left, "xobj_id", 0) != getattr(right, "xobj_id", 0):
                        continue
                    right_lines = lines(right)
                    if not right_lines:
                        continue
                    right_line = right_lines[0]
                    left_box = left_line.box
                    right_box = right_line.box
                    if not left_box or not right_box:
                        continue
                    vertical_overlap = max(
                        0.0,
                        min(left_box.y2, right_box.y2) - max(left_box.y, right_box.y),
                    )
                    min_height = max(
                        min(left_box.y2 - left_box.y, right_box.y2 - right_box.y),
                        1.0,
                    )
                    horizontal_gap = right_box.x - left_box.x2
                    right_text = get_char_unicode_string(right_line.pdf_character).lstrip()
                    if not (
                        vertical_overlap >= 0.70 * min_height
                        and -1.0 <= horizontal_gap <= 18.0
                        and right_text
                        and (right_text[0].islower() or right_text[0] in ",.;:)")
                    ):
                        continue

                    # Merge the physically adjacent runs into one PdfLine so
                    # translation sees one sentence and RTL layout gets one
                    # right-aligned box instead of two colliding boxes.
                    if (
                        left_line.pdf_character
                        and not get_char_unicode_string(left_line.pdf_character).endswith(" ")
                    ) and not get_char_unicode_string(right_line.pdf_character).startswith(" "):
                        from babeldoc.format.pdf.document_il.il_version_1 import VisualBbox

                        space_box = Box(
                            left_box.x2,
                            left_box.y,
                            max(left_box.x2, right_box.x),
                            left_box.y2,
                        )
                        left_line.pdf_character.append(
                            PdfCharacter(
                                char_unicode=" ",
                                box=space_box,
                                visual_bbox=VisualBbox(box=space_box),
                                vertical=False,
                                xobj_id=getattr(left, "xobj_id", 0),
                                pdf_style=left_line.pdf_character[-1].pdf_style,
                            )
                        )
                    left_line.pdf_character.extend(right_line.pdf_character)
                    self.update_line_data(left_line)

                    remaining = [
                        composition
                        for composition in right.pdf_paragraph_composition
                        if composition.pdf_line is not right_line
                    ]
                    if (
                        remaining
                        and right_line.pdf_character
                        and not get_char_unicode_string(right_line.pdf_character).endswith(" ")
                    ):
                        from babeldoc.format.pdf.document_il.il_version_1 import VisualBbox

                        separator_box = Box(
                            right_box.x2,
                            right_box.y,
                            right_box.x2 + 3.0,
                            right_box.y2,
                        )
                        left.pdf_paragraph_composition.append(
                            PdfParagraphComposition(pdf_character=PdfCharacter(
                                char_unicode=" ",
                                box=separator_box,
                                visual_bbox=VisualBbox(box=separator_box),
                                vertical=False,
                                xobj_id=getattr(left, "xobj_id", 0),
                                pdf_style=right_line.pdf_character[-1].pdf_style,
                            ))
                        )
                    left.pdf_paragraph_composition.extend(remaining)
                    self.update_paragraph_data(left, update_unicode=True)
                    paragraphs.remove(right)
                    changed = True
                    break
                if changed:
                    break

    @staticmethod
    def _paragraph_source_characters(paragraph: PdfParagraph) -> list[PdfCharacter]:
        characters: list[PdfCharacter] = []
        for composition in paragraph.pdf_paragraph_composition or []:
            if composition.pdf_line:
                characters.extend(composition.pdf_line.pdf_character or [])
            elif composition.pdf_character:
                characters.append(composition.pdf_character)
        return characters

    @staticmethod
    def _cluster_coordinates(values: list[float], tolerance: float = 2.0) -> list[float]:
        if not values:
            return []
        groups: list[list[float]] = []
        for value in sorted(values):
            if not groups or value - groups[-1][-1] > tolerance:
                groups.append([value])
            else:
                groups[-1].append(value)
        return [sum(group) / len(group) for group in groups]

    def reconstruct_wired_table_cells(
        self,
        page: Page,
        paragraphs: list[PdfParagraph],
    ) -> None:
        """Rebuild table cells from the vector grid retained by the source PDF.

        A ruled table normally has *continuous* vertical rules and separate
        horizontal rules for each row.  The old recovery path assumed every
        vertical rule was clipped to one row.  That is true for a few PDFs but
        makes a normal grid look like one giant row, so all translated cells
        are rendered at the top of the table.  Prefer the complete two-axis
        grid and retain that older vertical-only interpretation as a fallback
        for irregular/exported tables.

        The source vector grid remains in the PDF.  We only recover text cell
        ownership; therefore the physical cell boxes must not be mirrored for
        RTL output.
        """
        table_layouts = [
            layout
            for layout in page.page_layout or []
            if layout.box and (layout.class_name or "").lower() in {
                "table", "wired_table", "wireless_table"
            }
        ]
        if not table_layouts:
            return

        is_arabic = "AR" in (self.translation_config.lang_out or "").upper()
        for layout in table_layouts:
            table_box = layout.box
            vertical_rules: list[Box] = []
            horizontal_rules: list[Box] = []
            for curve in page.pdf_curve or []:
                box = curve.box
                if not box:
                    continue
                width = box.x2 - box.x
                height = box.y2 - box.y
                if (
                    width <= 2.0
                    and height >= 7.0
                    and box.x >= table_box.x - 2.0
                    and box.x2 <= table_box.x2 + 2.0
                    and box.y >= table_box.y - 2.0
                    and box.y2 <= table_box.y2 + 2.0
                ):
                    vertical_rules.append(box)
                if (
                    height <= 2.0
                    and width >= max(12.0, (table_box.x2 - table_box.x) * 0.35)
                    and box.x >= table_box.x - 2.0
                    and box.x2 <= table_box.x2 + 2.0
                    and box.y >= table_box.y - 2.0
                    and box.y2 <= table_box.y2 + 2.0
                ):
                    horizontal_rules.append(box)
            if len(vertical_rules) < 2:
                continue

            cell_rows: list[list[Box]] = []
            # Build rows from horizontal rules when they are available.  At a
            # row midpoint, only vertical rules crossing that midpoint are
            # boundaries.  This preserves merged header cells where an inner
            # vertical rule deliberately begins below the header.
            ys = self._cluster_coordinates(
                [(rule.y + rule.y2) / 2.0 for rule in horizontal_rules]
            )
            if len(ys) >= 2:
                for y, y2 in zip(ys, ys[1:]):
                    if y2 - y < 3.0:
                        continue
                    midpoint = (y + y2) / 2.0
                    xs = [table_box.x, table_box.x2]
                    for rule in vertical_rules:
                        if rule.y - 1.5 <= midpoint <= rule.y2 + 1.5:
                            xs.append((rule.x + rule.x2) / 2.0)
                    xs = self._cluster_coordinates(xs)
                    if len(xs) >= 2:
                        cell_rows.append(
                            [Box(xs[index], y, xs[index + 1], y2) for index in range(len(xs) - 1)]
                        )

            if not cell_rows:
                # Legacy fallback: some generators emit one independently
                # clipped set of vertical rules per row and no horizontal
                # strokes.  Continue supporting those tables.
                row_groups: list[list[Box]] = []
                for rule in sorted(vertical_rules, key=lambda b: (-b.y2, b.x)):
                    matched = next(
                        (
                            group
                            for group in row_groups
                            if abs(group[0].y - rule.y) <= 1.5
                            and abs(group[0].y2 - rule.y2) <= 1.5
                        ),
                        None,
                    )
                    if matched is None:
                        matched = []
                        row_groups.append(matched)
                    matched.append(rule)

                for rules in row_groups:
                    xs = self._cluster_coordinates(
                        [(rule.x + rule.x2) / 2.0 for rule in rules]
                    )
                    if len(xs) < 2:
                        continue
                    y = sum(rule.y for rule in rules) / len(rules)
                    y2 = sum(rule.y2 for rule in rules) / len(rules)
                    cell_rows.append(
                        [Box(xs[i], y, xs[i + 1], y2) for i in range(len(xs) - 1)]
                    )
            if not cell_rows:
                continue

            source_paragraphs = []
            owned: dict[int, tuple[PdfCharacter, int, int]] = {}
            for paragraph in list(paragraphs):
                chars = self._paragraph_source_characters(paragraph)
                if not chars:
                    continue
                assigned_count = 0
                paragraph_owned: dict[int, tuple[PdfCharacter, int, int]] = {}
                for char in chars:
                    char_box = char.visual_bbox.box if char.visual_bbox and char.visual_bbox.box else char.box
                    if not char_box:
                        continue
                    cx = (char_box.x + char_box.x2) / 2.0
                    cy = (char_box.y + char_box.y2) / 2.0
                    for row_index, cells in enumerate(cell_rows):
                        if cells[0].y - 1.0 <= cy <= cells[0].y2 + 1.0:
                            for cell_index, cell in enumerate(cells):
                                if cell.x - 1.0 <= cx <= cell.x2 + 1.0:
                                    paragraph_owned[id(char)] = (char, row_index, cell_index)
                                    assigned_count += 1
                                    break
                            break
                if assigned_count and assigned_count >= max(1, int(len(chars) * 0.8)):
                    source_paragraphs.append(paragraph)
                    owned.update(paragraph_owned)

            if not source_paragraphs:
                continue
            paragraphs[:] = [p for p in paragraphs if p not in source_paragraphs]

            for row_index, cells in enumerate(cell_rows):
                for cell_index, source_cell in enumerate(cells):
                    chars = [
                        item[0]
                        for item in owned.values()
                        if item[1] == row_index and item[2] == cell_index
                    ]
                    if not chars:
                        continue
                    # The original grid is part of the preserved base PDF
                    # operations. Keep its physical slots stable and apply RTL
                    # only inside each recovered cell. Reversing text boxes
                    # without also replacing the base vector grid creates width
                    # mismatches (the 21-character account column would inherit
                    # the narrow POSITION slot).
                    destination = source_cell
                    line_groups: list[list[PdfCharacter]] = []
                    for char in sorted(
                        chars,
                        key=lambda c: (
                            -(c.visual_bbox.box.y2 if c.visual_bbox and c.visual_bbox.box else c.box.y2),
                            c.visual_bbox.box.x if c.visual_bbox and c.visual_bbox.box else c.box.x,
                        ),
                    ):
                        char_box = char.visual_bbox.box if char.visual_bbox and char.visual_bbox.box else char.box
                        group = next(
                            (
                                group
                                for group in line_groups
                                if abs(
                                    (group[0].visual_bbox.box.y if group[0].visual_bbox and group[0].visual_bbox.box else group[0].box.y)
                                    - char_box.y
                                ) <= 2.0
                            ),
                            None,
                        )
                        if group is None:
                            line_groups.append([char])
                        else:
                            group.append(char)
                    compositions = []
                    for group in line_groups:
                        group.sort(key=lambda c: c.visual_bbox.box.x if c.visual_bbox and c.visual_bbox.box else c.box.x)
                        compositions.append(self.create_line(group))
                    destination_box = Box(
                        destination.x + 2.0,
                        destination.y + 1.0,
                        destination.x2 - 2.0,
                        destination.y2 - 1.0,
                    )
                    paragraph = PdfParagraph(
                        box=destination_box,
                        pdf_paragraph_composition=compositions,
                        layout_label="wired_table_cell_fixed_rtl" if is_arabic else "wired_table_cell",
                        xobj_id=getattr(source_paragraphs[0], "xobj_id", 0),
                        debug_id=generate_base58_id(),
                    )
                    self.update_paragraph_data(paragraph, update_unicode=True)
                    # update_paragraph_data derives a tight source-text box;
                    # restore the actual destination cell so wrapping and RTL
                    # alignment use the complete ruled cell.
                    paragraph.box = destination_box
                    paragraphs.append(paragraph)

    def _remove_trailing_hyphen(self, paragraph: PdfParagraph):
        """Remove trailing hyphen or soft-hyphen from paragraph composition."""
        for comp in reversed(paragraph.pdf_paragraph_composition or []):
            if comp.pdf_line and comp.pdf_line.pdf_character:
                chars = comp.pdf_line.pdf_character
                for idx in range(len(chars) - 1, -1, -1):
                    ch = chars[idx].char_unicode
                    if ch in ("-", "\u2010", "\u00ad"):
                        del chars[idx]
                        return
                    elif ch and not ch.isspace():
                        return
            elif comp.pdf_character:
                ch = comp.pdf_character.char_unicode
                if ch in ("-", "\u2010", "\u00ad"):
                    comp.pdf_character.char_unicode = ""
                    return
                elif ch and not ch.isspace():
                    return

    def merge_hyphenated_and_continuation_paragraphs(
        self,
        paragraphs: list[PdfParagraph],
        font_dict: dict | None = None,
    ):
        """
        Merge paragraphs that were severed mid-word (hyphenated line breaks) or
        split mid-sentence across fragmented lines.
        """
        if not paragraphs or len(paragraphs) < 2:
            return

        i = 0
        while i < len(paragraphs) - 1:
            a = paragraphs[i]
            b = paragraphs[i + 1]

            text_a = (a.unicode or "").rstrip()
            text_b = (b.unicode or "").lstrip()

            if not text_a or not text_b:
                i += 1
                continue

            # A parenthesized alphabetic marker is a real list item even when
            # its source font/extraction stream did not retain a distinct
            # bullet glyph.  Never concatenate ``(a)`` / ``(b)`` into the
            # preceding Arabic paragraph.
            if re.match(r"^\s*\([A-Za-z]\)\s+", text_b):
                i += 1
                continue

            # Style guard: never merge bold headings with regular body text
            is_a_bold = is_para_bold(a, font_dict)
            is_b_bold = is_para_bold(b, font_dict)
            if is_a_bold and not is_b_bold:
                i += 1
                continue

            # Check if b is a bullet point or list marker — never merge bullets into previous text
            first_chars_b = []
            if b.pdf_paragraph_composition:
                for comp in b.pdf_paragraph_composition:
                    if comp.pdf_line:
                        first_chars_b.extend(comp.pdf_line.pdf_character)
                    elif comp.pdf_character:
                        first_chars_b.append(comp.pdf_character)
                    if first_chars_b:
                        break
            if first_chars_b and is_bullet_or_list_marker(first_chars_b):
                i += 1
                continue

            box_a = a.box
            box_b = b.box
            if not box_a or not box_b:
                i += 1
                continue

            # Vertical distance: a is above b, gap should be reasonable line spacing (< 25pt)
            vert_gap = box_a.y - box_b.y2
            # Horizontal alignment: left edges aligned or significant horizontal overlap
            horiz_overlap = max(0.0, min(box_a.x2, box_b.x2) - max(box_a.x, box_b.x))
            same_column = (
                horiz_overlap > 0.25 * min(box_a.x2 - box_a.x, box_b.x2 - box_b.x)
                or abs(box_a.x - box_b.x) < 45.0
            )

            is_hyphen_split = (
                text_a.endswith(("-", "\u2010", "\u00ad"))
                and text_b[0].isalpha()
                and same_column
                and -5.0 <= vert_gap <= 25.0
            )

            is_lowercase_continuation = (
                text_b[0].islower()
                and not text_a.endswith((".", ":", "!", "?", "؛", ";"))
                and same_column
                and -5.0 <= vert_gap <= 25.0
            )

            if is_hyphen_split or is_lowercase_continuation:
                if is_hyphen_split:
                    self._remove_trailing_hyphen(a)
                elif is_lowercase_continuation and not text_a.endswith(" ") and not text_b.startswith(" "):
                    from babeldoc.format.pdf.document_il.il_version_1 import VisualBbox
                    adjacent_chars = (
                        self._paragraph_source_characters(a)
                        + self._paragraph_source_characters(b)
                    )
                    space_style = next(
                        (char.pdf_style for char in adjacent_chars if char.pdf_style),
                        a.pdf_style or b.pdf_style,
                    )
                    space_box = Box(box_a.x2, box_b.y2, box_a.x2 + 3.0, box_b.y2 + 10.0)
                    space_char = PdfCharacter(
                        char_unicode=" ",
                        box=space_box,
                        visual_bbox=VisualBbox(box=space_box),
                        vertical=False,
                        xobj_id=getattr(a, "xobj_id", 0),
                        pdf_style=space_style,
                    )
                    if b.pdf_paragraph_composition:
                        comp0 = b.pdf_paragraph_composition[0]
                        if comp0.pdf_line and comp0.pdf_line.pdf_character is not None:
                            comp0.pdf_line.pdf_character.insert(0, space_char)
                        elif comp0.pdf_character:
                            b.pdf_paragraph_composition.insert(0, PdfParagraphComposition(pdf_character=space_char))

                a.pdf_paragraph_composition.extend(b.pdf_paragraph_composition)
                self.update_paragraph_data(a, update_unicode=True)
                del paragraphs[i + 1]
                continue

            i += 1

    def _group_characters_into_paragraphs(
        self, page: Page, layout_index, layout_map
    ) -> list[PdfParagraph]:
        paragraphs: list[PdfParagraph] = []
        if page.pdf_paragraph:
            paragraphs.extend(page.pdf_paragraph)
            page.pdf_paragraph = []

        char_areas = [
            (char.visual_bbox.box.x2 - char.visual_bbox.box.x)
            * (char.visual_bbox.box.y2 - char.visual_bbox.box.y)
            for char in page.pdf_character
        ]
        median_char_area = 0.0
        if char_areas:
            char_areas.sort()
            mid = len(char_areas) // 2
            median_char_area = (
                char_areas[mid]
                if len(char_areas) % 2 == 1
                else (char_areas[mid - 1] + char_areas[mid]) / 2
            )

        current_paragraph: PdfParagraph | None = None
        current_layout: Layout | None = None
        skip_chars = []

        for char in page.pdf_character:
            char_layout = get_character_layout(char, layout_index, layout_map)
            # Check if character is in any formula layout and set formula_layout_id
            char.formula_layout_id = is_character_in_formula_layout(
                char, page, layout_index, layout_map
            )

            if not is_text_layout(char_layout) or self.is_isolated_formula(char):
                skip_chars.append(char)
                continue

            char_box = char.visual_bbox.box
            # char_pdf_box = char.box
            # if calculate_iou_for_boxes(char_box, char_pdf_box) < 0.2:
            #     char_box = char_pdf_box
            char_area = (char_box.x2 - char_box.x) * (char_box.y2 - char_box.y)
            is_small_char = char_area < median_char_area * 0.05

            is_new_paragraph = False
            if current_paragraph is None:
                is_new_paragraph = True
            elif (
                not (
                    is_small_char
                    and current_paragraph.pdf_paragraph_composition
                    and char_layout.id == current_layout.id
                )
                and char.char_unicode not in HEIGHT_NOT_USFUL_CHAR_IN_CHAR
            ):
                # Check if current character is directly touching or continuing the previous character (mid-word)
                prev_comp = (
                    current_paragraph.pdf_paragraph_composition[-1]
                    if current_paragraph.pdf_paragraph_composition
                    else None
                )
                prev_char = prev_comp.pdf_character if prev_comp else None
                is_mid_word = False
                if prev_char and prev_char.char_unicode and not SPACE_REGEX.match(prev_char.char_unicode):
                    curr_u = char.char_unicode or ""
                    if not SPACE_REGEX.match(curr_u):
                        prev_b = prev_char.visual_bbox.box
                        curr_b = char.visual_bbox.box
                        y_diff = abs(prev_b.y - curr_b.y)
                        avg_h = ((prev_b.y2 - prev_b.y) + (curr_b.y2 - curr_b.y)) / 2.0
                        x_gap = curr_b.x - prev_b.x2
                        # Same reading line and adjacent character in the same word
                        if y_diff <= (avg_h * 0.45) and -4.0 <= x_gap <= max(avg_h * 0.45, 4.0):
                            is_mid_word = True

                if not is_mid_word and (
                    (
                        char_layout.id != current_layout.id
                        and not SPACE_REGEX.match(char.char_unicode)
                    )
                    or (  # not same xobject
                        current_paragraph.pdf_paragraph_composition
                        and current_paragraph.pdf_paragraph_composition[
                            -1
                        ].pdf_character.xobj_id
                        != char.xobj_id
                    )
                    or (
                        (is_bullet_point(char) or could_be_list_marker_start(char))
                        and not current_paragraph.pdf_paragraph_composition
                    )
                ):
                    is_new_paragraph = True

            if is_new_paragraph:
                current_layout = char_layout
                current_paragraph = PdfParagraph(
                    pdf_paragraph_composition=[],
                    layout_id=current_layout.id,
                    debug_id=generate_base58_id(),
                    layout_label=current_layout.name,
                )
                paragraphs.append(current_paragraph)

            current_paragraph.pdf_paragraph_composition.append(
                PdfParagraphComposition(pdf_character=char)
            )

        page.pdf_character = skip_chars
        for para in paragraphs:
            self.update_paragraph_data(para)
        return paragraphs

    def _merge_overlapping_clusters(
        self, lines: dict[int, list[PdfCharacter]], char_height_average: float
    ) -> dict[int, list[PdfCharacter]]:
        """
        Merge clusters that have significant y-axis overlap.
        If y_intersection / min_height > 0.5 or the distance between y-midlines is less than char_height_average, merge the two clusters.
        """
        if len(lines) <= 1:
            return lines

        # Calculate y-axis ranges for each cluster
        cluster_ranges = {}
        cluster_midlines = {}
        for label, chars in lines.items():
            y_values = [char.visual_bbox.box.y for char in chars] + [
                char.visual_bbox.box.y2 for char in chars
            ]
            y_min, y_max = min(y_values), max(y_values)
            cluster_ranges[label] = (y_min, y_max)
            cluster_midlines[label] = (y_min + y_max) / 2

        # Keep merging until no more merges are possible
        changed = True
        while changed:
            changed = False
            labels_to_check = list(lines.keys())

            for i in range(len(labels_to_check)):
                if not changed:  # Only continue if no merge happened in this iteration
                    for j in range(i + 1, len(labels_to_check)):
                        label1, label2 = labels_to_check[i], labels_to_check[j]

                        # Skip if either label has been merged away
                        if label1 not in lines or label2 not in lines:
                            continue

                        y1_min, y1_max = cluster_ranges[label1]
                        y2_min, y2_max = cluster_ranges[label2]

                        # Calculate intersection
                        intersection_start = max(y1_min, y2_min)
                        intersection_end = min(y1_max, y2_max)

                        # Calculate midline distance
                        midline_distance = abs(
                            cluster_midlines[label1] - cluster_midlines[label2]
                        )

                        should_merge = False
                        if (
                            intersection_end > intersection_start
                        ):  # There is intersection
                            intersection_height = intersection_end - intersection_start
                            height1 = y1_max - y1_min
                            height2 = y2_max - y2_min
                            min_height = min(height1, height2)

                            # Check if intersection ratio exceeds threshold
                            if (
                                min_height > 0
                                and intersection_height / min_height > 0.3
                            ):
                                should_merge = True

                        # Check if midline distance is less than char_height_average
                        if midline_distance < char_height_average:
                            should_merge = True

                        if should_merge:
                            # Merge label2 into label1
                            lines[label1].extend(lines[label2])
                            del lines[label2]

                            # Update cluster range and midline for the merged cluster
                            new_y_min = min(y1_min, y2_min)
                            new_y_max = max(y1_max, y2_max)
                            cluster_ranges[label1] = (new_y_min, new_y_max)
                            cluster_midlines[label1] = (new_y_min + new_y_max) / 2
                            del cluster_ranges[label2]
                            del cluster_midlines[label2]

                            changed = True
                            break

        return lines

    def _get_effective_y_bounds(self, char: PdfCharacter) -> tuple[float, float]:
        """
        Determines the effective vertical boundaries (y1, y2) for a character.

        It prioritizes the visual bounding box if its Intersection over Union (IoU)
        with the PDF bounding box is high (>= 0.5), otherwise, it falls back to the
        PDF bounding box. This helps use more accurate layout information when available.
        """
        visual_box = char.visual_bbox.box
        return visual_box.y, visual_box.y2
        pdf_box = char.box
        if calculate_iou_for_boxes(visual_box, pdf_box) >= 0.5:
            return visual_box.y, visual_box.y2
        return pdf_box.y, pdf_box.y2

    @staticmethod
    def _compute_collision_counts_histogram(
        y1_arr: np.ndarray,
        y2_arr: np.ndarray,
        para_y_min: float,
        para_y_max: float,
        step: float,
    ) -> np.ndarray:
        """Compute overlap counts at each scan line using a difference-array histogram.

        Args:
            y1_arr: 1-D array with lower y bounds of characters (inclusive).
            y2_arr: 1-D array with upper y bounds of characters (exclusive).
            para_y_min: Minimum y of the paragraph.
            para_y_max: Maximum y of the paragraph.
            step: Scan step size.

        Returns:
            1-D NumPy int32 array where index i corresponds to y = para_y_max - i Ã— step.
        """
        # Number of scan positions
        m = int(np.ceil((para_y_max - para_y_min) / step))
        if m <= 0:
            return np.array([], dtype=np.int32)

        # Map character bounds to discrete indices (top inclusive, bottom exclusive)
        starts = np.floor((para_y_max - y2_arr) / step).astype(np.int32)
        ends = np.floor((para_y_max - y1_arr) / step).astype(np.int32) + 1
        # Clip ends to the valid range [0, m]
        np.clip(ends, 0, m, out=ends)

        hist = np.zeros(m + 1, dtype=np.int32)
        np.add.at(hist, starts, 1)
        np.add.at(hist, ends, -1)

        return np.cumsum(hist[:-1])

    def _split_paragraph_into_lines(
        self, paragraph: PdfParagraph, formula_font_ids: set[str]
    ):
        """
        Splits a paragraph into lines using a "line-threading" method.

        This method works by scanning vertically across the paragraph's bounding
        box and counting how many characters intersect with a horizontal line
        at each y-coordinate. The regions with a low number of intersections
        (less than 2) are identified as gaps between lines. The characters
        are then partitioned into lines based on these identified gaps.
        """
        if not paragraph.pdf_paragraph_composition:
            return

        # 1. Extract all characters and other compositions from the paragraph.
        all_chars: list[PdfCharacter] = []
        other_compositions: list[PdfParagraphComposition] = []
        for comp in paragraph.pdf_paragraph_composition:
            if comp.pdf_character:
                all_chars.append(comp.pdf_character)
            else:
                other_compositions.append(comp)

        if not all_chars:
            return

        # 2. Determine effective y-bounds for each character and the paragraph's total vertical range.
        char_y_bounds = [
            {"char": char, "y1": y1, "y2": y2}
            for char in all_chars
            for y1, y2 in [self._get_effective_y_bounds(char)]
        ]

        if not char_y_bounds:
            paragraph.pdf_paragraph_composition = other_compositions
            self.update_paragraph_data(paragraph)
            return

        para_y_min = min(b["y1"] for b in char_y_bounds)
        para_y_max = max(b["y2"] for b in char_y_bounds)

        # If the paragraph is vertically flat, treat it as a single line.
        if (para_y_max - para_y_min) < 5:  # Using a small threshold
            # all_chars.sort(key=lambda c: c.visual_bbox.box.x)
            single_line_composition = self.create_line(all_chars)
            paragraph.pdf_paragraph_composition = [
                single_line_composition
            ] + other_compositions
            self.update_paragraph_data(paragraph)
            return

        # 3. Perform "threading" scan to create a collision histogram.
        # Scan from top (max y) to bottom (min y) with a step of 0.5.
        scan_y_min = para_y_min
        scan_y_max = para_y_max
        step = 0.25

        y_coordinates = np.arange(scan_y_max, scan_y_min, -step)

        # Compute collision counts using NumPy histogram (O(m + n))
        y1_arr = np.array([b["y1"] for b in char_y_bounds], dtype=np.float32)
        y2_arr = np.array([b["y2"] for b in char_y_bounds], dtype=np.float32)
        collision_counts = self._compute_collision_counts_histogram(
            y1_arr,
            y2_arr,
            scan_y_min,
            scan_y_max,
            step,
        )

        # 4. Find gaps (regions with low collision count) from the histogram.
        gaps = []
        in_gap = False
        for i, count in enumerate(collision_counts):
            if count < 1 and not in_gap:
                in_gap = True
                gap_start_index = i
            elif count >= 1 and in_gap:
                in_gap = False
                gaps.append((gap_start_index, i - 1))
        if in_gap:
            gaps.append((gap_start_index, len(collision_counts) - 1))

        # If no significant gaps are found, treat it as a single line.
        if not gaps:
            # all_chars.sort(key=lambda c: c.visual_bbox.box.x)
            single_line_composition = self.create_line(all_chars)
            paragraph.pdf_paragraph_composition = [
                single_line_composition
            ] + other_compositions
            self.update_paragraph_data(paragraph)
            return

        # 5. Assign characters to lines based on the identified gaps.
        # Calculate separator y-coordinates from the midpoints of the gaps.
        separator_y_coords = sorted(
            [y_coordinates[start_idx] for start_idx, end_idx in gaps],
            reverse=True,
        )

        lines: list[list[PdfCharacter]] = [
            [] for _ in range(len(separator_y_coords) + 1)
        ]

        for b in char_y_bounds:
            char_y_center = (b["y1"] + b["y2"]) / 2
            line_idx = 0
            # Find which line bucket the character belongs to.
            for sep_y in separator_y_coords:
                if char_y_center > sep_y:
                    break
                line_idx += 1
            lines[line_idx].append(b["char"])

        # 6. Rebuild the paragraph's composition list from the new lines.
        new_line_compositions = []
        for line_chars in lines:
            if line_chars:
                # Sort characters within each line by x-coordinate (left-to-right).
                # line_chars.sort(key=lambda c: c.visual_bbox.box.x)
                new_line_compositions.append(self.create_line(line_chars))

        # The lines are already sorted vertically due to the scanning process.
        paragraph.pdf_paragraph_composition = new_line_compositions + other_compositions
        self.update_paragraph_data(paragraph)

    def process_paragraph_spacing(self, paragraph: PdfParagraph):
        if not paragraph.pdf_paragraph_composition:
            return

        # å¤„ç†è¡Œçº§åˆ«çš„ç©ºæ ¼
        processed_lines = []
        for composition in paragraph.pdf_paragraph_composition:
            if not composition.pdf_line:
                processed_lines.append(composition)
                continue

            line = composition.pdf_line
            if not "".join(
                x.char_unicode for x in line.pdf_character
            ).strip():  # è·³è¿‡å®Œå…¨ç©ºç™½çš„è¡Œ
                continue

            # å¤„ç†è¡Œå†…å­—ç¬¦çš„å°¾éšç©ºæ ¼
            processed_chars = []
            for char in line.pdf_character:
                if not char.char_unicode.isspace():
                    processed_chars = processed_chars + [char]
                elif processed_chars:  # åªæœ‰åœ¨æœ‰éžç©ºæ ¼å­—ç¬¦åŽæ‰è€ƒè™‘ä¿ç•™ç©ºæ ¼
                    processed_chars.append(char)

            # ç§»é™¤å°¾éšç©ºæ ¼
            while processed_chars and processed_chars[-1].char_unicode.isspace():
                processed_chars.pop()

            if processed_chars:  # å¦‚æžœè¡Œå†…è¿˜æœ‰å­—ç¬¦
                line = self.create_line(processed_chars)
                processed_lines.append(line)

        paragraph.pdf_paragraph_composition = processed_lines
        self.update_paragraph_data(paragraph)

    def create_line(self, chars: list[PdfCharacter]) -> PdfParagraphComposition:
        assert chars

        line = PdfLine(pdf_character=chars)
        self.update_line_data(line)
        return PdfParagraphComposition(pdf_line=line)

    def calculate_median_line_width(self, paragraphs: list[PdfParagraph]) -> float:
        # æ”¶é›†æ‰€æœ‰è¡Œçš„å®½åº¦
        line_widths = []
        for paragraph in paragraphs:
            for composition in paragraph.pdf_paragraph_composition:
                if composition.pdf_line:
                    line = composition.pdf_line
                    line_widths.append(line.box.x2 - line.box.x)

        if not line_widths:
            return 0.0

        # è®¡ç®—ä¸­ä½æ•°
        line_widths.sort()
        mid = len(line_widths) // 2
        if len(line_widths) % 2 == 0:
            return (line_widths[mid - 1] + line_widths[mid]) / 2
        return line_widths[mid]

    def purge_duplicate_character_ownership(self, paragraphs: list[PdfParagraph]) -> None:
        """Ensure each extracted character belongs to exactly one paragraph.

        This is intentionally identity-based: repeated letters in the document
        remain valid, while a ghost paragraph holding a second reference to the
        *same* extracted PdfCharacter is removed before it can be typeset twice.
        """
        owned_character_ids: set[int] = set()
        empty_paragraphs: list[PdfParagraph] = []

        for paragraph in paragraphs:
            retained_compositions: list[PdfParagraphComposition] = []
            for composition in paragraph.pdf_paragraph_composition or []:
                if composition.pdf_line:
                    unique_chars = [
                        char
                        for char in composition.pdf_line.pdf_character
                        if id(char) not in owned_character_ids
                    ]
                    owned_character_ids.update(id(char) for char in unique_chars)
                    if unique_chars:
                        if len(unique_chars) == len(composition.pdf_line.pdf_character):
                            retained_compositions.append(composition)
                        else:
                            retained_compositions.append(self.create_line(unique_chars))
                elif composition.pdf_character:
                    if id(composition.pdf_character) not in owned_character_ids:
                        owned_character_ids.add(id(composition.pdf_character))
                        retained_compositions.append(composition)
                else:
                    # Formula and same-style compositions are not source
                    # character ownership candidates at this pipeline stage.
                    retained_compositions.append(composition)

            paragraph.pdf_paragraph_composition = retained_compositions
            if not retained_compositions:
                empty_paragraphs.append(paragraph)

        for paragraph in empty_paragraphs:
            paragraphs.remove(paragraph)
            logger.debug(
                "Removed ghost paragraph %s after duplicate character ownership purge.",
                paragraph.debug_id,
            )

    def process_independent_paragraphs(
        self,
        paragraphs: list[PdfParagraph],
        median_width: float,
        font_dict: dict | None = None,
    ):
        i = 0
        while i < len(paragraphs):
            paragraph = paragraphs[i]
            if len(paragraph.pdf_paragraph_composition) <= 1:  # è·³è¿‡åªæœ‰ä¸€è¡Œçš„æ®µè½
                i += 1
                continue

            j = 1
            while j < len(paragraph.pdf_paragraph_composition):
                prev_composition = paragraph.pdf_paragraph_composition[j - 1]
                if not prev_composition.pdf_line:
                    j += 1
                    continue

                prev_line = prev_composition.pdf_line
                prev_width = prev_line.box.x2 - prev_line.box.x
                prev_text = "".join([c.char_unicode for c in prev_line.pdf_character])

                # æ£€æŸ¥æ˜¯å¦åŒ…å«è¿žç»­çš„ç‚¹ï¼ˆè‡³å°‘ 20 ä¸ªï¼‰
                # å¦‚æžœæœ‰è‡³å°‘è¿žç»­ 20 ä¸ªç‚¹ï¼Œåˆ™ä»£è¡¨è¿™æ˜¯ç›®å½•æ¡ç›®
                if re.search(r"\.{20,}", prev_text):
                    # åˆ›å»ºæ–°çš„æ®µè½
                    new_paragraph = PdfParagraph(
                        box=Box(0, 0, 0, 0),  # ä¸´æ—¶è¾¹ç•Œæ¡†
                        pdf_paragraph_composition=(
                            paragraph.pdf_paragraph_composition[j:]
                        ),
                        unicode="",
                        debug_id=generate_base58_id(),
                        layout_label=paragraph.layout_label,
                        layout_id=paragraph.layout_id,
                    )
                    # æ›´æ–°åŽŸæ®µè½
                    paragraph.pdf_paragraph_composition = (
                        paragraph.pdf_paragraph_composition[:j]
                    )

                    # æ›´æ–°ä¸¤ä¸ªæ®µè½çš„æ•°æ®
                    self.update_paragraph_data(paragraph, update_unicode=True)
                    self.update_paragraph_data(new_paragraph, update_unicode=True)

                    # åœ¨åŽŸæ®µè½åŽæ’å…¥æ–°æ®µè½
                    paragraphs.insert(i + 1, new_paragraph)
                    break

                # å¦‚æžœå‰ä¸€è¡Œå®½åº¦å°äºŽä¸­ä½æ•°çš„ä¸€åŠï¼Œå°†å½“å‰è¡ŒåŠåŽç»­è¡Œåˆ†å‰²æˆæ–°æ®µè½
                # Check if previous line ends with a hyphen or current line continues with lowercase:
                # NEVER split across hyphenated word or mid-sentence continuation
                current_chars = []
                if (
                    paragraph.pdf_paragraph_composition
                    and j < len(paragraph.pdf_paragraph_composition)
                    and (curr_comp := paragraph.pdf_paragraph_composition[j])
                    and curr_comp.pdf_line
                ):
                    current_chars = curr_comp.pdf_line.pdf_character

                first_char = next(
                    (c.char_unicode for c in current_chars if not c.char_unicode.isspace()), ""
                )
                is_bullet = bool(current_chars and is_bullet_or_list_marker(current_chars))

                curr_line = curr_comp.pdf_line if curr_comp and curr_comp.pdf_line else None
                prev_is_bold = is_line_bold(prev_line, font_dict)
                curr_is_bold = is_line_bold(curr_line, font_dict) if curr_line else False

                prev_size = get_line_dominant_font_size(prev_line)
                curr_size = get_line_dominant_font_size(curr_line) if curr_line else 0.0

                is_style_boundary = (
                    (prev_is_bold and not curr_is_bold)
                    or (prev_size >= curr_size + 1.5 and curr_size > 0.0)
                )

                is_hyphen_or_continuation = (
                    not is_style_boundary
                    and (
                        prev_text.rstrip().endswith(("-", "\u2010", "\u00ad"))
                        or (first_char and first_char.isalpha() and first_char.islower() and not is_bullet)
                    )
                )

                if is_hyphen_or_continuation:
                    j += 1
                    continue

                should_split = (
                    is_style_boundary
                    or (
                        self.translation_config.split_short_lines
                        and prev_width
                        < median_width * self.translation_config.short_line_split_factor
                    )
                    or is_bullet
                )

                if should_split:
                    if is_style_boundary and prev_is_bold:
                        paragraph.layout_label = "heading"
                    # åˆ›å»ºæ–°çš„æ®µè½
                    new_paragraph = PdfParagraph(
                        box=Box(0, 0, 0, 0),  # ä¸´æ—¶è¾¹ç•Œæ¡†
                        pdf_paragraph_composition=(
                            paragraph.pdf_paragraph_composition[j:]
                        ),
                        unicode="",
                        debug_id=generate_base58_id(),
                        layout_label="paragraph" if is_style_boundary else paragraph.layout_label,
                        layout_id=paragraph.layout_id,
                    )
                    # æ›´æ–°åŽŸæ®µè½
                    paragraph.pdf_paragraph_composition = (
                        paragraph.pdf_paragraph_composition[:j]
                    )

                    # æ›´æ–°ä¸¤ä¸ªæ®µè½çš„æ•°æ®
                    self.update_paragraph_data(paragraph, update_unicode=True)
                    self.update_paragraph_data(new_paragraph, update_unicode=True)

                    # åœ¨åŽŸæ®µè½åŽæ’å…¥æ–°æ®µè½
                    paragraphs.insert(i + 1, new_paragraph)
                    break
                j += 1
            i += 1

    @staticmethod
    def is_bbox_contain_in_vertical(bbox1: Box, bbox2: Box) -> bool:
        """Check if one bounding box is completely contained within the other."""
        # Check if bbox1 is contained in bbox2
        bbox1_in_bbox2 = bbox1.y >= bbox2.y and bbox1.y2 <= bbox2.y2
        # Check if bbox2 is contained in bbox1
        bbox2_in_bbox1 = bbox2.y >= bbox1.y and bbox2.y2 <= bbox1.y2
        return bbox1_in_bbox2 or bbox2_in_bbox1

    def fix_overlapping_paragraphs(self, page: Page):
        """
        Adjusts the bounding boxes of paragraphs on a page to resolve vertical overlaps.

        Iteratively checks pairs of paragraphs and adjusts their vertical boundaries
        (y and y2) if they overlap, aiming to place the boundary at the midpoint
        of the vertical overlap.
        """
        paragraphs = page.pdf_paragraph
        if not paragraphs or len(paragraphs) < 2:
            return

        max_iterations = len(paragraphs) * len(paragraphs)  # Safety break
        iterations = 0

        while iterations < max_iterations:
            iterations += 1
            overlap_found_in_pass = False

            for i in range(len(paragraphs)):
                for j in range(i + 1, len(paragraphs)):
                    para1 = paragraphs[i]
                    para2 = paragraphs[j]

                    if para1.box is None or para2.box is None:
                        continue

                    if para1.xobj_id != para2.xobj_id:
                        continue

                    labels = (
                        (para1.layout_label or "").lower(),
                        (para2.layout_label or "").lower(),
                    )
                    # Table grids have their own geometry and section labels
                    # must retain a dedicated lane.  The old midpoint splitter
                    # compressed either case back into adjacent body text.
                    if any(
                        marker in label
                        for label in labels
                        for marker in ("table", "heading", "title", "section")
                    ):
                        continue

                    # Check for overlap using the existing method
                    if self.bbox_overlap(para1.box, para2.box):
                        if self.is_bbox_contain_in_vertical(para1.box, para2.box):
                            continue
                        # Calculate vertical overlap details
                        overlap_y_start = max(para1.box.y, para2.box.y)
                        overlap_y_end = min(para1.box.y2, para2.box.y2)
                        overlap_height = overlap_y_end - overlap_y_start

                        # Calculate horizontal overlap details
                        overlap_x_start = max(para1.box.x, para2.box.x)
                        overlap_x_end = min(para1.box.x2, para2.box.x2)
                        overlap_width = overlap_x_end - overlap_x_start

                        # Ensure there's a real 2D overlap, focusing on vertical adjustment
                        if overlap_height > 1e-6 and overlap_width > 1e-6:
                            overlap_found_in_pass = True

                            # Determine which paragraph is visually higher
                            if para1.box.y2 > para2.box.y and para1.box.y < para2.box.y:
                                lower_para = para1
                                higher_para = para2
                            # Handle cases where y values are identical (or very close)
                            # Prefer the one with smaller y2 as the higher one, or break tie arbitrarily
                            elif para1.box.y2 < para2.box.y2:
                                lower_para = para1
                                higher_para = para2
                            else:
                                lower_para = para2
                                higher_para = para1

                            # Calculate the midpoint of the vertical overlap
                            mid_y = overlap_y_start + overlap_height / 2

                            # Adjust boxes, ensuring they remain valid (y2 > y)
                            if mid_y > higher_para.box.y and mid_y < lower_para.box.y2:
                                higher_para.box.y = mid_y + 1
                                lower_para.box.y2 = mid_y - 1
                            else:
                                # This might happen if one box is fully contained vertically
                                # within another, or due to floating point issues.
                                # Log a warning and skip adjustment for this pair in this iteration.
                                # A more complex strategy might be needed for full containment.
                                logger.warning(
                                    "Could not resolve overlap between paragraphs"
                                    f" {higher_para.debug_id} and {lower_para.debug_id}"
                                    " using simple midpoint strategy."
                                    f" Midpoint: {mid_y},"
                                    f" Higher Box: {higher_para.box},"
                                    f" Lower Box: {lower_para.box}"
                                )

            # If no overlaps were found and adjusted in this pass, we're done.
            if not overlap_found_in_pass:
                break

        if iterations == max_iterations:
            logger.warning(
                f"Maximum iterations ({max_iterations}) reached in"
                f" fix_overlapping_paragraphs for page {page.page_number}."
                " Some overlaps might remain."
            )

    @staticmethod
    def classify_numbered_section_headings(paragraphs: list[PdfParagraph]) -> None:
        """Mark short decimal/roman numbered labels as headings.

        Style-based heading detection cannot be trusted for many older PDFs:
        the visual heading and its body often share one embedded font.  The
        conservative length cap avoids turning a numbered body sentence or a
        table entry into a heading.
        """
        section_pattern = re.compile(
            r"^\s*(?:\d+(?:\.\d+){0,4}|[IVXLCDM]+\.?)(?:\s+|\.)\S",
            re.IGNORECASE,
        )
        for paragraph in paragraphs:
            label = (paragraph.layout_label or "").lower()
            if "table" in label or "heading" in label:
                continue
            text = " ".join((paragraph.unicode or "").split())
            if text and len(text) <= 110 and section_pattern.match(text):
                paragraph.layout_label = "heading"

    def merge_same_line_sentence_fragments(
        self, paragraphs: list[PdfParagraph]
    ) -> None:
        """Join adjacent source runs that form one visual sentence line.

        Layout models sometimes assign a different role to each style run in
        the same sentence (``All`` / ``banks are required`` / ``to validate``).
        Geometry is authoritative here: only horizontally adjacent runs on the
        same baseline are joined, and tables, lists and numbered headings are
        excluded.
        """
        body_labels = {"paragraph", "plain text", "text", "body_text"}

        def leading_line_box(paragraph: PdfParagraph) -> Box | None:
            lines = [
                composition.pdf_line
                for composition in paragraph.pdf_paragraph_composition or []
                if composition.pdf_line and composition.pdf_line.box
            ]
            if not lines:
                return None
            anchor = lines[0].box
            anchor_center = (anchor.y + anchor.y2) / 2.0
            same_baseline = [
                line.box
                for line in lines
                if abs((line.box.y + line.box.y2) / 2.0 - anchor_center) <= 2.5
            ]
            return Box(
                min(box.x for box in same_baseline),
                min(box.y for box in same_baseline),
                max(box.x2 for box in same_baseline),
                max(box.y2 for box in same_baseline),
            )

        changed = True
        while changed:
            changed = False
            candidates = [
                paragraph
                for paragraph in paragraphs
                if paragraph.box
                and leading_line_box(paragraph)
                and not any(
                    marker in (paragraph.layout_label or "").lower()
                    for marker in ("table", "list")
                )
            ]
            candidates.sort(
                key=lambda paragraph: (
                    paragraph.xobj_id or 0,
                    round(leading_line_box(paragraph).y / 3.0),
                    leading_line_box(paragraph).x,
                )
            )
            for left_index, left in enumerate(candidates):
                left_box = leading_line_box(left)
                left_text = " ".join((left.unicode or "").split())
                if not left_text:
                    continue
                if re.match(r"^\d+(?:\.\d+)+", left_text):
                    continue
                if left_text.endswith((".", ":", ";", "?", "!")):
                    continue
                for right in candidates[left_index + 1 :]:
                    if left.xobj_id != right.xobj_id:
                        continue
                    right_box = leading_line_box(right)
                    right_text = " ".join((right.unicode or "").split())
                    if not right_text or not right_text[0].islower():
                        continue
                    left_height = max(1.0, left_box.y2 - left_box.y)
                    right_height = max(1.0, right_box.y2 - right_box.y)
                    center_delta = abs(
                        (left_box.y + left_box.y2) / 2.0
                        - (right_box.y + right_box.y2) / 2.0
                    )
                    horizontal_gap = right_box.x - left_box.x2
                    if (
                        center_delta > max(2.5, min(left_height, right_height) * 0.35)
                        or not -2.0 <= horizontal_gap <= 18.0
                    ):
                        continue
                    from babeldoc.format.pdf.document_il.il_version_1 import VisualBbox

                    source_chars = (
                        self._paragraph_source_characters(left)
                        + self._paragraph_source_characters(right)
                    )
                    space_style = next(
                        (char.pdf_style for char in source_chars if char.pdf_style),
                        left.pdf_style or right.pdf_style,
                    )
                    space_x = left_box.x2
                    space_width = max(
                        1.5, min(4.0, max(horizontal_gap, 1.5))
                    )
                    space_box = Box(
                        space_x,
                        left_box.y,
                        space_x + space_width,
                        left_box.y2,
                    )
                    left.pdf_paragraph_composition.append(
                        PdfParagraphComposition(
                            pdf_character=PdfCharacter(
                                char_unicode=" ",
                                box=space_box,
                                visual_bbox=VisualBbox(box=space_box),
                                vertical=False,
                                xobj_id=left.xobj_id,
                                pdf_style=space_style,
                            )
                        )
                    )
                    left.pdf_paragraph_composition.extend(
                        right.pdf_paragraph_composition or []
                    )
                    if (right.layout_label or "").lower() in body_labels:
                        left.layout_label = right.layout_label
                    elif (left.layout_label or "").lower() not in body_labels:
                        left.layout_label = "paragraph"
                    self.update_paragraph_data(left, update_unicode=True)
                    paragraphs.remove(right)
                    changed = True
                    break
                if changed:
                    break

    def split_embedded_structured_lines(
        self, paragraphs: list[PdfParagraph]
    ) -> None:
        """Recover semantic records hidden inside one extracted text block.

        The operation is deliberately content- and geometry-based.  It only
        activates when a multi-line paragraph contains a procedure marker,
        contact field, or an embedded decimal section heading.  Ordinary body
        paragraphs retain their source grouping and diagrams are untouched.
        """
        procedure_pattern = re.compile(
            r"^\s*(?:step\s*\d+|example\s*:|sample\s+iban\s*:|result\s+full\s+iban)",
            re.IGNORECASE,
        )
        contact_pattern = re.compile(
            r"\b(?:tel(?:ephone)?|fax|e[ -]?mail|email)\s*[:：]", re.IGNORECASE
        )
        section_pattern = re.compile(
            r"^\s*\d+(?:\.\d+){1,4}(?:\s+|\.)\S", re.IGNORECASE
        )

        def line_text(composition: PdfParagraphComposition) -> str:
            if not composition.pdf_line:
                return ""
            return get_char_unicode_string(
                composition.pdf_line.pdf_character or []
            ).strip()

        def clone_with(
            source: PdfParagraph,
            compositions: list[PdfParagraphComposition],
            label: str,
        ) -> PdfParagraph:
            clone = PdfParagraph(
                pdf_style=source.pdf_style,
                pdf_paragraph_composition=list(compositions),
                xobj_id=source.xobj_id,
                unicode="",
                vertical=source.vertical,
                first_line_indent=source.first_line_indent,
                debug_id=generate_base58_id(),
                layout_label=label,
                layout_id=source.layout_id,
                text_direction=source.text_direction,
                text_align=source.text_align,
            )
            self.update_paragraph_data(clone, update_unicode=True)
            return clone

        for paragraph in list(paragraphs):
            compositions = list(paragraph.pdf_paragraph_composition or [])
            visual_lines = [
                (index, line_text(composition))
                for index, composition in enumerate(compositions)
                if composition.pdf_line
            ]
            if len(visual_lines) < 2:
                continue

            texts = [text for _, text in visual_lines]
            procedure_mode = any(procedure_pattern.match(text) for text in texts)
            contact_mode = any(contact_pattern.search(text) for text in texts)
            section_indices = [
                index
                for index, text in visual_lines
                if len(text) <= 110 and section_pattern.match(text)
            ]
            if not (procedure_mode or contact_mode or section_indices):
                continue

            replacement: list[PdfParagraph] = []
            if procedure_mode:
                # Every painted row keeps its own source box.  This prevents a
                # later label, formula, phone number, or e-mail address from
                # being translated/reflowed on the preceding row.
                for composition in compositions:
                    text = line_text(composition)
                    label = (
                        "procedure_step"
                        if procedure_pattern.match(text)
                        else "procedure_step_body"
                    )
                    replacement.append(clone_with(paragraph, [composition], label))
            elif contact_mode:
                # Phone numbers are often tagged as formula runs immediately
                # after the ``Tel:`` line composition. Keep non-line fragments
                # with their preceding row so exact values survive.
                contact_rows: list[list[PdfParagraphComposition]] = []
                for composition in compositions:
                    if composition.pdf_line or not contact_rows:
                        contact_rows.append([composition])
                    else:
                        contact_rows[-1].append(composition)
                replacement.extend(
                    clone_with(paragraph, row, "contact_card")
                    for row in contact_rows
                )
            else:
                # Preserve body-line grouping on both sides of the embedded
                # heading, isolating only the semantic boundary itself.
                boundaries = sorted(set(section_indices))
                cursor = 0
                first_boundary = boundaries[0]
                prefix_text = " ".join(
                    line_text(composition)
                    for composition in compositions[:first_boundary]
                ).strip()
                prefix_paragraph = (
                    clone_with(
                        paragraph,
                        compositions[:first_boundary],
                        "paragraph",
                    )
                    if first_boundary > 0
                    else None
                )
                nearby_candidates = []
                if prefix_paragraph and prefix_paragraph.box:
                    for candidate in paragraphs:
                        if candidate is paragraph or not candidate.box:
                            continue
                        if candidate.xobj_id != paragraph.xobj_id:
                            continue
                        if "table" in (candidate.layout_label or "").lower():
                            continue
                        horizontal_overlap = min(
                            candidate.box.x2, prefix_paragraph.box.x2
                        ) - max(candidate.box.x, prefix_paragraph.box.x)
                        if horizontal_overlap <= 0:
                            continue
                        gap = min(
                            abs(candidate.box.y - prefix_paragraph.box.y2),
                            abs(prefix_paragraph.box.y - candidate.box.y2),
                        )
                        if gap <= 30.0:
                            nearby_candidates.append((gap, candidate))
                previous = (
                    min(nearby_candidates, key=lambda item: item[0])[1]
                    if nearby_candidates
                    else None
                )
                prefix_is_continuation = bool(
                    prefix_text
                    and prefix_text[0].islower()
                    and previous
                    and previous.xobj_id == paragraph.xobj_id
                    and "table" not in (previous.layout_label or "").lower()
                )
                if prefix_is_continuation:
                    previous.pdf_paragraph_composition.extend(
                        compositions[:first_boundary]
                    )
                    self.update_paragraph_data(previous, update_unicode=True)
                    cursor = first_boundary
                for boundary in boundaries:
                    if boundary > cursor:
                        replacement.append(
                            clone_with(paragraph, compositions[cursor:boundary], "paragraph")
                        )
                    replacement.append(
                        clone_with(paragraph, [compositions[boundary]], "heading")
                    )
                    cursor = boundary + 1
                if cursor < len(compositions):
                    replacement.append(
                        clone_with(paragraph, compositions[cursor:], "paragraph")
                    )

            if len(replacement) <= 1:
                continue
            insert_at = paragraphs.index(paragraph)
            paragraphs[insert_at : insert_at + 1] = replacement

    @staticmethod
    def classify_structured_text_blocks(paragraphs: list[PdfParagraph]) -> None:
        """Classify procedural rows and contact cards before translation.

        These regions look like ordinary prose to a layout model but their
        source line breaks encode meaning.  Joining them into one MT prompt is
        what made MOD-97 steps detach from their result lines and shrank the
        final contact card into a single line.
        """
        contact_pattern = re.compile(
            r"\b(?:tel(?:ephone)?|fax|e[ -]?mail|email)\s*[:：]", re.IGNORECASE
        )
        procedure_pattern = re.compile(
            r"^\s*(?:step\s*\d+|example\s*:|sample\s+iban\s*:|result\s+full\s+iban)",
            re.IGNORECASE,
        )
        contact_anchors: list[PdfParagraph] = []
        procedure_anchors: list[PdfParagraph] = []
        for paragraph in paragraphs:
            label = (paragraph.layout_label or "").lower()
            text = paragraph.unicode or ""
            if contact_pattern.search(text):
                # A bordered card is frequently classified as a one-cell table.
                # Semantic contact fields take precedence over that visual label.
                paragraph.layout_label = "contact_card"
                source_lines = [
                    get_char_unicode_string(composition.pdf_line.pdf_character).strip()
                    for composition in paragraph.pdf_paragraph_composition or []
                    if composition.pdf_line and composition.pdf_line.pdf_character
                ]
                source_lines = [line for line in source_lines if line]
                if len(source_lines) > 1:
                    paragraph.unicode = "\n".join(source_lines)
                contact_anchors.append(paragraph)
            elif procedure_pattern.match(text):
                # Semantic procedure markers take precedence over a generic
                # heading/title label.  In older PDFs, bold ``Step N`` rows
                # are routinely detected as headings even when their source
                # line compositions also contain the instruction and result.
                paragraph.layout_label = "procedure_step"
                source_lines = [
                    get_char_unicode_string(composition.pdf_line.pdf_character).strip()
                    for composition in paragraph.pdf_paragraph_composition or []
                    if composition.pdf_line and composition.pdf_line.pdf_character
                ]
                source_lines = [line for line in source_lines if line]
                if len(source_lines) > 1:
                    paragraph.unicode = "\n".join(source_lines)
                procedure_anchors.append(paragraph)
            elif "table" in label or "heading" in label or "title" in label:
                continue

        # Contact cards are often emitted as individual name, role and contact
        # paragraphs. Mark nearby sibling fields once a Tel/Fax/E-mail anchor
        # identifies the card, giving every row the same protected layout path.
        for anchor in contact_anchors:
            if not anchor.box:
                continue
            for candidate in paragraphs:
                if candidate is anchor or not candidate.box:
                    continue
                label = (candidate.layout_label or "").lower()
                if any(marker in label for marker in ("heading", "title")):
                    continue
                if getattr(candidate, "xobj_id", None) != getattr(anchor, "xobj_id", None):
                    continue
                vertical_gap = max(
                    anchor.box.y - candidate.box.y2,
                    candidate.box.y - anchor.box.y2,
                    0.0,
                )
                horizontal_overlap = min(anchor.box.x2, candidate.box.x2) - max(anchor.box.x, candidate.box.x)
                if vertical_gap <= 95.0 and horizontal_overlap >= -12.0:
                    candidate.layout_label = "contact_card"

        # A procedure is commonly painted as three independent paragraphs:
        # ``Step N``, its instruction, and a formula/result.  Propagate the
        # structural label only to close, horizontally aligned followers and
        # stop at the next step anchor.  This lets the typesetter reserve one
        # ordered lane for the complete step without folding unrelated prose
        # into it.
        sorted_procedures = sorted(
            (p for p in paragraphs if p.box), key=lambda p: -p.box.y2
        )
        for anchor in procedure_anchors:
            if not anchor.box:
                continue
            anchor_index = sorted_procedures.index(anchor)
            previous = anchor
            for candidate in sorted_procedures[anchor_index + 1:]:
                if not candidate.box:
                    continue
                candidate_text = " ".join((candidate.unicode or "").split())
                if candidate is not anchor and procedure_pattern.match(candidate_text):
                    break
                label = (candidate.layout_label or "").lower()
                if any(marker in label for marker in ("table", "heading", "title", "contact")):
                    break
                if getattr(candidate, "xobj_id", None) != getattr(anchor, "xobj_id", None):
                    continue
                vertical_gap = previous.box.y - candidate.box.y2
                horizontal_overlap = min(anchor.box.x2, candidate.box.x2) - max(
                    anchor.box.x, candidate.box.x
                )
                min_width = max(
                    1.0,
                    min(
                        anchor.box.x2 - anchor.box.x,
                        candidate.box.x2 - candidate.box.x,
                    ),
                )
                if vertical_gap < -3.0 or vertical_gap > 34.0:
                    break
                if horizontal_overlap < min_width * 0.20:
                    continue
                candidate.layout_label = "procedure_step_body"
                previous = candidate

    @staticmethod
    def merge_detached_leading_word_fragments(paragraphs: list[PdfParagraph]) -> None:
        """Join an extraction-created first word with its same-line prose.

        This is geometric and role-aware rather than language-specific, so it
        does not merge headings, table cells or actual list labels.
        """
        word_pattern = re.compile(r"^[A-Za-z]{1,12}$")
        index = 0
        while index < len(paragraphs):
            lead = paragraphs[index]
            lead_text = " ".join((lead.unicode or "").split())
            lead_label = (lead.layout_label or "").lower()
            if (
                not lead.box
                or not word_pattern.fullmatch(lead_text)
                or any(marker in lead_label for marker in ("table", "list"))
            ):
                index += 1
                continue
            merged = False
            for candidate in paragraphs[index + 1:index + 5]:
                if not candidate.box or getattr(candidate, "xobj_id", None) != getattr(lead, "xobj_id", None):
                    continue
                candidate_text = " ".join((candidate.unicode or "").split())
                lead_height = max(1.0, lead.box.y2 - lead.box.y)
                candidate_height = max(1.0, candidate.box.y2 - candidate.box.y)
                center_delta = abs(
                    (lead.box.y + lead.box.y2) / 2.0
                    - (candidate.box.y + candidate.box.y2) / 2.0
                )
                vertical_overlap = max(
                    0.0,
                    min(lead.box.y2, candidate.box.y2)
                    - max(lead.box.y, candidate.box.y),
                )
                horizontal_gap = candidate.box.x - lead.box.x2
                same_visual_line = (
                    center_delta <= max(2.5, min(lead_height, candidate_height) * 0.35)
                    and vertical_overlap >= min(lead_height, candidate_height) * 0.55
                )
                if (
                    not candidate_text
                    or len(candidate_text) < 12
                    or not same_visual_line
                    or not -2.0 <= horizontal_gap <= 18.0
                ):
                    continue
                candidate.unicode = f"{lead_text} {candidate_text}"
                candidate.pdf_paragraph_composition = (
                    list(lead.pdf_paragraph_composition or [])
                    + list(candidate.pdf_paragraph_composition or [])
                )
                candidate.box = Box(
                    min(lead.box.x, candidate.box.x),
                    min(lead.box.y, candidate.box.y),
                    max(lead.box.x2, candidate.box.x2),
                    max(lead.box.y2, candidate.box.y2),
                )
                paragraphs.remove(lead)
                merged = True
                break
            if not merged:
                index += 1

    def _sort_characters_in_lines(self, page: Page):
        """Sort characters in each line from left to right, top to bottom."""
        for paragraph in page.pdf_paragraph:
            for composition in paragraph.pdf_paragraph_composition:
                if composition.pdf_line:
                    line = composition.pdf_line
                    line.pdf_character.sort(key=self._get_char_sort_key)

    def _get_char_sort_key(self, char: PdfCharacter):
        """Get sort key for character positioning (top to bottom, left to right)."""
        visual_box = char.visual_bbox.box
        pdf_box = char.box

        # Use visual box if IoU with bbox is >= 0.1, otherwise use bbox
        if calculate_iou_for_boxes(visual_box, pdf_box) >= 0.1:
            box = visual_box
        else:
            box = pdf_box

        # Sort by y coordinate first (top to bottom), then x coordinate (left to right)
        # Note: In PDF coordinate system, y increases upward, so we negate y for top-to-bottom sorting
        return (box.x, -box.y)
