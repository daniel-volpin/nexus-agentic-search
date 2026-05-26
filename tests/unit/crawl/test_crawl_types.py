import pytest

from nexus.crawl.types import CrawlRequest


def test_crawl_request_validates_url() -> None:
    with pytest.raises(ValueError):
        CrawlRequest(url="   ")
