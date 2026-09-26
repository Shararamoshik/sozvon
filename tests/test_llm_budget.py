"""Бюджет отчёта сохраняется и доходит до реального одноразового HTTP-запроса."""
import json
from threading import Event

import httpx
import pytest
from test_llm_provider import document, openai_response, payload
from test_llm_provider import (
    mock_http as mock_http,  # noqa: PLC0414 - повторно экспортируем fixture
)

from sozvon.config import SettingsStore
from sozvon.services.application import Application


def test_old_config_defaults_and_saved_budget_reach_job(tmp_path, monkeypatch):
    path = tmp_path / 'config.toml'
    path.write_text('[llm]\nmodel = "deepseek/deepseek-v4.1-flash"\n', encoding='utf-8')
    monkeypatch.setattr('keyring.get_password', lambda *args: None)
    store = SettingsStore(tmp_path)
    assert store.public()['llm']['max_output_tokens'] == 16384
    assert store.public()['llm']['timeout_s'] == 300
    store.save({'llm': {'max_output_tokens': 24576, 'timeout_s': 450}})
    app = Application(tmp_path)
    calls = []
    monkeypatch.setattr(app, '_submit', lambda *args, **kwargs: calls.append((args, kwargs)) or 'job')
    try:
        mid = app.repo.create_text('Проверка бюджета', 'Синтетическая расшифровка.')
        assert app.report(mid) == 'job'
        args, kwargs = calls[0]
        assert args[2]['max_output_tokens'] == 24576
        assert args[2]['timeout'] == 450
        assert args[3] == 465
        assert kwargs['transcript_revision'] == 1
        assert app.settings.public()['llm']['model'] == 'deepseek/deepseek-v4.1-flash'
        app.settings.save({'llm': {'model': 'another-model'}})
        assert app.settings.snapshot()['llm']['max_output_tokens'] == 24576
        assert app.settings.snapshot()['llm']['timeout_s'] == 450
    finally:
        app.close()


@pytest.mark.parametrize('field,value', [
    ('max_output_tokens', 255), ('max_output_tokens', 65537),
    ('max_output_tokens', True), ('max_output_tokens', '16384'),
    ('max_output_tokens', 4096.5), ('max_output_tokens', None),
    ('timeout_s', 9), ('timeout_s', 601), ('timeout_s', True),
    ('timeout_s', '300'), ('timeout_s', 12.5), ('timeout_s', None),
])
def test_invalid_budget_does_not_mutate_config(tmp_path, field, value):
    store = SettingsStore(tmp_path)
    store.save({'llm': {'model': 'unchanged'}})
    before = store.path.read_bytes()
    with pytest.raises(ValueError):
        store.save({'llm': {field: value}})
    assert store.path.read_bytes() == before


@pytest.mark.parametrize('protocol', ['openai', 'anthropic'])
@pytest.mark.parametrize('budget', [256, 24576, 65536])
def test_configured_budget_reaches_one_post(protocol, budget, mock_http):
    from sozvon.llm.provider import generate

    reply = openai_response() if protocol == 'openai' else {
        'stop_reason': 'end_turn',
        'content': [{'type': 'text', 'text': json.dumps(document())}],
    }
    calls, configurations = mock_http(lambda request: httpx.Response(200, json=reply))
    result = generate(payload(protocol=protocol, max_output_tokens=budget, timeout=450), Event(), lambda _: None)
    assert result['document'] == document()
    assert len(calls) == 1
    assert json.loads(calls[0].content)['max_tokens'] == budget
    assert configurations[0]['timeout'] == 450


@pytest.mark.parametrize('value', [255, 65537, True, '16384', 4096.5, None])
def test_invalid_worker_budget_never_sends(value, mock_http):
    from sozvon.llm.provider import generate

    calls, _ = mock_http(lambda request: httpx.Response(200, json=openai_response()))
    with pytest.raises(ValueError):
        generate(payload(max_output_tokens=value), Event(), lambda _: None)
    assert not calls
