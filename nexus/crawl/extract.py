"""HTML → markdown extraction.

A dependency-free extractor built on ``html.parser`` that drops the
content that must never reach the LLM:

- ``<script>``, ``<style>``, ``<noscript>``, ``<template>`` bodies
  (the previous extractor leaked script/style *text* into the markdown).
- HTML comments.
- Elements hidden via inline ``style`` (display:none / visibility:hidden
  / opacity:0 / zero font-size) or ``hidden`` / ``aria-hidden="true"``
  attributes — a common prompt-injection vector.

Visible text is collected into paragraph-ish blocks. This is the
JS-off extraction path; rendered-DOM extraction (Crawl4AI/Playwright)
is a separate, deferred path.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

_DROP_CONTENT_TAGS = {"script", "style", "noscript", "template", "head", "svg"}
_BLOCK_TAGS = {
    "p",
    "div",
    "section",
    "article",
    "li",
    "br",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "tr",
    "blockquote",
    "pre",
}

_HIDDEN_STYLE = re.compile(
    r"(display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0(?!\.)|font-size\s*:\s*0)",
    re.IGNORECASE,
)


def _is_hidden(attrs: list[tuple[str, str | None]]) -> bool:
    for name, value in attrs:
        lname = name.lower()
        if lname == "hidden":
            return True
        if lname == "aria-hidden" and (value or "").lower() == "true":
            return True
        if lname == "style" and value and _HIDDEN_STYLE.search(value):
            return True
    return False


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._suppress_depth = 0  # inside a drop-content or hidden subtree
        self._suppress_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self._suppress_depth:
            if tag in _DROP_CONTENT_TAGS or _is_hidden(attrs):
                self._suppress_stack.append(tag)
                self._suppress_depth += 1
            return
        if tag in _DROP_CONTENT_TAGS or _is_hidden(attrs):
            self._suppress_stack.append(tag)
            self._suppress_depth += 1
            return
        if tag in _BLOCK_TAGS and self._parts and self._parts[-1] != "\n":
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._suppress_depth and self._suppress_stack and self._suppress_stack[-1] == tag:
            self._suppress_stack.pop()
            self._suppress_depth -= 1
            return
        if tag in _BLOCK_TAGS and self._parts and self._parts[-1] != "\n":
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._suppress_depth:
            return
        text = data.strip()
        if text:
            self._parts.append(text)

    # Comments are dropped: HTMLParser routes them to handle_comment,
    # which we deliberately do not implement to collect text.
    def handle_comment(self, data: str) -> None:
        return

    def markdown(self) -> str:
        raw = " ".join(p if p != "\n" else "\n" for p in self._parts)
        # Collapse runs of spaces, normalize blank lines.
        lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in raw.split("\n")]
        return "\n\n".join(ln for ln in lines if ln)


def extract_markdown(html: str) -> str:
    """Extract visible text as a markdown-ish string. Never raises on
    malformed HTML (html.parser is lenient)."""
    parser = _Extractor()
    parser.feed(html)
    parser.close()
    return parser.markdown()
