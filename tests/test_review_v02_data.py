"""Регрессии review v0.2: частичные правки и язык снимка шаблона."""
import json

import pytest

from sozvon.llm.request import prepare_request
from sozvon.storage.repository import Repository
from sozvon.storage.transcripts import list_revisions, read_revision, save_edits
from sozvon.templates.builtin import builtin_spec
from sozvon.transcripts.schema import TranscriptEdit


@pytest.fixture
def spoken_transcript(tmp_path):
    repo = Repository(tmp_path)
    mid = repo.create("Встреча", "audio")
    repo.save_segments(mid, [{
        "text": "Исходник", "speaker": "Анна", "start_ms": 123, "end_ms": 456,
    }])
    return repo, mid, read_revision(repo, mid, 1)


def test_omitted_speaker_noop_keeps_revision(spoken_transcript):
    repo, mid, original = spoken_transcript
    history = list_revisions(repo, mid)
    request = TranscriptEdit.model_validate({
        "base_revision": 1,
        "edits": [{"id": original[0]["id"], "text": original[0]["text"]}],
    })

    assert save_edits(repo, mid, request) == 1
    assert repo.detail(mid)["segments"] == original
    assert list_revisions(repo, mid) == history


def test_text_edit_without_speaker_keeps_existing_speaker(spoken_transcript):
    repo, mid, original = spoken_transcript
    request = TranscriptEdit.model_validate({
        "base_revision": 1,
        "edits": [{"id": original[0]["id"], "text": "Правка"}],
    })

    assert save_edits(repo, mid, request) == 2
    current = read_revision(repo, mid, 2)[0]
    assert current["text"] == "Правка"
    assert current["speaker"] == "Анна"
    assert (current["start_ms"], current["end_ms"]) == (123, 456)
    assert read_revision(repo, mid, 1) == original


def test_explicit_null_speaker_clears_existing_speaker(spoken_transcript):
    repo, mid, original = spoken_transcript
    request = TranscriptEdit.model_validate({
        "base_revision": 1,
        "edits": [{"id": original[0]["id"], "text": original[0]["text"], "speaker": None}],
    })

    assert save_edits(repo, mid, request) == 2
    current = read_revision(repo, mid, 2)[0]
    assert current["speaker"] is None
    assert current["text"] == original[0]["text"]
    assert (current["start_ms"], current["end_ms"]) == (123, 456)
    assert read_revision(repo, mid, 1) == original


@pytest.fixture
def report_payload():
    return {
        "base_url": "https://synthetic.test/v1", "protocol": "openai", "model": "synthetic",
        "api_key": "synthetic-private-api-key",
        "segments": [{"id": "s1", "text": "Синтетический текст"}],
        "template": "meeting", "language": "en",
    }


@pytest.mark.parametrize("template", ["meeting", "client", "technical"])
def test_legacy_language_is_stable_when_replaying_snapshot(report_payload, template):
    report_payload["template"] = template
    request, context = prepare_request(report_payload)
    replay, replay_context = prepare_request({
        **report_payload, "template_snapshot": request.template_snapshot,
    })

    assert replay.language == request.language == "en"
    assert request.template_snapshot is not None
    assert request.template_snapshot["spec"]["language"] == "en"
    assert json.loads(context)["language"] == "en"
    assert json.loads(context)["template_snapshot"] == request.template_snapshot
    assert replay_context == context


@pytest.mark.parametrize("language", ["en-US", "EN", "synthetic-private-language"])
def test_invalid_legacy_language_returns_safe_error(report_payload, language):
    report_payload["language"] = language

    with pytest.raises(ValueError, match="Некорректные параметры отчёта") as caught:
        prepare_request(report_payload)

    assert language not in str(caught.value)
    assert report_payload["api_key"] not in str(caught.value)
    assert report_payload["segments"][0]["text"] not in str(caught.value)


@pytest.mark.parametrize("language,snapshot_language", [
    ("en", "ru"), ("ru", "en"), ("synthetic-private-language", "en"),
])
def test_explicit_snapshot_language_is_authoritative(report_payload, language, snapshot_language):
    report_payload["language"] = language
    spec = builtin_spec("meeting").model_dump()
    spec["language"] = snapshot_language
    report_payload["template_snapshot"] = {"id": "meeting", "revision": 1, "spec": spec}

    request, context = prepare_request(report_payload)

    assert request.language == snapshot_language
    assert request.template_snapshot == report_payload["template_snapshot"]
    assert json.loads(context)["language"] == snapshot_language
