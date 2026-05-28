"""Adversarial tests for the crawler's redirect re-validation
(Spec 03 §Failure modes + Spec 10 §SSRF guard).

We mock ``urllib.request.urlopen`` so the test exercises the crawler's
own logic — specifically the re-validation of the *final* URL after
redirects — without needing a reachable server (a live loopback
fixture would be blocked by the SSRF guard anyway, since 127.0.0.1 is
loopback).

The initial URL uses a public IP literal allowlisted via
``public_ip_allow`` so the first guard call passes; the mocked
response's ``geturl()`` then reports a redirect into a blocked range,
which the crawler must catch.
"""

from __future__ import annotations

from types import TracebackType
from unittest.mock import patch

import pytest

from nexus.crawl import CrawlClient, CrawlRequest
from nexus.crawl.ssrf import SSRFGuard

pytestmark = pytest.mark.security

_PUBLIC_LITERAL = "93.184.216.34"
_START_URL = f"http://{_PUBLIC_LITERAL}/start"


class _FakeHeaders:
    def __init__(self, content_type: str = "text/html") -> None:
        self._content_type = content_type

    def get(self, name: str, default: str = "") -> str:
        if name.lower() == "content-type":
            return self._content_type
        return default


class _FakeResponse:
    """Mimics the slice of http.client.HTTPResponse that CrawlClient
    uses: context manager, geturl(), status, headers, read()."""

    def __init__(self, *, final_url: str, body: bytes = b"<p>x</p>") -> None:
        self._final_url = final_url
        self._body = body
        self.status = 200
        self.headers = _FakeHeaders()

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    def geturl(self) -> str:
        return self._final_url

    def read(self, _n: int = -1) -> bytes:
        return self._body


def _client_allowing_start() -> CrawlClient:
    # Allow the public literal so the FIRST guard call passes; the
    # redirect target is what we want the crawler to reject.
    return CrawlClient(ssrf_guard=SSRFGuard(public_ip_allow={_PUBLIC_LITERAL}))


@pytest.mark.parametrize(
    "redirect_target",
    [
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://10.0.0.1/internal",  # RFC1918
        "http://127.0.0.1/admin",  # loopback
        "http://192.168.1.1/router",  # RFC1918
        "http://[::1]/",  # loopback v6
    ],
)
def test_redirect_final_url_into_blocked_range_is_rejected(
    redirect_target: str,
) -> None:
    """Spec 03: when ``urlopen`` follows a redirect whose final URL is
    in a blocked range, the crawler MUST classify the document as
    ``blocked_by_ssrf_guard`` and surface no body."""
    client = _client_allowing_start()
    fake = _FakeResponse(final_url=redirect_target, body=b"<p>secret</p>")
    with patch("nexus.crawl.client.request.urlopen", return_value=fake):
        doc = client.fetch(CrawlRequest(url=_START_URL))
    assert doc.status == "blocked_by_ssrf_guard"
    assert doc.markdown == "", "blocked documents must not surface body content"


def test_redirect_to_public_final_url_is_allowed() -> None:
    """Negative control: a redirect whose final URL is still public is
    fetched normally — proves the rejection above is specific to
    blocked ranges, not all redirects."""
    client = _client_allowing_start()
    fake = _FakeResponse(
        final_url=f"http://{_PUBLIC_LITERAL}/final",
        body=b"<html><body><p>hello</p></body></html>",
    )
    with patch("nexus.crawl.client.request.urlopen", return_value=fake):
        doc = client.fetch(CrawlRequest(url=_START_URL))
    assert doc.status == "ok"
    assert "hello" in doc.markdown
    assert doc.url == f"http://{_PUBLIC_LITERAL}/final"


def test_initial_url_in_blocked_range_never_calls_urlopen() -> None:
    """The first guard call must reject a blocked initial URL BEFORE
    any network attempt — urlopen is never invoked."""
    client = CrawlClient()  # default guard, no allowlist
    with patch("nexus.crawl.client.request.urlopen") as urlopen:
        doc = client.fetch(CrawlRequest(url="http://169.254.169.254/"))
    assert doc.status == "blocked_by_ssrf_guard"
    urlopen.assert_not_called()
