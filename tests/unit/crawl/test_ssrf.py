import pytest

from nexus.crawl.ssrf import SSRFGuard


def test_rejects_non_http_scheme() -> None:
    guard = SSRFGuard()
    with pytest.raises(ValueError, match="scheme"):
        guard.validate_url("file:///etc/passwd")


def test_rejects_private_literal() -> None:
    guard = SSRFGuard()
    with pytest.raises(ValueError, match="literals"):
        guard.validate_url("http://127.0.0.1")
