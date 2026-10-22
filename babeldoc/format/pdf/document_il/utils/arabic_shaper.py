"""Run-level Arabic shaping diagnostics backed by HarfBuzz.

The PDF renderer still owns line breaking and visual BiDi ordering.  This module
therefore deliberately returns *logical* text plus a HarfBuzz glyph plan rather
than feeding HarfBuzz's visual glyph order straight into the renderer.  It lets
us validate contextual shaping, ligatures, clusters, and target-font coverage
without introducing a second BiDi reordering step.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ShapedGlyph:
    glyph_id: int
    cluster: int
    x_advance: int
    x_offset: int
    y_offset: int


@dataclass(frozen=True, slots=True)
class ShapedRun:
    text: str
    upem: int
    glyphs: tuple[ShapedGlyph, ...]

    @property
    def has_missing_glyphs(self) -> bool:
        return any(glyph.glyph_id == 0 for glyph in self.glyphs)


def shape_arabic_run(text: str, font_path: str | Path) -> ShapedRun | None:
    """Shape one logical Arabic run with HarfBuzz.

    Returns ``None`` when HarfBuzz is unavailable or *text* has no Arabic
    letters.  A caller can then retain the legacy reshaper as a compatibility
    fallback.  Clusters are preserved exactly as HarfBuzz reports them.
    """
    if not text or not any("\u0600" <= char <= "\u08ff" for char in text):
        return None

    try:
        import uharfbuzz as hb
    except ImportError:
        return None

    font_bytes = Path(font_path).read_bytes()
    face = hb.Face(font_bytes)
    font = hb.Font(face)
    font.scale = (face.upem, face.upem)

    buffer = hb.Buffer()
    buffer.add_str(text)
    buffer.direction = "rtl"
    buffer.script = "Arab"
    buffer.language = "ar"
    hb.shape(font, buffer)

    glyphs = tuple(
        ShapedGlyph(
            glyph_id=info.codepoint,
            cluster=info.cluster,
            x_advance=position.x_advance,
            x_offset=position.x_offset,
            y_offset=position.y_offset,
        )
        for info, position in zip(buffer.glyph_infos, buffer.glyph_positions, strict=True)
    )
    return ShapedRun(text=text, upem=face.upem, glyphs=glyphs)
