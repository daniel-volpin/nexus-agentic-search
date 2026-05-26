from __future__ import annotations

from html import escape


def wrap_untrusted(url: str, content_hash: str, body: str) -> str:
    safe_url = escape(url, quote=True)
    safe_hash = escape(content_hash, quote=True)
    safe_body = body.replace("</untrusted_source>", "<\\/untrusted_source>")
    return f'<untrusted_source url="{safe_url}" sha256="{safe_hash}">{safe_body}</untrusted_source>'
