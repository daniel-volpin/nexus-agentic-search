from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACK_EXACT = {
    "gclid",
    "fbclid",
    "ref",
    "ref_src",
    "_hsenc",
    "_hsmi",
    "igshid",
    "vero_id",
    "mkt_tok",
    "yclid",
}
_TRACK_PREFIX = ("utm_", "mc_")


def _is_unwanted_param(name: str) -> bool:
    key = name.lower()
    if key in _TRACK_EXACT:
        return True
    return any(key.startswith(prefix) for prefix in _TRACK_PREFIX)


def canonicalize(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme.lower() not in {"http", "https"}:
        return ""

    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if not host:
        return ""

    port = parts.port
    netloc = host
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"

    path = parts.path or "/"
    while "//" in path:
        path = path.replace("//", "/")
    if path != "/" and path.endswith("/"):
        path = path[:-1]

    params = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _is_unwanted_param(k)]
    params.sort(key=lambda item: (item[0], item[1]))
    query = urlencode(params, doseq=True)

    return urlunsplit((scheme, netloc, path, query, ""))
