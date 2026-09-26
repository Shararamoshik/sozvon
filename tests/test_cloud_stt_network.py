import pytest


def test_shared_url_validation_preserves_llm_contract():
    from sozvon.llm.network import endpoint_url
    from sozvon.shared.network import validated_base_url

    assert validated_base_url("http://localhost:1234/v1/") == "http://127.0.0.1:1234/v1"
    assert validated_base_url("https://api.example/custom/v1/") == "https://api.example/custom/v1"
    assert endpoint_url("https://api.example/custom/v1/", "openai") == (
        "https://api.example/custom/v1/chat/completions"
    )
    for url in [
        "http://external.example/v1",
        "https://user:key@api.example/v1",
        "https://api.example/v1?",
        "https://api.example/v1#",
        "https://api.example/v1\n",
        "https://api.example:0/v1",
        "https://api.example\\other/v1",
    ]:
        with pytest.raises(ValueError):
            validated_base_url(url)
