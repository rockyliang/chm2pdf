"""Combine topics into a single HTML book with full content completeness."""

from __future__ import annotations

import html as html_mod
import posixpath
from collections import OrderedDict
from pathlib import Path
from typing import Callable

from .utils import LogFn
from .css_generator import generate_print_css
from .html_processor import (
    collect_stylesheets,
    downgrade_body_headings,
    rewrite_css_urls,
    rewrite_stylesheet_file,
    scope_styles,
    split_head_body,
)
from .toc_parser import (
    TocEntry,
    find_hhc,
    flatten_toc,
    generate_fallback_entries,
    parse_hhc,
)
from .utils import (
    HTML_TOPIC_EXTS,
    ATTR_URL_RE,
    detect_cjk_language,
    load_text,
    normalize_chm_local_path,
    rewrite_fragment_urls,
    save_text,
    slugify,
    sniff_declared_encoding,
)


def _build_anchor_map(
    entries: list[tuple[str, str, int]],
) -> dict[str, str]:
    """Create a mapping from normalized CHM path -> unique HTML anchor ID."""
    anchor_map: dict[str, str] = {}
    used_ids: set[str] = set()
    for idx, (title, rel_path, _level) in enumerate(entries, start=1):
        if not rel_path:
            continue
        base_id = slugify(f"section-{idx:04d}-{title}")
        section_id = base_id
        counter = 2
        while section_id in used_ids:
            section_id = f"{base_id}-{counter}"
            counter += 1
        used_ids.add(section_id)
        anchor_map[normalize_chm_local_path(rel_path)] = section_id
    return anchor_map


def _find_orphan_html(
    extracted_dir: Path,
    toc_paths: set[str],
) -> list[str]:
    """Find HTML files not listed in the TOC."""
    orphans: list[str] = []
    for f in sorted(extracted_dir.rglob("*")):
        if not f.is_file():
            continue
        if f.suffix.lower() not in HTML_TOPIC_EXTS:
            continue
        rel = f.relative_to(extracted_dir).as_posix()
        norm = normalize_chm_local_path(rel)
        if norm not in toc_paths:
            orphans.append(rel)
    return orphans


def _validate_resources(
    body_html: str,
    topic_dir_rel: str,
    extracted_dir: Path,
    log: LogFn,
) -> None:
    """Log warnings for missing images/resources referenced in HTML."""
    for m in ATTR_URL_RE.finditer(body_html):
        attr, _, url = m.group(1), m.group(2), m.group(3)
        if not url or url.startswith("#") or url.startswith("data:"):
            continue
        lowered = url.strip().lower()
        if any(lowered.startswith(p) for p in ("http://", "https://", "mailto:", "javascript:", "about:")):
            continue
        # Skip cross-CHM references (ms-its:, mk:@msitstore:, or paths with ::)
        if "::" in url or lowered.startswith("ms-its:") or lowered.startswith("mk:@"):
            continue
        # Skip absolute file:/ references
        if lowered.startswith("file:"):
            continue
        # Resolve relative to topic directory
        if topic_dir_rel:
            resolved = posixpath.normpath(posixpath.join(topic_dir_rel, url))
        else:
            resolved = posixpath.normpath(url)
        resource_path = extracted_dir / Path(resolved)
        if not resource_path.exists():
            log(f"  Warning: missing resource: {resolved} (referenced via {attr})")


# ---------------------------------------------------------------------------
# Shared preparation: parse TOC, process topics, rewrite CSS
# ---------------------------------------------------------------------------

def _prepare_topics(
    extracted_dir: Path,
    title: str,
    renderer: str,
    log: LogFn,
    progress_callback: Callable[[int, int], None] | None = None,
) -> tuple[list[dict], OrderedDict, Path, str]:
    """Parse TOC, process all topics, rewrite CSS, generate print stylesheet.

    Returns (sections, stylesheet_paths, print_css_path, language).
    Each section dict has keys: title, section_id, level, content, scoped_styles.
    *language* is a BCP-47 tag (e.g. ``'zh-CN'``) or empty string.
    """
    # ------------------------------------------------------------------
    # 1. Parse TOC (hierarchical)
    # ------------------------------------------------------------------
    detected_encodings: list[str] = []  # for CJK language detection

    hhc_path = find_hhc(extracted_dir)
    if hhc_path:
        log(f"Using TOC file: {hhc_path.name}")
        declared = sniff_declared_encoding(hhc_path.read_bytes())
        if declared:
            log(f"Detected TOC encoding: {declared}")
            detected_encodings.append(declared)
        toc_tree = parse_hhc(hhc_path, log=log)
    else:
        toc_tree = generate_fallback_entries(extracted_dir, log=log)

    flat_entries = flatten_toc(toc_tree)
    if not flat_entries:
        raise RuntimeError("No TOC entries found and no HTML files in extraction.")
    log(f"Found {len(flat_entries)} TOC entries.")

    # ------------------------------------------------------------------
    # 2. Detect orphan HTML files
    # ------------------------------------------------------------------
    toc_paths = {normalize_chm_local_path(p) for _, p, _ in flat_entries if p}
    orphans = _find_orphan_html(extracted_dir, toc_paths)
    if orphans:
        log(f"Found {len(orphans)} HTML files not in TOC (will include as additional topics).")
        for orphan_path in orphans:
            flat_entries.append((
                Path(orphan_path).stem.replace("_", " ").replace("-", " ").title(),
                orphan_path,
                1,  # level
            ))

    # ------------------------------------------------------------------
    # 3. Filter to existing files
    # ------------------------------------------------------------------
    existing_entries: list[tuple[int, str, str, int, Path]] = []
    for idx, (topic_title, rel_path, level) in enumerate(flat_entries, start=1):
        if not rel_path:
            continue
        topic_file = extracted_dir / Path(rel_path)
        if topic_file.exists() and topic_file.is_file():
            existing_entries.append((idx, topic_title, rel_path, level, topic_file))
        else:
            log(f"Skipping missing topic: {rel_path}")

    if not existing_entries:
        raise RuntimeError("None of the topic files were found after extraction.")

    total_topics = len(existing_entries)
    log(f"Processing {total_topics} topics...")

    # ------------------------------------------------------------------
    # 4. Build anchor map
    # ------------------------------------------------------------------
    anchor_map = _build_anchor_map([
        (t, p, l) for _, t, p, l, _ in existing_entries
    ])

    # ------------------------------------------------------------------
    # 5. Process each topic
    # ------------------------------------------------------------------
    stylesheet_paths: OrderedDict[str, None] = OrderedDict()
    sections: list[dict] = []

    for step, (idx, topic_title, rel_path, level, topic_file) in enumerate(
        existing_entries, start=1
    ):
        rel_path_norm = normalize_chm_local_path(rel_path)
        topic_dir_rel = posixpath.dirname(rel_path_norm)
        section_id = anchor_map.get(rel_path_norm, f"section-{idx:04d}")

        declared = sniff_declared_encoding(topic_file.read_bytes())
        if declared:
            log(f"Topic encoding for {rel_path}: {declared}")
            detected_encodings.append(declared)

        topic_html = load_text(topic_file)
        head_html, body_html, style_blocks = split_head_body(topic_html)

        # Collect linked stylesheets
        for css_path in collect_stylesheets(head_html, topic_dir_rel):
            css_file = extracted_dir / Path(css_path)
            if css_file.exists() and css_file.is_file():
                stylesheet_paths[css_path] = None

        # Scope inline <style> blocks for this section
        scoped_styles = [scope_styles(css_text, section_id) for css_text in style_blocks]

        # Rewrite internal URLs
        rewritten_body = rewrite_fragment_urls(body_html, topic_dir_rel, anchor_map)

        # Downgrade h1-h6 in body to <div class="body-hN"> so only topic
        # titles produce PDF bookmarks (required for Playwright; also cleaner
        # for WeasyPrint since it replaces the bookmark-level:none CSS hack).
        rewritten_body = downgrade_body_headings(rewritten_body)

        # Validate resources
        _validate_resources(rewritten_body, topic_dir_rel, extracted_dir, log)

        sections.append({
            "title": topic_title,
            "section_id": section_id,
            "level": level,
            "content": rewritten_body,
            "scoped_styles": scoped_styles,
        })

        if progress_callback:
            progress_callback(step, total_topics)

    # ------------------------------------------------------------------
    # 6. Rewrite CSS url() references in stylesheets
    # ------------------------------------------------------------------
    book_dir = ""  # book.html lives at extracted_dir root
    for css_path in stylesheet_paths:
        css_file = extracted_dir / Path(css_path)
        rewrite_stylesheet_file(css_file, extracted_dir, book_dir)

    # ------------------------------------------------------------------
    # 7. Detect CJK language from collected encodings
    # ------------------------------------------------------------------
    language = detect_cjk_language(detected_encodings)
    if language:
        log(f"Detected document language: {language}")

    # ------------------------------------------------------------------
    # 8. Generate print.css with language-specific font stack
    # ------------------------------------------------------------------
    print_css_path = extracted_dir / "print.css"
    print_css = generate_print_css(renderer=renderer, language=language)
    save_text(print_css_path, print_css)

    return sections, stylesheet_paths, print_css_path, language


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

def _build_nested_toc_html(sections: list[dict]) -> str:
    """Build a properly nested ``<ul>`` tree from sections with level info."""
    if not sections:
        return ""

    parts: list[str] = []
    prev_level = 0

    for sec in sections:
        level = sec["level"]
        sid = html_mod.escape(sec["section_id"], quote=True)
        title = html_mod.escape(sec["title"])
        link = f'<a href="#{sid}">{title}</a>'

        if level > prev_level:
            # Going deeper — open new sub-lists (one per level increase)
            for _ in range(level - prev_level):
                parts.append("<ul>")
        elif level < prev_level:
            # Going up — close items and sub-lists
            parts.append("</li>")
            for _ in range(prev_level - level):
                parts.append("</ul>")
                parts.append("</li>")
        elif prev_level > 0:
            # Same level — close previous sibling
            parts.append("</li>")

        parts.append(f"<li>{link}")
        prev_level = level

    # Close all remaining open tags
    if prev_level > 0:
        parts.append("</li>")
        for _ in range(prev_level - 1):
            parts.append("</ul>")
            parts.append("</li>")
        parts.append("</ul>")

    return "\n".join(parts)


def _generate_book_html(
    title: str,
    body_sections: list[dict],
    stylesheet_paths: OrderedDict,
    include_generated_toc: bool,
    include_cover: bool = True,
    toc_sections: list[dict] | None = None,
    language: str = "",
) -> str:
    """Generate combined HTML string from processed sections.

    *body_sections* are the topics included in this HTML file.
    *toc_sections* (if given) are used for the TOC listing — pass all sections
    when building a chunk that should contain the full table of contents.
    *language* is a BCP-47 tag used for the ``<html lang>`` attribute.
    """
    if toc_sections is None:
        toc_sections = body_sections

    # Stylesheet tags
    stylesheet_tags = []
    for css_path in stylesheet_paths:
        css_attr = html_mod.escape(css_path, quote=True)
        stylesheet_tags.append(f'<link rel="stylesheet" href="{css_attr}">')

    # Collect scoped styles from the body sections only
    all_scoped_styles: list[str] = []
    for sec in body_sections:
        all_scoped_styles.extend(sec.get("scoped_styles", []))

    scoped_style_block = ""
    if all_scoped_styles:
        combined = "\n\n".join(all_scoped_styles)
        scoped_style_block = f"<style>\n{combined}\n</style>"

    # Generated TOC with proper nested <ul> hierarchy
    toc_html = ""
    if include_generated_toc:
        toc_html = (
            '<section class="generated-toc">\n'
            '  <h1>Contents</h1>\n'
            f'{_build_nested_toc_html(toc_sections)}\n'
            '</section>\n'
        )

    # Topic sections — use <h1>..<h6> matching the TOC level so PDF
    # bookmarks are hierarchical automatically (WeasyPrint maps hN to
    # bookmark-level N by default).
    section_html_parts = []
    for sec in body_sections:
        sid = html_mod.escape(sec["section_id"], quote=True)
        h_level = min(sec["level"], 6)  # HTML only has h1–h6
        section_html_parts.append(
            f'<section class="topic" id="{sid}">\n'
            f'  <h{h_level} class="topic-title">'
            f'{html_mod.escape(sec["title"])}</h{h_level}>\n'
            f'  <div class="topic-body">\n'
            f'{sec["content"]}\n'
            f'  </div>\n'
            f'</section>'
        )

    # Cover
    cover_html = ""
    if include_cover:
        cover_html = (
            '  <section class="cover">\n'
            '    <div>\n'
            f'      <h1>{html_mod.escape(title)}</h1>\n'
            '    </div>\n'
            '  </section>\n'
        )

    lang_attr = f' lang="{html_mod.escape(language, quote=True)}"' if language else ""

    book_html = (
        '<!doctype html>\n'
        f'<html{lang_attr}>\n'
        '<head>\n'
        '  <meta charset="utf-8">\n'
        f'  <title>{html_mod.escape(title)}</title>\n'
        f'  {chr(10).join("  " + tag for tag in stylesheet_tags)}\n'
        f'  {scoped_style_block}\n'
        '</head>\n'
        '<body>\n'
        f'{cover_html}'
        f'  {toc_html}\n'
        f'  {(chr(10) + chr(10)).join(section_html_parts)}\n'
        '</body>\n'
        '</html>\n'
    )

    return book_html


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_book(
    extracted_dir: Path,
    title: str,
    include_generated_toc: bool,
    renderer: str,
    log: LogFn,
    progress_callback: Callable[[int, int], None] | None = None,
) -> tuple[Path, Path]:
    """Build the combined HTML book and print stylesheet.

    Returns (book_html_path, print_css_path).
    """
    sections, stylesheet_paths, print_css_path, language = _prepare_topics(
        extracted_dir, title, renderer, log, progress_callback
    )

    book_html = _generate_book_html(
        title, sections, stylesheet_paths, include_generated_toc,
        language=language,
    )

    book_html_path = extracted_dir / "book.html"
    save_text(book_html_path, book_html)

    size_mb = book_html_path.stat().st_size / 1024 / 1024
    log(f"Created combined HTML: {book_html_path.name} ({size_mb:.1f} MB)")
    log(f"Created print stylesheet: {print_css_path.name}")
    return book_html_path, print_css_path


def build_book_chunked(
    extracted_dir: Path,
    title: str,
    include_generated_toc: bool,
    renderer: str,
    log: LogFn,
    progress_callback: Callable[[int, int], None] | None = None,
    chunk_size: int = 200,
) -> list[tuple[Path, Path]]:
    """Build multiple HTML books for large documents.

    Splits topics into chunks of *chunk_size*.  The first chunk includes the
    cover page and the full table of contents.  Each chunk includes all
    external stylesheets but only the scoped inline styles for its own topics.

    Returns list of ``(book_html_path, print_css_path)`` for each chunk.
    """
    sections, stylesheet_paths, print_css_path, language = _prepare_topics(
        extracted_dir, title, renderer, log, progress_callback
    )

    total = len(sections)
    num_chunks = (total + chunk_size - 1) // chunk_size
    log(f"Splitting {total} topics into {num_chunks} chunks of up to {chunk_size} topics.")

    results: list[tuple[Path, Path]] = []
    for chunk_idx in range(num_chunks):
        start = chunk_idx * chunk_size
        end = min(start + chunk_size, total)
        chunk_sections = sections[start:end]

        is_first = (chunk_idx == 0)

        chunk_html = _generate_book_html(
            title,
            body_sections=chunk_sections,
            stylesheet_paths=stylesheet_paths,
            include_generated_toc=include_generated_toc and is_first,
            include_cover=is_first,
            # First chunk gets the full TOC referencing all sections
            toc_sections=sections if is_first else None,
            language=language,
        )

        chunk_path = extracted_dir / f"book_{chunk_idx:03d}.html"
        save_text(chunk_path, chunk_html)

        size_mb = chunk_path.stat().st_size / 1024 / 1024
        log(f"  Chunk {chunk_idx + 1}/{num_chunks}: {end - start} topics ({size_mb:.1f} MB)")

        results.append((chunk_path, print_css_path))

    return results
