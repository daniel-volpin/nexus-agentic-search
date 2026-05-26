from nexus.search.canonical import canonicalize


def test_canonical_strips_tracking_and_fragment() -> None:
    assert canonicalize("https://Example.com/path/?utm_source=x&id=1#frag") == "https://example.com/path?id=1"


def test_canonical_rejects_non_http_scheme() -> None:
    assert canonicalize("javascript:alert(1)") == ""
