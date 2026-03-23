"""Command-line interface for CHM to PDF conversion."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="chm2pdf",
        description="Convert CHM (Compiled HTML Help) files to PDF.",
    )
    parser.add_argument(
        "input",
        nargs="+",
        help="One or more .chm files to convert",
    )
    parser.add_argument(
        "-o", "--output",
        default=None,
        help="Output directory (default: same directory as input file)",
    )
    parser.add_argument(
        "--title",
        default=None,
        help="PDF title (default: CHM filename stem)",
    )
    parser.add_argument(
        "--no-toc",
        action="store_true",
        help="Do not include a generated table of contents page",
    )
    parser.add_argument(
        "--renderer",
        choices=["playwright", "weasyprint", "prince"],
        default="playwright",
        help="PDF rendering backend (default: playwright)",
    )
    parser.add_argument(
        "--prince-path",
        default="",
        help="Path to prince.exe (only used with --renderer prince)",
    )
    parser.add_argument(
        "--hh-path",
        default="",
        help="Path to hh.exe (Windows CHM extraction fallback)",
    )
    parser.add_argument(
        "--keep-work",
        action="store_true",
        help="Keep the intermediate working folder after conversion",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    args = parser.parse_args(argv)

    from . import convert

    def log(msg: str) -> None:
        print(msg, flush=True)

    for chm_path_str in args.input:
        chm_path = Path(chm_path_str)
        if not chm_path.exists():
            log(f"Error: file not found: {chm_path}")
            sys.exit(1)

        output_dir = Path(args.output) if args.output else chm_path.parent
        output_pdf = output_dir / f"{chm_path.stem}.pdf"

        log("=" * 72)
        log(f"Converting: {chm_path.name}")

        try:
            convert(
                chm_path=chm_path,
                output_pdf=output_pdf,
                title=args.title,
                include_toc=not args.no_toc,
                renderer=args.renderer,
                prince_path=args.prince_path,
                hh_path=args.hh_path,
                keep_work=args.keep_work,
                log=log,
            )
        except Exception as exc:
            log(f"Error: {exc}")
            sys.exit(1)

    log("Done.")
