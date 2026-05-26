from __future__ import annotations

from fastapi import Header, HTTPException


def require_bearer_token(expected_token: str, authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={"error": "unauthorized"})
    token = authorization[len("Bearer ") :]
    if token != expected_token:
        raise HTTPException(status_code=401, detail={"error": "unauthorized"})
    return token
