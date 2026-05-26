import pytest
from pydantic import ValidationError as PydanticValidationError

from nexus.search.types import SearchRequest


def test_query_rejects_whitespace() -> None:
    with pytest.raises(PydanticValidationError):
        SearchRequest(query="   ")


def test_query_rejects_too_long() -> None:
    with pytest.raises(PydanticValidationError):
        SearchRequest(query="x" * 513)


def test_query_strips_control_chars() -> None:
    req = SearchRequest(query="hello\x00world")
    assert req.query == "helloworld"
