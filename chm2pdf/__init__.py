"""chm2pdf — convert Compiled HTML Help (.chm) files to PDF.

Quick start::

    from chm2pdf import convert
    convert("input.chm", "output.pdf")

For finer control, use the individual modules directly::

    from chm2pdf.extractor import get_extractor
    from chm2pdf.book_builder import build_book
    from chm2pdf.pdf_renderer import get_renderer
"""

from __future__ import annotations

__version__ = "2.0.0"


def convert(
    chm_path: str | "Path",
    output_pdf: str | "Path",
    *,
    title: str | None = None,
    include_toc: bool = True,
    renderer: str = "playwright",
    prince_path: str = "",
    hh_path: str = "",
    keep_work: bool = False,
    log: "Callable[[str], None] | None" = None,
    progress_callback: "Callable[[int, int], None] | None" = None,
) -> "Path":
    """Convert a CHM file to PDF.

    This is the main entry point for using chm2pdf as a library.

    Parameters
    ----------
    chm_path : str or Path
        Path to the input .chm file.
    output_pdf : str or Path
        Path for the output .pdf file.
    title : str, optional
        PDF title.  Defaults to the CHM filename stem.
    include_toc : bool
        Whether to include a generated table-of-contents page (default True).
    renderer : str
        ``'playwright'`` (default), ``'weasyprint'``, or ``'prince'``.
    prince_path : str
        Explicit path to prince.exe (only needed with ``renderer='prince'``).
    hh_path : str
        Explicit path to hh.exe (Windows fallback when pychm is unavailable).
    keep_work : bool
        Keep the intermediate working folder after conversion (default False).
    log : callable, optional
        Logging function ``f(message: str) -> None``.  Defaults to ``print``.
    progress_callback : callable, optional
        Called as ``f(current: int, total: int)`` during topic processing.

    Returns
    -------
    Path
        The path to the created PDF file.
    """
    import shutil
    import time
    from pathlib import Path

    from .book_builder import build_book, build_book_chunked
    from .extractor import get_extractor
    from .pdf_renderer import get_renderer, merge_pdfs
    from .utils import HTML_TOPIC_EXTS

    # Documents with more HTML files than this threshold are rendered in
    # chunks to avoid excessive memory usage and long render times.
    CHUNK_THRESHOLD = 500
    CHUNK_SIZE = 200

    chm_path = Path(chm_path)
    output_pdf = Path(output_pdf)
    if log is None:
        log = print

    if not chm_path.exists():
        raise FileNotFoundError(f"CHM file not found: {chm_path}")

    output_dir = output_pdf.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    title = title or chm_path.stem

    # Working directory
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    work_root = output_dir / f"{chm_path.stem}_build_{timestamp}"
    extracted_dir = work_root / "extracted"
    extracted_dir.mkdir(parents=True, exist_ok=True)

    try:
        log(f"Working folder: {work_root}")

        # 1. Extract CHM
        extractor = get_extractor(hh_path=hh_path)
        extractor.extract(chm_path, extracted_dir, log)

        # 2. Count HTML files to decide rendering strategy
        html_count = sum(
            1 for f in extracted_dir.rglob("*")
            if f.is_file() and f.suffix.lower() in HTML_TOPIC_EXTS
        )

        pdf_renderer = get_renderer(name=renderer, prince_path=prince_path)

        if html_count > CHUNK_THRESHOLD:
            # Large document: chunked rendering
            log(f"Large document ({html_count} HTML files) — using chunked rendering.")
            chunks = build_book_chunked(
                extracted_dir=extracted_dir,
                title=title,
                include_generated_toc=include_toc,
                renderer=renderer,
                log=log,
                progress_callback=progress_callback,
                chunk_size=CHUNK_SIZE,
            )

            chunk_pdfs: list[Path] = []
            for i, (chunk_html, chunk_css) in enumerate(chunks):
                chunk_pdf = work_root / f"chunk_{i:03d}.pdf"
                log(f"Rendering chunk {i + 1}/{len(chunks)}...")
                pdf_renderer.render(chunk_html, chunk_css, chunk_pdf, log)
                chunk_pdfs.append(chunk_pdf)

            merge_pdfs(chunk_pdfs, output_pdf, log)
        else:
            # Normal single-file rendering
            book_html, print_css = build_book(
                extracted_dir=extracted_dir,
                title=title,
                include_generated_toc=include_toc,
                renderer=renderer,
                log=log,
                progress_callback=progress_callback,
            )
            pdf_renderer.render(book_html, print_css, output_pdf, log)

        return output_pdf

    finally:
        if not keep_work:
            shutil.rmtree(work_root, ignore_errors=True)
            log("Temporary working folder removed.")
        else:
            log(f"Working files kept at: {work_root}")
