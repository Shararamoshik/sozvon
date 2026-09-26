"""LLM endpoint construction using the shared strict URL validator."""

from sozvon.shared.network import validated_base_url


def endpoint_url(base_url: str, protocol: str) -> str:
    suffix = "/chat/completions" if protocol == "openai" else "/v1/messages"
    return validated_base_url(base_url) + suffix
