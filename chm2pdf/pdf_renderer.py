"""PDF rendering backends: WeasyPrint (primary) and PrinceXML (optional)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from abc import ABC, abstractmethod
from pathlib import Path

from .utils import LogFn

# WeasyPrint on Windows needs GTK3 native libraries from MSYS2.
# Auto-detect and add to DLL search path if installed.
_MSYS2_MINGW64_BIN = r"C:\msys64\mingw64\bin"
if sys.platform == "win32" and os.path.isdir(_MSYS2_MINGW64_BIN):
    # Python 3.8+ requires explicit DLL directory registration
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(_MSYS2_MINGW64_BIN)
    if _MSYS2_MINGW64_BIN not in os.environ.get("PATH", ""):
        os.environ["PATH"] = _MSYS2_MINGW64_BIN + os.pathsep + os.environ.get("PATH", "")


class PdfRenderer(ABC):
    """Base class for PDF rendering backends."""

    name: str = ""

    @abstractmethod
    def render(
        self,
        book_html: Path,
        print_css: Path,
        output_pdf: Path,
        log: LogFn,
    ) -> None:
        """Render *book_html* + *print_css* into *output_pdf*."""

    @abstractmethod
    def available(self) -> bool:
        """Return True if this backend can be used."""


class WeasyPrintRenderer(PdfRenderer):
    """Primary renderer — free, cross-platform, no watermark."""

    name = "weasyprint"

    def available(self) -> bool:
        try:
            import weasyprint  # noqa: F401
            return True
        except (ImportError, OSError):
            return False

    def render(
        self,
        book_html: Path,
        print_css: Path,
        output_pdf: Path,
        log: LogFn,
    ) -> None:
        try:
            from weasyprint import CSS, HTML
        except OSError as e:
            raise RuntimeError(
                "WeasyPrint native libraries not found. On Windows, install GTK3:\n"
                "  1. Install MSYS2 from https://www.msys2.org/\n"
                "  2. Run: pacman -S mingw-w64-x86_64-pango\n"
                "  3. Add C:\\msys64\\mingw64\\bin to your PATH\n"
                f"Original error: {e}"
            ) from e

        size_mb = book_html.stat().st_size / 1024 / 1024
        log(f"Rendering PDF with WeasyPrint ({size_mb:.1f} MB HTML input)...")
        if size_mb > 10:
            log("  Large document — this may take several minutes. Please wait...")

        html_doc = HTML(
            filename=str(book_html),
            base_url=str(book_html.parent),
        )
        css = CSS(filename=str(print_css))
        html_doc.write_pdf(str(output_pdf), stylesheets=[css])

        if not output_pdf.exists():
            raise RuntimeError("WeasyPrint finished but no PDF was created.")
        pdf_mb = output_pdf.stat().st_size / 1024 / 1024
        log(f"PDF created: {output_pdf.name} ({pdf_mb:.1f} MB)")


# ---------------------------------------------------------------------------
# Playwright/Chromium backend (default — zero system deps)
# ---------------------------------------------------------------------------


class PlaywrightRenderer(PdfRenderer):
    """Playwright/Chromium renderer — no native system dependencies.

    Requires::

        pip install playwright
        playwright install chromium
    """

    name = "playwright"

    def available(self) -> bool:
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
            return True
        except ImportError:
            return False

    def render(
        self,
        book_html: Path,
        print_css: Path,
        output_pdf: Path,
        log: LogFn,
    ) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise RuntimeError(
                "Playwright is not installed. Run:\n"
                "  pip install playwright\n"
                "  playwright install chromium"
            )

        size_mb = book_html.stat().st_size / 1024 / 1024
        log(f"Rendering PDF with Playwright/Chromium ({size_mb:.1f} MB HTML input)...")
        if size_mb > 10:
            log("  Large document — this may take several minutes. Please wait...")

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()

            # Navigate to the book HTML file
            page.goto(book_html.as_uri(), wait_until="networkidle")

            # Inject the print stylesheet
            page.add_style_tag(path=str(print_css))

            # Generate PDF with hierarchical bookmarks
            page.pdf(
                path=str(output_pdf),
                format="A4",
                margin={
                    "top": "16mm",
                    "right": "14mm",
                    "bottom": "16mm",
                    "left": "14mm",
                },
                outline=True,           # hierarchical bookmarks from h1-h6
                tagged=True,            # tagged/accessible PDF
                print_background=True,  # include background colors/images
            )

            browser.close()

        if not output_pdf.exists():
            raise RuntimeError("Playwright finished but no PDF was created.")
        pdf_mb = output_pdf.stat().st_size / 1024 / 1024
        log(f"PDF created: {output_pdf.name} ({pdf_mb:.1f} MB)")


# ---------------------------------------------------------------------------
# PrinceXML backend (optional, requires license for watermark-free output)
# ---------------------------------------------------------------------------

COMMON_PRINCE_LOCATIONS = [
    r"C:\Program Files\Prince\engine\bin\prince.exe",
    r"C:\Program Files (x86)\Prince\engine\bin\prince.exe",
]


def _find_prince(explicit_path: str = "") -> str:
    """Resolve prince.exe: explicit path -> PATH -> common locations."""
    if explicit_path and Path(explicit_path).is_file():
        return explicit_path
    found = shutil.which("prince")
    if found:
        return found
    for p in COMMON_PRINCE_LOCATIONS:
        if Path(p).exists():
            return p
    return ""


class PrinceXmlRenderer(PdfRenderer):
    """Optional renderer for users with a PrinceXML license."""

    name = "prince"

    def __init__(self, prince_path: str = ""):
        self._prince_path = prince_path

    @property
    def prince_path(self) -> str:
        return _find_prince(self._prince_path)

    def available(self) -> bool:
        return bool(self.prince_path)

    def render(
        self,
        book_html: Path,
        print_css: Path,
        output_pdf: Path,
        log: LogFn,
    ) -> None:
        prince = self.prince_path
        if not prince:
            raise RuntimeError(
                "PrinceXML not found. Install PrinceXML or switch to WeasyPrint."
            )

        cmd = [
            prince,
            str(book_html),
            f"--style={print_css}",
            "-o",
            str(output_pdf),
        ]
        log(f"Rendering PDF with PrinceXML...")
        log(f"Command: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.stdout.strip():
            log(result.stdout.strip())
        if result.stderr.strip():
            log(result.stderr.strip())
        if "warning" in result.stderr.lower() and "font" in result.stderr.lower():
            log(
                "Prince reported a font warning. Install a Chinese-capable "
                "font (e.g. Noto Sans CJK) if characters look wrong."
            )
        if result.returncode != 0:
            raise RuntimeError(
                f"PrinceXML failed with exit code {result.returncode}."
            )
        if not output_pdf.exists():
            raise RuntimeError("PrinceXML finished but no PDF was created.")
        log(f"PDF created: {output_pdf}")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_renderer(
    name: str = "playwright",
    prince_path: str = "",
) -> PdfRenderer:
    """Return the requested PDF renderer.

    *name* is ``'playwright'`` (default), ``'weasyprint'``, or ``'prince'``.
    """
    if name == "playwright":
        renderer = PlaywrightRenderer()
        if not renderer.available():
            raise RuntimeError(
                "Playwright not installed. Run:\n"
                "  pip install playwright\n"
                "  playwright install chromium"
            )
        return renderer

    if name == "prince":
        renderer = PrinceXmlRenderer(prince_path)
        if not renderer.available():
            raise RuntimeError(
                "PrinceXML not found. Install it or use --renderer playwright."
            )
        return renderer

    # "weasyprint"
    renderer = WeasyPrintRenderer()
    if not renderer.available():
        raise RuntimeError(
            "WeasyPrint not installed. Run: pip install weasyprint\n"
            "WeasyPrint also requires native GTK3 libraries (see README)."
        )
    return renderer


# ---------------------------------------------------------------------------
# PDF merging (for chunked rendering)
# ---------------------------------------------------------------------------

def merge_pdfs(pdf_paths: list[Path], output_path: Path, log: LogFn) -> None:
    """Merge multiple PDF files into a single PDF using pypdf."""
    try:
        from pypdf import PdfWriter
    except ImportError:
        raise RuntimeError(
            "pypdf is required for merging chunked PDFs. Run: pip install pypdf"
        )

    log(f"Merging {len(pdf_paths)} PDF chunks...")
    writer = PdfWriter()
    for pdf_path in pdf_paths:
        writer.append(str(pdf_path))

    with open(output_path, "wb") as f:
        writer.write(f)

    if not output_path.exists():
        raise RuntimeError("PDF merge failed — no output file created.")

    size_mb = output_path.stat().st_size / 1024 / 1024
    log(f"Merged PDF: {output_path.name} ({size_mb:.1f} MB)")
