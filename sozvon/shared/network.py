"""Endpoint validation shared by LLM and STT: no credentials or redirects."""

from ipaddress import ip_address
from urllib.parse import urlsplit

import httpx


def is_loopback_url(base_url: str) -> bool:
    host = urlsplit(base_url).hostname
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def validated_base_url(base_url: str) -> str:
    """Validate before client construction; never include the input in errors."""
    error = "Некорректный адрес API: нужен HTTPS URL без логина, query и fragment."
    if not isinstance(base_url, str) or any(
        char.isspace() or ord(char) < 32 or ord(char) == 127 for char in base_url
    ):
        raise ValueError(error)
    if any(char in base_url for char in ("\\", "?", "#")):
        raise ValueError(error)
    try:
        parsed = urlsplit(base_url)
        url = httpx.URL(base_url)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or not url.host
            or "@" in parsed.netloc
            or "%" in parsed.netloc
            or (parsed.port is not None and not 1 <= parsed.port <= 65535)
        ):
            raise ValueError
    except (ValueError, httpx.InvalidURL):
        raise ValueError(error) from None
    if url.scheme == "http":
        try:
            local = ip_address(url.host).is_loopback
        except ValueError:
            local = url.host == "localhost"
        if not local:
            raise ValueError("Для удалённого API обязателен HTTPS; HTTP разрешён только loopback.")
    if url.scheme == "http" and url.host == "localhost":
        # Pin plaintext localhost: a hosts/DNS override must not receive a key.
        base_url = str(url.copy_with(host="127.0.0.1"))
    return base_url.rstrip("/")
