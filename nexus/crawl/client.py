from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib import error, request
from urllib.parse import urlparse

from .ssrf import SSRFGuard
from .types import CrawlRequest, Document

_ALLOWED_CONTENT_TYPES = {"text/html", "text/markdown", "application/xhtml+xml"}


class _MarkdownExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self._parts.append(text)

    def markdown(self) -> str:
        return "\n\n".join(self._parts)


class CrawlClient:
    def __init__(self, ssrf_guard: SSRFGuard | None = None, user_agent: str = "NexusAgenticSearch/0.1") -> None:
        self._ssrf = ssrf_guard or SSRFGuard()
        self._user_agent = user_agent

    def fetch(self, req: CrawlRequest) -> Document:
        requested = req.url
        now = datetime.now(timezone.utc)
        try:
            self._ssrf.validate_url(req.url)
        except ValueError:
            return self._empty_document(requested, now, "blocked_by_ssrf_guard", "", None, 0, [])

        render_start = time.perf_counter()
        req_obj = request.Request(req.url, headers={"User-Agent": self._user_agent})
        try:
            with request.urlopen(req_obj, timeout=req.timeout_s) as resp:
                final_url = resp.geturl()
                try:
                    self._ssrf.validate_url(final_url)
                except ValueError:
                    return self._empty_document(requested, now, "blocked_by_ssrf_guard", "", None, 0, [req.url])

                status = getattr(resp, "status", 200)
                content_type_header = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
                if content_type_header not in _ALLOWED_CONTENT_TYPES:
                    return self._empty_document(requested, now, "unsupported_content_type", final_url, status, 0, [final_url])

                body = resp.read(req.max_bytes + 1)
                bytes_in = len(body)
                if bytes_in > req.max_bytes:
                    return self._empty_document(requested, now, "too_large", final_url, status, bytes_in, [final_url])

                render_ms = int((time.perf_counter() - render_start) * 1000)
                extract_start = time.perf_counter()
                markdown = self._extract_markdown(body.decode("utf-8", errors="ignore"))
                extraction_ms = int((time.perf_counter() - extract_start) * 1000)
                content_hash = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
                return Document(
                    url=final_url,
                    requested_url=requested,
                    content_hash=content_hash,
                    markdown=markdown,
                    content_type=content_type_header,
                    fetched_at=now,
                    status="ok",
                    http_status=status,
                    bytes_in=bytes_in,
                    render_ms=render_ms,
                    extraction_ms=extraction_ms,
                    redirect_chain=[final_url],
                )
        except error.HTTPError as exc:
            mapped = "http_4xx" if 400 <= exc.code <= 499 else "http_5xx"
            return self._empty_document(requested, now, mapped, req.url, exc.code, 0, [req.url])
        except TimeoutError:
            return self._empty_document(requested, now, "timeout", req.url, None, 0, [req.url])
        except Exception:
            return self._empty_document(requested, now, "extraction_failed", req.url, None, 0, [req.url])

    def _extract_markdown(self, html: str) -> str:
        parser = _MarkdownExtractor()
        parser.feed(html)
        return parser.markdown()

    def _empty_document(self, requested_url: str, fetched_at: datetime, status: str, final_url: str, http_status: int | None, bytes_in: int, redirect_chain: list[str]) -> Document:
        return Document(
            url=final_url or requested_url,
            requested_url=requested_url,
            content_hash=hashlib.sha256(b"").hexdigest(),
            markdown="",
            content_type="",
            fetched_at=fetched_at,
            status=status,  # type: ignore[arg-type]
            http_status=http_status,
            bytes_in=bytes_in,
            render_ms=0,
            extraction_ms=0,
            redirect_chain=redirect_chain,
        )
