"""Generate the print stylesheet for PDF rendering."""

from __future__ import annotations

MAX_BOOKMARK_LEVEL = 6

# Language-specific font stacks.
# Each list is ordered by preference: sans-serif first, then serif fallbacks.
# Only fonts for the target language are listed to prevent glyph substitution
# from a font designed for a different CJK locale.
_FONT_STACKS: dict[str, str] = {
    "zh-CN": (
        '"Microsoft YaHei", "SimHei", "SimSun", "NSimSun", '
        '"Noto Sans CJK SC", "Noto Serif CJK SC", '
        '"WenQuanYi Micro Hei", "WenQuanYi Zen Hei", '
        '"Arial Unicode MS", Arial, Helvetica, sans-serif'
    ),
    "zh-TW": (
        '"Microsoft JhengHei", "PMingLiU", "MingLiU", '
        '"Noto Sans CJK TC", "Noto Serif CJK TC", '
        '"Arial Unicode MS", Arial, Helvetica, sans-serif'
    ),
    "ja": (
        '"Yu Gothic", "Meiryo", "MS Gothic", "MS PGothic", "MS Mincho", '
        '"Noto Sans CJK JP", "Noto Serif CJK JP", '
        '"Arial Unicode MS", Arial, Helvetica, sans-serif'
    ),
    "ko": (
        '"Malgun Gothic", "Gulim", "Dotum", "Batang", '
        '"Noto Sans CJK KR", "Noto Serif CJK KR", '
        '"Arial Unicode MS", Arial, Helvetica, sans-serif'
    ),
}

# Generic fallback when language is unknown (covers all CJK locales).
_FONT_STACK_GENERIC = (
    '"Microsoft YaHei", "SimHei", "SimSun", '
    '"Microsoft JhengHei", "PMingLiU", '
    '"Yu Gothic", "Meiryo", '
    '"Malgun Gothic", "Gulim", '
    '"Noto Sans CJK SC", "Noto Sans CJK TC", "Noto Sans CJK JP", "Noto Sans CJK KR", '
    '"Arial Unicode MS", Arial, Helvetica, sans-serif'
)


def generate_print_css(
    renderer: str = "weasyprint",
    language: str = "",
) -> str:
    """Generate print.css content.

    *renderer* is ``'weasyprint'`` or ``'prince'`` — controls which
    bookmark-level property name is used.

    *language* is a BCP-47 tag (e.g. ``'zh-CN'``, ``'zh-TW'``, ``'ja'``,
    ``'ko'``).  When set, the font stack is tailored for that language so
    that glyphs are rendered with the correct locale-specific forms.
    """
    # Use standard CSS property for WeasyPrint; Prince uses its own prefix
    if renderer == "prince":
        bm = "prince-bookmark-level"
    else:
        bm = "bookmark-level"

    # Select font stack for the detected language
    font_family = _FONT_STACKS.get(language, _FONT_STACK_GENERIC)

    return f"""\
@page {{
  size: A4;
  margin: 16mm 14mm 16mm 14mm;
}}

/* ------------------------------------------------------------------ */
/* Base typography — low specificity, original CHM styles win          */
/* ------------------------------------------------------------------ */
html, body {{
  font-family: {font_family};
  font-size: 10pt;
  line-height: 1.42;
  color: #111;
}}

body {{
  margin: 0;
}}

/* ------------------------------------------------------------------ */
/* Cover page                                                         */
/* ------------------------------------------------------------------ */
.cover {{
  page-break-after: always;
  min-height: 90vh;
  display: flex;
  align-items: center;
  justify-content: center;
  text-align: center;
}}

.cover h1 {{
  font-size: 26pt;
  margin: 0;
}}

/* ------------------------------------------------------------------ */
/* Generated table of contents (nested lists)                         */
/* ------------------------------------------------------------------ */
.generated-toc {{
  page-break-after: always;
}}

.generated-toc h1 {{
  {bm}: 1;
}}

.generated-toc ul {{
  list-style: none;
  padding-left: 0;
  margin: 0;
}}

/* Indent nested levels */
.generated-toc ul ul {{
  padding-left: 16pt;
}}

.generated-toc li {{
  margin: 2pt 0;
  line-height: 1.5;
}}

/* Top-level entries are bolder */
.generated-toc > ul > li > a {{
  font-weight: bold;
  font-size: 11pt;
}}

.generated-toc > ul > li > ul > li > a {{
  font-size: 10pt;
}}

/* ------------------------------------------------------------------ */
/* Topics                                                             */
/* ------------------------------------------------------------------ */
.topic {{
  page-break-before: always;
}}

/* Topic titles use h1–h6 matching their TOC level for correct PDF    */
/* bookmarks.  Style them uniformly regardless of heading level.      */
h1.topic-title, h2.topic-title, h3.topic-title,
h4.topic-title, h5.topic-title, h6.topic-title {{
  font-size: 14pt;
  font-weight: bold;
  margin: 0 0 12pt 0;
  padding-bottom: 6pt;
  border-bottom: 1px solid #bbb;
}}

/* Body headings are downgraded to <div class="body-hN"> so they     */
/* don't generate PDF bookmarks.  Style them to match the originals. */
.body-h1 {{ font-size: 2em; font-weight: bold; margin: 0.67em 0; }}
.body-h2 {{ font-size: 1.5em; font-weight: bold; margin: 0.83em 0; }}
.body-h3 {{ font-size: 1.17em; font-weight: bold; margin: 1em 0; }}
.body-h4 {{ font-size: 1em; font-weight: bold; margin: 1.33em 0; }}
.body-h5 {{ font-size: 0.83em; font-weight: bold; margin: 1.67em 0; }}
.body-h6 {{ font-size: 0.67em; font-weight: bold; margin: 2.33em 0; }}

/* ------------------------------------------------------------------ */
/* Element defaults — only when CHM styles don't specify              */
/* ------------------------------------------------------------------ */
img {{
  max-width: 100%;
  height: auto;
}}

pre, code {{
  white-space: pre-wrap;
  word-wrap: break-word;
}}

table {{
  border-collapse: collapse;
  max-width: 100%;
}}

th, td {{
  vertical-align: top;
}}

a {{
  color: inherit;
}}
"""
