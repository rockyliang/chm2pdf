# chm2pdf

Convert CHM (Microsoft Compiled HTML Help) files to PDF with full content and style fidelity.

Designed as a **modular library** — use `chm2pdf.convert()` in your own projects, or run standalone via CLI / GUI.

---

## Requirements

- Python 3.10+
- `pip install beautifulsoup4 playwright pypdf`
- `playwright install chromium`

Optional:
- `pip install pychm` — cross-platform CHM extraction (required on macOS/Linux)
- `pip install weasyprint` — alternative PDF renderer (requires native GTK3/Pango libraries)
- PrinceXML — alternative PDF renderer (if you have a license)

On Windows without `pychm`, the built-in `hh.exe` is used for CHM extraction automatically.

### PDF renderers

| Renderer | Install | Notes |
|---|---|---|
| **Playwright** (default) | `pip install playwright && playwright install chromium` | Zero system deps, ~250MB Chromium download |
| WeasyPrint | `pip install weasyprint` + native GTK3 libs | Requires MSYS2 on Windows, system packages on Linux |
| PrinceXML | Commercial license | Proprietary, watermark without license |

---

## Usage

### As a library

**One-liner** — the simplest way to convert a CHM file:

```python
from chm2pdf import convert

convert("input.chm", "output.pdf")
```

**With options:**

```python
from chm2pdf import convert

convert(
    "input.chm",
    "output.pdf",
    title="My Book",
    include_toc=True,
    renderer="playwright",    # or "weasyprint", "prince"
    keep_work=False,
    log=print,                # any callable(str) -> None
    progress_callback=None,   # callable(current, total) -> None
)
```

**Fine-grained control** — use individual modules directly:

```python
from pathlib import Path
from chm2pdf.extractor import get_extractor
from chm2pdf.book_builder import build_book
from chm2pdf.pdf_renderer import get_renderer

extracted_dir = Path("./extracted")
extracted_dir.mkdir(exist_ok=True)

# 1. Extract
extractor = get_extractor()
extractor.extract(Path("input.chm"), extracted_dir, log=print)

# 2. Build combined HTML
book_html, print_css = build_book(
    extracted_dir=extracted_dir,
    title="My Book",
    include_generated_toc=True,
    renderer="playwright",
    log=print,
)

# 3. Render PDF
renderer = get_renderer("playwright")
renderer.render(book_html, print_css, Path("output.pdf"), log=print)
```

### CLI

```bash
# Single file
python -m chm2pdf input.chm -o output_folder/

# Batch conversion
python -m chm2pdf file1.chm file2.chm file3.chm -o pdfs/

# With options
python -m chm2pdf input.chm -o out/ --title "My Book" --no-toc --keep-work

# Using WeasyPrint instead of Playwright
python -m chm2pdf input.chm --renderer weasyprint

# Using PrinceXML
python -m chm2pdf input.chm --renderer prince --prince-path "C:\Program Files\Prince\engine\bin\prince.exe"
```

Full CLI options:

```
python -m chm2pdf --help

  input                 One or more .chm files to convert
  -o, --output          Output directory (default: same as input)
  --title               PDF title (default: CHM filename)
  --no-toc              Skip generated table of contents page
  --renderer            playwright (default), weasyprint, or prince
  --prince-path         Path to prince.exe
  --hh-path             Path to hh.exe (Windows fallback)
  --keep-work           Keep intermediate working folder
  --version             Show version
```

### GUI

```bash
python chm2pdf_gui.py
```

1. Select a `.chm` file
2. Choose output folder
3. Pick PDF renderer (Playwright recommended)
4. Click **Convert CHM to PDF**

---

## Project structure

```
chm2pdf/
├── __init__.py           # convert() entry point + package metadata
├── __main__.py           # python -m chm2pdf entry point
├── utils.py              # Encoding detection, CJK language detection, path normalization
├── extractor.py          # CHM extraction (pychm + hh.exe fallback)
├── toc_parser.py         # Hierarchical .hhc TOC parsing (BeautifulSoup)
├── html_processor.py     # HTML head/body splitting, style scoping, URL rewriting
├── css_generator.py      # Language-adaptive print stylesheet generation
├── book_builder.py       # Combine topics into single/chunked HTML (with orphan detection)
├── pdf_renderer.py       # Playwright (default) + WeasyPrint + PrinceXML + PDF merging
├── cli.py                # Command-line interface
└── gui.py                # Tkinter GUI
```

Each module is independently importable. The dependency graph is:

```
utils.py          ← no internal deps (foundation)
extractor.py      ← utils
toc_parser.py     ← utils
html_processor.py ← utils
css_generator.py  ← no internal deps
book_builder.py   ← utils, toc_parser, html_processor, css_generator
pdf_renderer.py   ← utils
cli.py            ← __init__ (convert)
gui.py            ← __init__ (convert), extractor, pdf_renderer (availability checks only)
```

---

## Conversion pipeline

```
CHM file
  │
  ├─ Extract ─── pychm (cross-platform) or hh.exe (Windows)
  │
  ├─ Parse TOC ─── .hhc file → hierarchical tree (BeautifulSoup)
  │                 └─ fallback: scan all HTML files if no .hhc
  │
  ├─ Detect orphans ─── HTML files not in TOC → appended
  │
  ├─ Detect CJK language ─── encoding → zh-CN / zh-TW / ja / ko
  │
  ├─ Process topics (for each HTML file):
  │   ├─ Detect encoding (BOM → meta charset → fallback chain)
  │   ├─ Split head/body (BeautifulSoup)
  │   ├─ Extract & scope <style> blocks
  │   ├─ Collect <link> stylesheets
  │   ├─ Rewrite internal URLs → anchor references
  │   └─ Validate referenced resources exist
  │
  ├─ Rewrite CSS url() paths in stylesheets
  │
  ├─ Generate book.html
  │   ├─ Hierarchical nested TOC (cover + contents page)
  │   ├─ Topic titles as h1–h6 matching TOC depth → PDF bookmarks
  │   ├─ Language-specific font stack in print.css
  │   └─ Large documents (500+ topics): split into chunks
  │
  └─ Render PDF ─── Playwright (default), WeasyPrint, or PrinceXML
        └─ Chunked: render each chunk → merge with pypdf
```

---

## What changed (v1 → v2)

The original converter was a single 759-line script using regex HTML parsing, PrinceXML (proprietary), and Windows-only `hh.exe`. Version 2 is a complete rewrite.

### Problems fixed

| # | Problem | Fix |
|---|---|---|
| 1 | **Missing pages** — orphan HTML files silently dropped | Orphan detection includes all HTML files |
| 2 | **Lost `<style>` blocks** — inline styles discarded | Extracted, scoped per-topic, and embedded |
| 3 | **Fragile regex HTML parsing** | Replaced with BeautifulSoup |
| 4 | **Silent extraction failures** | Proper error handling + validation |
| 5 | **`!important` clobbered original fonts** | Removed — low-specificity defaults |
| 6 | **No fallback without `.hhc`** | Auto-scans all HTML files |
| 7 | **Broken CSS `url()` references** | Rewritten relative to book.html |
| 8 | **Flat bookmarks** | Multi-level hierarchy preserved (h1–h6 matching TOC depth) |
| 9 | **PrinceXML watermark** | Playwright is now default (free, no system deps) |
| 10 | **Windows-only** | pychm for cross-platform extraction |
| 11 | **Wrong CJK glyphs** — mixed SC/TC/JP/KR font stack | Language-adaptive font selection from encoding |
| 12 | **Flat TOC page** — no visual hierarchy | Nested `<ul>` with indentation and bold headings |
| 13 | **Out of memory on large CHMs** | Chunked rendering + PDF merging for 500+ topics |

### New in v2

- One-call `convert()` API for library use
- CLI with batch support (`python -m chm2pdf`)
- Modular package (10 independently importable modules)
- Three PDF renderers: Playwright (default), WeasyPrint, PrinceXML
- Zero system dependencies with Playwright — just `pip install` and go
- Determinate progress bar in GUI (with animated rendering indicator)
- Resource validation (warns about missing images)
- Chunked rendering for large documents (500+ topics) with automatic PDF merging
- CJK language detection with locale-specific font stacks
- Hierarchical PDF bookmarks and nested table of contents

---

## CJK (Chinese/Japanese/Korean) support

The converter detects the document language from the declared character encoding and selects the appropriate font stack automatically:

| Encoding | Language | Primary fonts |
|---|---|---|
| GB2312, GBK, GB18030 | Simplified Chinese (`zh-CN`) | Microsoft YaHei, SimHei, SimSun, Noto Sans CJK SC |
| Big5, Big5-HKSCS | Traditional Chinese (`zh-TW`) | Microsoft JhengHei, PMingLiU, Noto Sans CJK TC |
| Shift_JIS, EUC-JP | Japanese (`ja`) | Yu Gothic, Meiryo, MS Gothic, Noto Sans CJK JP |
| EUC-KR, CP949 | Korean (`ko`) | Malgun Gothic, Gulim, Noto Sans CJK KR |

The detected language is also set as the `<html lang>` attribute, which helps PDF renderers choose the correct glyph variants for shared CJK code points.

If characters look wrong in the PDF, install a font for the correct locale (e.g. **Noto Sans CJK SC** for Simplified Chinese).
