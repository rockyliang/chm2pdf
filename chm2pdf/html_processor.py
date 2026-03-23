"""HTML processing: head/body splitting, style extraction, URL rewriting."""

from __future__ import annotations

import posixpath
import re
from pathlib import Path

from bs4 import BeautifulSoup

from .utils import (
    is_external_url,
    load_text,
    normalize_chm_local_path,
    rewrite_fragment_urls,
    ATTR_URL_RE,
)


# ---------------------------------------------------------------------------
# Head / body splitting with <style> extraction
# ---------------------------------------------------------------------------

def split_head_body(topic_html: str) -> tuple[str, str, list[str]]:
    """Parse an HTML topic and return (head_html, body_html, style_blocks).

    - Extracts all <style> blocks from both <head> and <body>.
    - Removes all <script> and <noscript> tags.
    - Returns raw inner HTML strings (not re-serialized by BeautifulSoup)
      for body to preserve original formatting.
    """
    soup = BeautifulSoup(topic_html, "html.parser")

    # Collect all <style> blocks
    style_blocks: list[str] = []
    for style_tag in soup.find_all("style"):
        css_text = style_tag.string or style_tag.get_text()
        if css_text and css_text.strip():
            style_blocks.append(css_text.strip())
        style_tag.decompose()

    # Remove <script> and <noscript>
    for tag_name in ("script", "noscript"):
        for tag in soup.find_all(tag_name):
            tag.decompose()

    # Extract head content
    head_tag = soup.find("head")
    head_html = head_tag.decode_contents() if head_tag else ""

    # Extract body content — use decode_contents() to get inner HTML
    body_tag = soup.find("body")
    body_html = body_tag.decode_contents() if body_tag else soup.decode_contents()

    return head_html, body_html, style_blocks


# ---------------------------------------------------------------------------
# Stylesheet collection
# ---------------------------------------------------------------------------

def collect_stylesheets(head_html: str, topic_dir_rel: str) -> list[str]:
    """Extract local stylesheet paths from <link> tags in head HTML.

    Returns normalized CHM-internal paths, deduped, in order.
    """
    soup = BeautifulSoup(head_html, "html.parser")
    paths: list[str] = []
    seen: set[str] = set()

    for link in soup.find_all("link"):
        rel = link.get("rel", [])
        if isinstance(rel, list):
            rel = " ".join(rel)
        if "stylesheet" not in rel.lower():
            continue
        href = link.get("href", "").strip()
        if not href or is_external_url(href):
            continue
        # Resolve relative to topic directory
        if topic_dir_rel:
            resolved = posixpath.normpath(posixpath.join(topic_dir_rel, href))
        else:
            resolved = posixpath.normpath(href)
        norm = normalize_chm_local_path(resolved)
        if norm not in seen:
            seen.add(norm)
            paths.append(norm)

    return paths


# ---------------------------------------------------------------------------
# Style scoping
# ---------------------------------------------------------------------------

# Matches CSS selectors before the opening brace of a rule.
# Handles multi-line selectors and ignores @-rules (which start with @).
_CSS_RULE_RE = re.compile(
    r"""
    (?P<atrule>@[^{]+\{(?:[^{}]*|\{[^{}]*\})*\})  # @-rule with nested braces
    |
    (?P<selectors>[^@{][^{]*?)\s*\{                  # regular selectors
    """,
    re.VERBOSE | re.S,
)


def scope_styles(css_text: str, section_id: str) -> str:
    """Prefix CSS selectors with ``#section_id`` to scope them.

    Handles @media rules by scoping their inner selectors.
    Leaves @font-face, @import, @charset, @keyframes rules unchanged.
    """
    if not css_text.strip():
        return css_text

    result_parts: list[str] = []
    pos = 0
    for m in _CSS_RULE_RE.finditer(css_text):
        # Emit text between matches as-is
        result_parts.append(css_text[pos:m.start()])
        if m.group("atrule"):
            # @-rule — include as-is (could recursively scope @media, but
            # for CHM content the simple approach is sufficient)
            result_parts.append(m.group("atrule"))
        elif m.group("selectors"):
            selectors = m.group("selectors")
            # Find the matching closing brace
            brace_start = m.end() - 1  # position of {
            depth = 1
            i = brace_start + 1
            while i < len(css_text) and depth > 0:
                if css_text[i] == "{":
                    depth += 1
                elif css_text[i] == "}":
                    depth -= 1
                i += 1
            body = css_text[brace_start + 1 : i - 1]
            # Scope each comma-separated selector
            parts = []
            for sel in selectors.split(","):
                sel = sel.strip()
                if sel:
                    # For html/body selectors, replace with the section ID
                    if sel.lower() in ("html", "body", "html body"):
                        parts.append(f"#{section_id}")
                    else:
                        parts.append(f"#{section_id} {sel}")
            scoped_selectors = ", ".join(parts)
            result_parts.append(f"{scoped_selectors} {{{body}}}")
            pos = i
            continue
        pos = m.end()

    # Emit remaining text
    result_parts.append(css_text[pos:])
    return "".join(result_parts)


# ---------------------------------------------------------------------------
# CSS url() rewriting
# ---------------------------------------------------------------------------

_CSS_URL_RE = re.compile(
    r"""url\(\s*(['"]?)([^'")]+)\1\s*\)""",
    re.I,
)


def rewrite_css_urls(css_text: str, css_dir: str, book_dir: str) -> str:
    """Rewrite ``url()`` references in CSS to be relative to book.html.

    *css_dir* is the directory of the CSS file (relative to extraction root).
    *book_dir* is the directory of book.html (relative to extraction root).
    """
    def _replace(m: re.Match) -> str:
        quote = m.group(1)
        url = m.group(2).strip()
        if is_external_url(url) or url.startswith("data:"):
            return m.group(0)
        # Resolve relative to CSS file location
        if css_dir:
            abs_path = posixpath.normpath(posixpath.join(css_dir, url))
        else:
            abs_path = posixpath.normpath(url)
        # Make relative to book.html location
        if book_dir:
            rel_path = posixpath.relpath(abs_path, book_dir)
        else:
            rel_path = abs_path
        return f"url({quote}{rel_path}{quote})"

    return _CSS_URL_RE.sub(_replace, css_text)


# ---------------------------------------------------------------------------
# Body heading downgrade (for Playwright bookmark compatibility)
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(
    r"<(h[1-6])(\b[^>]*)>(.*?)</\1>",
    re.I | re.S,
)


def downgrade_body_headings(body_html: str) -> str:
    """Replace ``<h1>``–``<h6>`` in topic body with ``<div class="body-hN">``.

    This prevents body-content headings from generating PDF bookmarks in
    Playwright (which auto-generates outline entries from all h1–h6).
    WeasyPrint uses ``bookmark-level: none`` CSS for the same purpose, but
    using non-heading elements is a renderer-agnostic solution.

    The visual appearance is preserved via CSS rules in ``css_generator.py``.
    """
    def _replace(m: re.Match) -> str:
        tag = m.group(1).lower()  # e.g. "h2"
        attrs = m.group(2)        # e.g. ' class="foo"'
        inner = m.group(3)
        return f'<div class="body-{tag}"{attrs}>{inner}</div>'

    return _HEADING_RE.sub(_replace, body_html)


def rewrite_stylesheet_file(
    css_path: Path,
    extracted_dir: Path,
    book_dir: str,
) -> None:
    """Rewrite url() references in a CSS file in-place."""
    if not css_path.exists():
        return
    css_text = load_text(css_path)
    css_rel_dir = css_path.parent.relative_to(extracted_dir).as_posix()
    rewritten = rewrite_css_urls(css_text, css_rel_dir, book_dir)
    if rewritten != css_text:
        from .utils import save_text
        save_text(css_path, rewritten)
