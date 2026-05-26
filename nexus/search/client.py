from __future__ import annotations

from typing import Protocol

from .types import SearchRequest, SearchResponse


class SearchClient(Protocol):
    async def search(self, req: SearchRequest) -> SearchResponse: ...
