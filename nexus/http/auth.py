from __future__ import annotations

import hmac

from fastapi import HTTPException


def require_bearer_token(expected_token: str, authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={"error": "unauthorized"})
    token = authorization[len("Bearer ") :]
    # Constant-time comparison: defeats observation of partial-prefix
    # match via timing side-channel (Spec 10).
    if not hmac.compare_digest(token, expected_token):
        raise HTTPException(status_code=401, detail={"error": "unauthorized"})
    return token
