"""All provider traffic is intercepted by httpx.MockTransport; no live API calls."""

import json
from threading import Event

import httpx
import pytest


def payload(**updates):
    data = {
        "base_url": "https://api.example.test/v1",
        "protocol": "openai",
        "model": "test-model",
        "api_key": "test-only-key",
        "timeout": 15,
        "segments": [{"id": "s1", "text": "Анна подготовит план к пятнице."}],
        "title": "Планирование",
        "template": "meeting",
        "language": "ru",
    }
    data.update(updates)
    return data


def document():
    return {
        "summary": [
            {
                "text": "Обсудили подготовку плана.",
                "evidence": [{"segment_id": "s1", "quote": "Анна подготовит план"}],
            }
        ],
        "decisions": [],
        "proposals": [],
        "tasks": [
            {
                "text": "Подготовить план.",
                "owner": None,
                "due": None,
                "evidence": [{"segment_id": "s1", "quote": "план к пятнице"}],
            }
        ],
        "questions": [],
        "risks": [],
    }


def openai_response(doc=None, **extra):
    result = {
        "choices": [
            {
                "message": {"role": "assistant", "content": json.dumps(doc or document())},
                "finish_reason": "stop",
            }
        ]
    }
    result.update(extra)
    return result


@pytest.fixture
def mock_http(monkeypatch):
    real_client = httpx.Client
    calls = []
    configurations = []

    def install(handler):
        def record(request):
            calls.append(request)
            return handler(request)

        def client(**kwargs):
            configurations.append(kwargs.copy())
            return real_client(transport=httpx.MockTransport(record), **kwargs)

        monkeypatch.setattr(httpx, "Client", client)
        return calls, configurations

    return install


def test_openai_generates_structured_report_in_one_post(mock_http):
    from sozvon.llm.provider import generate

    usage = {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}
    calls, configurations = mock_http(
        lambda request: httpx.Response(200, json=openai_response(usage=usage))
    )
    events = []
    result = generate(payload(), Event(), events.append)

    snapshot = result.pop("template_snapshot")
    assert snapshot["id"] == "meeting" and snapshot["revision"] == 1
    assert snapshot["spec"]["name"] == "Рабочая встреча"
    assert len(snapshot["spec"]["sections"]) == 6
    assert result == {
        "document": document(),
        "model": "test-model",
        "usage": usage,
        "warnings": [],
    }
    assert len(calls) == 1
    request = calls[0]
    assert request.method == "POST"
    assert str(request.url) == "https://api.example.test/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer test-only-key"
    body = json.loads(request.content)
    assert body["model"] == "test-model"
    assert body["stream"] is False
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][0]["role"] == "system"
    context = json.loads(body["messages"][1]["content"])
    assert context["segments"][0]["text"] == payload()["segments"][0]["text"]
    assert context["title"] == "Планирование"
    assert configurations[0]["trust_env"] is False
    assert configurations[0]["follow_redirects"] is False
    assert events
    assert all(event["type"] == "progress" for event in events)
    assert "test-only-key" not in json.dumps(result, ensure_ascii=False)
    assert "test-only-key" not in json.dumps(events, ensure_ascii=False)


def test_provider_rejects_unverifiable_report_without_retry(mock_http):
    from sozvon.llm.provider import generate

    doc = document()
    doc["tasks"][0]["evidence"][0]["segment_id"] = "invented"
    calls, _ = mock_http(lambda request: httpx.Response(200, json=openai_response(doc)))
    with pytest.raises(ValueError, match="источник"):
        generate(payload(), Event(), lambda event: None)
    assert len(calls) == 1


def test_prompt_defines_schema_and_treats_transcript_as_untrusted_data(mock_http):
    from sozvon.llm.provider import generate

    calls, _ = mock_http(lambda request: httpx.Response(200, json=openai_response()))
    generate(payload(), Event(), lambda event: None)
    prompt = json.loads(calls[0].content)["messages"][0]["content"]
    for field in (*document(), "owner", "due", "evidence", "segment_id", "quote"):
        assert field in prompt
    assert "null" in prompt
    assert "инструкц" in prompt.lower()
    assert "недоверенн" in prompt.lower()


@pytest.mark.parametrize(
    "base_url",
    [
        "http://example.test/v1",
        "http://192.168.1.8/v1",
        "http://0.0.0.0/v1",
        "http://[::]/v1",
        "ftp://127.0.0.1/v1",
        "https://user:secret@example.test/v1",
        "https://example.test/v1?key=secret",
        "https://example.test/v1#secret",
        "/v1",
        "https://example.test:bad/v1",
        "https://example.test:99999/v1",
        "https://example.test\\n/v1",
        " http://127.0.0.1/v1",
        "http://127.1/v1",
        "http://2130706433/v1",
        "http://localhost.evil.test/v1",
        "http://[::1%25eth0]/v1",
        "https://example.test/\\\\secret",
        "https:///v1",
        "https://example.test/v1?",
    ],
)
def test_unsafe_endpoint_is_rejected_before_network(base_url, mock_http):
    from sozvon.llm.provider import generate

    calls, _ = mock_http(lambda request: httpx.Response(200, json=openai_response()))
    with pytest.raises(ValueError, match="адрес|HTTPS|URL") as caught:
        generate(payload(base_url=base_url), Event(), lambda event: None)
    assert calls == []
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize(
    "base_url",
    [
        "http://127.0.0.1:1234/v1",
        "http://127.0.0.2/v1/",
        "http://[::1]:1234/v1",
        "http://localhost:1234/v1",
        "https://example.test/custom/v1/",
    ],
)
def test_allowed_endpoint_and_empty_key(base_url, mock_http):
    from sozvon.llm.provider import generate

    calls, _ = mock_http(lambda request: httpx.Response(200, json=openai_response()))
    generate(payload(base_url=base_url, api_key=""), Event(), lambda event: None)
    assert len(calls) == 1
    expected_base = base_url.replace("http://localhost:", "http://127.0.0.1:")
    assert str(calls[0].url) == expected_base.rstrip("/") + "/chat/completions"
    assert "authorization" not in calls[0].headers


def test_redirect_is_not_followed_or_returned_as_report(mock_http):
    from sozvon.llm.provider import generate

    calls, _ = mock_http(
        lambda request: httpx.Response(
            307, headers={"location": "https://evil.example/secret"}, json=openai_response()
        )
    )
    with pytest.raises(RuntimeError, match="перенаправлен") as caught:
        generate(payload(), Event(), lambda event: None)
    assert len(calls) == 1
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("base_url", ["https://anthropic.example.test", "https://proxy.test/llm/"])
def test_anthropic_generates_with_messages_protocol(base_url, mock_http):
    from sozvon.llm.provider import generate

    text = json.dumps(document())
    usage = {"input_tokens": 25, "output_tokens": 40}
    calls, _ = mock_http(
        lambda request: httpx.Response(
            200,
            json={
                "type": "message",
                "model": "provider-name",
                "content": [
                    {"type": "text", "text": text[:20]},
                    {"type": "text", "text": text[20:]},
                ],
                "stop_reason": "end_turn",
                "usage": usage,
            },
        )
    )
    result = generate(payload(protocol="anthropic", base_url=base_url), Event(), lambda event: None)
    assert result["document"] == document()
    assert result["usage"] == usage
    assert result["model"] == "test-model"
    assert len(calls) == 1
    request = calls[0]
    assert str(request.url) == base_url.rstrip("/") + "/v1/messages"
    assert request.headers["x-api-key"] == "test-only-key"
    assert request.headers["anthropic-version"] == "2023-06-01"
    assert "authorization" not in request.headers
    body = json.loads(request.content)
    assert isinstance(body["system"], str)
    assert len(body["messages"]) == 1
    assert body["messages"][0]["role"] == "user"
    assert body["max_tokens"] > 0
    assert body["stream"] is False
    assert "response_format" not in body


@pytest.mark.parametrize(
    "updates",
    [
        {"protocol": "other"},
        {"model": "\u2003"},
        {"model": None},
        {"segments": []},
        {"segments": [{"id": "s1", "text": "  "}]},
        {"segments": [{"id": True, "text": "Текст"}]},
        {"segments": [{"id": "s1", "text": "А"}, {"id": "s1", "text": "Б"}]},
        {"segments": [{"id": "s1"}]},
        {"template": "made-up"},
        {"language": ""},
        {"timeout": 0},
        {"timeout": -1},
        {"timeout": float("nan")},
        {"timeout": float("inf")},
        {"timeout": 9000},
        {"timeout": True},
        {"api_key": "secret\nheader"},
        {"api_key": "секрет"},
    ],
)
def test_invalid_input_is_rejected_without_request(updates, mock_http):
    from sozvon.llm.provider import generate

    calls, _ = mock_http(lambda request: httpx.Response(200, json=openai_response()))
    with pytest.raises(ValueError) as caught:
        generate(payload(**updates), Event(), lambda event: None)
    assert calls == []
    assert "secret" not in str(caught.value)
    assert "секрет" not in str(caught.value)


def test_large_input_is_rejected_explicitly_not_truncated(mock_http):
    from sozvon.llm.provider import MAX_INPUT_BYTES, generate

    calls, _ = mock_http(lambda request: httpx.Response(200, json=openai_response()))
    with pytest.raises(ValueError, match="прототип|длинн") as caught:
        generate(
            payload(segments=[{"id": "s1", "text": "Ж" * MAX_INPUT_BYTES}]),
            Event(),
            lambda event: None,
        )
    assert str(MAX_INPUT_BYTES) in str(caught.value)
    assert calls == []


def test_default_options_and_optional_usage(mock_http):
    from sozvon.llm.provider import generate

    calls, configurations = mock_http(lambda request: httpx.Response(200, json=openai_response()))
    data = payload()
    for key in ("timeout", "api_key", "template", "language", "title"):
        data.pop(key)
    result = generate(data, Event(), lambda event: None)
    assert result["usage"] is None
    assert len(calls) == 1
    assert configurations[0]["timeout"] == 120


def test_stop_before_request_never_sends(mock_http):
    from sozvon.llm.provider import generate

    calls, _ = mock_http(lambda request: httpx.Response(200, json=openai_response()))
    stopped = Event()
    stopped.set()
    with pytest.raises(RuntimeError, match="отмен"):
        generate(payload(), stopped, lambda event: None)
    assert calls == []


def test_stop_during_request_never_returns_success(mock_http):
    from sozvon.llm.provider import generate

    stopped = Event()

    def respond(request):
        stopped.set()
        return httpx.Response(200, json=openai_response())

    calls, _ = mock_http(respond)
    with pytest.raises(RuntimeError, match="отмен"):
        generate(payload(), stopped, lambda event: None)
    assert len(calls) == 1


@pytest.mark.parametrize("status", [400, 401, 403, 404, 413, 429, 500, 503, 524])
def test_http_errors_are_safe_and_never_retried(status, mock_http):
    from sozvon.llm.provider import generate

    calls, _ = mock_http(lambda request: httpx.Response(status, text="secret test-only-key"))
    with pytest.raises(RuntimeError) as caught:
        generate(payload(), Event(), lambda event: None)
    assert str(status) in str(caught.value)
    assert "secret" not in str(caught.value)
    assert "test-only-key" not in str(caught.value)
    assert len(calls) == 1


@pytest.mark.parametrize("response_body", [b"", b"<html>secret</html>", b"null", b"[]", b"{}"])
def test_malformed_envelope_is_a_safe_error(response_body, mock_http):
    from sozvon.llm.provider import generate

    calls, _ = mock_http(lambda request: httpx.Response(200, content=response_body))
    with pytest.raises(RuntimeError, match="ответ|JSON") as caught:
        generate(payload(), Event(), lambda event: None)
    assert "secret" not in str(caught.value)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "content",
    [
        None,
        "",
        "\u2003\n",
        "secret broken JSON",
        "```json\n{}\n```",
        [],
        {},
        '{"summary": [], "summary": []}',
        "NaN",
    ],
)
def test_empty_or_malformed_model_json_is_not_success(content, mock_http):
    from sozvon.llm.provider import generate

    data = openai_response()
    data["choices"][0]["message"]["content"] = content
    calls, _ = mock_http(lambda request: httpx.Response(200, json=data))
    with pytest.raises((ValueError, RuntimeError), match="ответ|JSON|отчёт") as caught:
        generate(payload(), Event(), lambda event: None)
    assert "secret" not in str(caught.value)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "protocol,reason",
    [
        ("openai", "length"),
        ("openai", "content_filter"),
        ("openai", "tool_calls"),
        ("anthropic", "max_tokens"),
        ("anthropic", "tool_use"),
        ("anthropic", "refusal"),
    ],
)
def test_incomplete_or_refused_reply_cannot_be_success(protocol, reason, mock_http):
    from sozvon.llm.provider import generate

    if protocol == "openai":
        data = openai_response()
        data["choices"][0]["finish_reason"] = reason
    else:
        data = {
            "content": [{"type": "text", "text": json.dumps(document())}],
            "stop_reason": reason,
        }
    calls, _ = mock_http(lambda request: httpx.Response(200, json=data))
    with pytest.raises(RuntimeError, match="ответ|отчёт"):
        generate(payload(protocol=protocol), Event(), lambda event: None)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "error_type", [httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError]
)
def test_network_errors_do_not_expose_request_or_secret(error_type, mock_http):
    from sozvon.llm.provider import generate

    def respond(request):
        raise error_type("secret test-only-key", request=request)

    calls, _ = mock_http(respond)
    with pytest.raises(RuntimeError, match="API|врем") as caught:
        generate(payload(), Event(), lambda event: None)
    assert "secret" not in str(caught.value)
    assert "test-only-key" not in str(caught.value)
    assert caught.value.__suppress_context__
    assert len(calls) == 1


def test_usage_is_allowlisted_and_invalid_usage_optional(mock_http):
    from sozvon.llm.provider import generate

    for usage, expected in [
        (None, None),
        ("secret", None),
        ([], None),
        (
            {"prompt_tokens": 10, "private": "test-only-key", "total_tokens": -1},
            {"prompt_tokens": 10},
        ),
        ({"input_tokens": True, "output_tokens": "test-only-key"}, None),
    ]:
        mock_http(
            lambda request, usage=usage: httpx.Response(200, json=openai_response(usage=usage))
        )
        result = generate(payload(), Event(), lambda event: None)
        assert result["usage"] == expected
        assert "test-only-key" not in json.dumps(result)


def test_response_size_is_bounded_before_parsing(mock_http):
    from sozvon.llm.provider import MAX_RESPONSE_BYTES, generate

    consumed = []

    class Chunks(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(100):
                consumed.append(True)
                yield b"x" * 65_536

    calls, _ = mock_http(lambda request: httpx.Response(200, stream=Chunks()))
    with pytest.raises(RuntimeError, match="больш|размер|лимит"):
        generate(payload(), Event(), lambda event: None)
    assert len(calls) == 1
    assert len(consumed) * 65_536 <= MAX_RESPONSE_BYTES + 65_536


def test_error_response_body_is_not_consumed(mock_http):
    from sozvon.llm.provider import generate

    class Unreadable(httpx.SyncByteStream):
        def __iter__(self):
            raise AssertionError("Error bodies may carry secrets; do not read them")
            yield b""  # pragma: no cover

    mock_http(lambda request: httpx.Response(401, stream=Unreadable()))
    with pytest.raises(RuntimeError, match="401"):
        generate(payload(), Event(), lambda event: None)


def test_provider_cannot_echo_api_key_in_report(mock_http):
    from sozvon.llm.provider import generate

    doc = document()
    doc["summary"][0]["text"] = "test-only-key"
    mock_http(lambda request: httpx.Response(200, json=openai_response(doc)))
    with pytest.raises(RuntimeError) as caught:
        generate(payload(), Event(), lambda event: None)
    assert "test-only-key" not in str(caught.value)


def test_output_token_budget_is_explicit_for_openai(mock_http):
    from sozvon.llm.provider import generate

    calls, _ = mock_http(lambda request: httpx.Response(200, json=openai_response()))
    generate(payload(), Event(), lambda event: None)
    assert json.loads(calls[0].content)["max_tokens"] == 4096


def test_quoted_key_cannot_leak_via_document(mock_http):
    from sozvon.llm.provider import generate

    key = 'test"quoted\\key'
    doc = document()
    doc["tasks"][0]["owner"] = key
    mock_http(lambda request: httpx.Response(200, json=openai_response(doc)))
    with pytest.raises(RuntimeError, match="конфиденциальн"):
        generate(payload(api_key=key), Event(), lambda event: None)


def test_invalid_utf8_in_report_fails_before_worker_serialization(mock_http):
    from sozvon.llm.provider import generate

    doc = document()
    doc["summary"][0]["text"] = "\ud800"
    mock_http(lambda request: httpx.Response(200, json=openai_response(doc)))
    with pytest.raises((ValueError, RuntimeError), match="отчёт|ответ|JSON"):
        generate(payload(), Event(), lambda event: None)


def test_localhost_resolution_cannot_redirect_http_off_loopback(mock_http, monkeypatch):
    import socket

    from sozvon.llm.provider import generate

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.22", 80))],
    )
    calls, _ = mock_http(lambda request: httpx.Response(200, json=openai_response()))
    # Pin localhost to the loopback address instead of trusting DNS for cleartext auth.
    generate(payload(base_url="http://localhost/v1"), Event(), lambda event: None)
    assert calls[0].url.host in ("127.0.0.1", "::1")


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
def test_full_size_context_reaches_provider_without_truncation(protocol, mock_http):
    from sozvon.llm.provider import MAX_INPUT_BYTES, generate
    from sozvon.llm.request import prepare_request

    data = payload(protocol=protocol)
    data["segments"][0].update(ordinal=0, start_ms=0, end_ms=1200, speaker=None)
    _, original = prepare_request(data)
    data["segments"][0]["text"] += "x" * (MAX_INPUT_BYTES - len(original.encode("utf-8")))
    _, exact = prepare_request(data)
    assert len(exact.encode("utf-8")) == MAX_INPUT_BYTES
    reply = (
        openai_response()
        if protocol == "openai"
        else {
            "content": [{"type": "text", "text": json.dumps(document())}],
            "stop_reason": "end_turn",
        }
    )
    calls, _ = mock_http(lambda request: httpx.Response(200, json=reply))
    generate(data, Event(), lambda event: None)
    body = json.loads(calls[0].content)
    user_message = next(message for message in body["messages"] if message["role"] == "user")
    assert json.loads(user_message["content"])["segments"] == data["segments"]
    data["segments"][0]["text"] += "x"
    with pytest.raises(ValueError, match="64000"):
        generate(data, Event(), lambda event: None)
    assert len(calls) == 1


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
def test_stop_while_reading_closes_transport(protocol, mock_http):
    from sozvon.llm.provider import generate

    stopped = Event()
    closed = []

    class Interrupted(httpx.SyncByteStream):
        def __iter__(self):
            stopped.set()
            yield b" " * 65_536
            raise AssertionError("Reading must end as soon as stop is observed")

        def close(self):
            closed.append(True)

    calls, _ = mock_http(lambda request: httpx.Response(200, stream=Interrupted()))
    with pytest.raises(RuntimeError, match="отмен"):
        generate(payload(protocol=protocol), stopped, lambda event: None)
    assert len(calls) == 1
    assert closed == [True]
