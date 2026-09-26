"""Safe provider diagnostics; all replies are local synthetic fixtures."""

import pytest

from sozvon.llm.response import report_text

REPORT = '{"summary": [], "tasks": []}'
PRIVATE = "PRIVATE-key-content-reasoning-refusal"


def reply(protocol, reason, *, content=REPORT):
    if protocol == "openai":
        return {"choices": [{"finish_reason": reason, "message": {
            "content": content, "reasoning_content": PRIVATE,
        }}]}
    return {"stop_reason": reason, "content": [{"type": "text", "text": content}]}


@pytest.mark.parametrize("protocol,reason", [("openai", "length"), ("anthropic", "max_tokens")])
def test_token_limit_is_distinct_even_with_valid_json(protocol, reason):
    with pytest.raises(RuntimeError, match="ответ|отчёт") as caught:
        report_text(reply(protocol, reason), protocol)
    message = str(caught.value)
    assert f"reason={reason}" in message
    assert "лимит" in message.lower()
    assert "повтора не было" in message.lower()
    assert PRIVATE not in message and REPORT not in message
    assert len(message) <= 500
