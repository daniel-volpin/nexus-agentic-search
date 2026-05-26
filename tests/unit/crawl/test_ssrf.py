from nexus.crawl.ssrf import SSRFGuard


def test_rejects_non_http_scheme() -> None:
    guard = SSRFGuard()
    try:
        guard.validate_url("file:///etc/passwd")
    except ValueError as exc:
        assert "scheme" in str(exc)
    else:
        raise AssertionError("Expected ValueError")


def test_rejects_private_literal() -> None:
    guard = SSRFGuard()
    try:
        guard.validate_url("http://127.0.0.1")
    except ValueError as exc:
        assert "literals" in str(exc)
    else:
        raise AssertionError("Expected ValueError")
