"""Синтетические данные; никакой настоящей сети или ключей."""
import json
from threading import Event

import httpx
import pytest

from sozvon.llm.request import MAX_INPUT_BYTES, prepare_request
from sozvon.templates.builtin import builtin_spec


def payload():
    return {"base_url": "https://synthetic.test/v1", "protocol": "openai", "model": "synthetic",
            "segments": [{"id": "s1", "text": "План подготовят"}], "template": "my-template",
            "template_snapshot": {"id": "my-template", "revision": 2,
                                  "spec": builtin_spec("meeting").model_dump()}}


def test_request_uses_detached_validated_snapshot():
    data = payload()
    request, context = prepare_request(data)
    assert json.loads(context)["template_snapshot"] == data["template_snapshot"]
    assert request.template_snapshot == data["template_snapshot"]
    data["template_snapshot"]["spec"]["name"] = "Теперь другое"
    assert request.template_snapshot["spec"]["name"] != "Теперь другое"


def test_legacy_builtin_request_gets_snapshot():
    data = payload()
    data.pop("template_snapshot")
    data["template"] = "technical"
    request, context = prepare_request(data)
    assert request.template_snapshot == {"id": "technical", "revision": 1, "spec": builtin_spec("technical").model_dump()}
    assert json.loads(context)["template_snapshot"] == request.template_snapshot
    data["template"] = "unknown"
    with pytest.raises(ValueError):
        prepare_request(data)


@pytest.mark.parametrize("change", ["id", "revision", "missing", "extra", "spec"])
def test_request_rejects_invalid_snapshot(change):
    data = payload()
    snap = data["template_snapshot"]
    if change == "id":
        snap["id"] = "other"
    elif change == "revision":
        snap["revision"] = True
    elif change == "missing":
        snap.pop("revision")
    elif change == "extra":
        snap["api_key"] = "not-allowed"
    elif change == "spec":
        snap["spec"]["sections"] = []
    with pytest.raises(ValueError):
        prepare_request(data)


def test_generated_result_preserves_exact_snapshot(monkeypatch):
    from sozvon.llm.provider import generate
    from sozvon.templates.schema import CORE
    data = payload()
    snapshot = json.loads(json.dumps(data["template_snapshot"]))
    doc = {key: [] for key in CORE}
    doc["summary"] = [{"text": "Есть план", "evidence": [{"segment_id": "s1", "quote": "План"}]}]
    real_client = httpx.Client
    calls = []

    def respond(request):
        body = json.loads(request.content)
        calls.append(body)
        data["template_snapshot"]["spec"]["name"] = "Поменялось во время запроса"
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(doc)}, "finish_reason": "stop"}]})

    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw))
    result = generate(data, Event(), lambda event: None)
    assert result["template_snapshot"] == snapshot
    assert json.loads(calls[0]["messages"][1]["content"])["template_snapshot"] == snapshot
    prompt = calls[0]["messages"][0]["content"]
    assert "template_snapshot" in prompt and "отключён" in prompt
    assert len(calls) == 1


def test_snapshot_cannot_leak_api_key_in_result(monkeypatch):
    from sozvon.llm.provider import generate
    from sozvon.templates.schema import CORE
    data = payload()
    data["api_key"] = "synthetic-only-key"
    data["template_snapshot"]["spec"]["instructions"] = data["api_key"]
    doc = {key: [] for key in CORE}
    doc["summary"] = [{"text": "Есть план", "evidence": [{"segment_id": "s1", "quote": "План"}]}]
    real_client = httpx.Client
    def respond(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(doc)}, "finish_reason": "stop"}]})
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw))
    with pytest.raises((ValueError, RuntimeError), match="конфиденциальн") as caught:
        generate(data, Event(), lambda event: None)
    assert data["api_key"] not in str(caught.value)


def test_snapshot_language_controls_request_context():
    data = payload()
    data["template_snapshot"]["spec"]["language"] = "en"
    request, context = prepare_request(data)
    assert request.language == "en"
    assert json.loads(context)["language"] == "en"


def test_snapshot_counts_toward_exact_context_budget():
    data = payload()
    data["template_snapshot"]["spec"]["instructions"] = "Подробно " * 100
    _, context = prepare_request(data)
    data["segments"][0]["text"] += "x" * (MAX_INPUT_BYTES - len(context.encode("utf-8")))
    _, exact = prepare_request(data)
    assert len(exact.encode("utf-8")) == MAX_INPUT_BYTES
    assert json.loads(exact)["template_snapshot"] == data["template_snapshot"]
    data["template_snapshot"]["spec"]["instructions"] += "x"
    with pytest.raises(ValueError, match="64000"):
        prepare_request(data)


def test_provider_enforces_snapshot_custom_sections_without_retry(monkeypatch):
    from uuid import uuid4

    from sozvon.llm.provider import generate
    from sozvon.templates.schema import CORE
    data = payload()
    data["template_snapshot"]["spec"]["sections"].append({
        "key": "custom_" + uuid4().hex, "title": "Обязательный раздел", "kind": "tasks", "enabled": True})
    doc = {key: [] for key in CORE}
    doc["summary"] = [{"text": "Есть план", "evidence": [{"segment_id": "s1", "quote": "План"}]}]
    calls = []
    real_client = httpx.Client
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(doc)}, "finish_reason": "stop"}]})
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw))
    with pytest.raises(ValueError, match="раздел"):
        generate(data, Event(), lambda event: None)
    assert len(calls) == 1


def test_provider_returns_valid_custom_task_in_saved_snapshot(monkeypatch, tmp_path):
    from uuid import uuid4

    from sozvon.llm.provider import generate
    from sozvon.storage.repository import Repository
    from sozvon.templates.schema import CORE

    data = payload()
    key = "custom_" + uuid4().hex
    data["template_snapshot"]["spec"]["sections"].append({
        "key": key, "title": "Мой раздел", "kind": "tasks", "enabled": True})
    doc = {name: [] for name in CORE}
    doc["custom_sections"] = {key: {"kind": "tasks", "items": [{"text": "План",
        "owner": None, "due": None, "evidence": [{"segment_id": "s1", "quote": "План"}]}]}}
    real_client = httpx.Client
    def respond(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(doc)}, "finish_reason": "stop"}]})
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw))
    result = generate(data, Event(), lambda event: None)
    assert result["document"] == doc
    repo = Repository(tmp_path)
    mid = repo.create_text("Синтетическая встреча", "План подготовят")
    repo.save_report(mid, result, transcript_revision=1)
    report = repo.detail(mid)["reports"][0]
    assert report["meta"]["template_snapshot"] == result["template_snapshot"]
    assert report["sections"][-1]["items"][0]["due"] is None
    assert report["sections"][-1]["title"] == "Мой раздел"
