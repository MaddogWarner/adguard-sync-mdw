from __future__ import annotations

from urllib.parse import urlsplit


def parse_origin(value: str, *, referer: bool = False) -> tuple[str, str, int] | None:
    try:
        parsed = urlsplit(value)
        if (
            any(char.isspace() or ord(char) < 32 for char in value)
            or any(char in value for char in "*\\")
            or (not referer and any(char in value for char in "?#"))
            or parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or (not referer and (parsed.path or parsed.query or parsed.fragment))
        ):
            return None
        port = parsed.port
        if port is None:
            port = 443 if parsed.scheme == "https" else 80
        return (parsed.scheme, parsed.hostname.lower(), port)
    except ValueError:
        return None
