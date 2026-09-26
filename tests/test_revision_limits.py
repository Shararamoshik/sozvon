"""Номер версии ограничен типом SQLite до выполнения SQL."""
import pytest
from test_web import open_client

SQLITE_OVERFLOW = 2 ** 63


@pytest.mark.parametrize('kind', ['read', 'restore'])
def test_sqlite_revision_overflow_rejected_as_422(tmp_path, kind):
    app, client, headers = open_client(tmp_path)
    with client:
        mid = app.state.service.repo.create_text('Синтетический', 'Не меняется')
        url = f'/api/meetings/{mid}/transcript'
        if kind == 'read':
            response = client.get(url, params={'revision': SQLITE_OVERFLOW})
        else:
            response = client.post(url + '/restore', headers=headers,
                                   json={'base_revision': 1, 'target_revision': SQLITE_OVERFLOW})
        assert response.status_code == 422
        assert client.get(url).json()['revision'] == 1


def test_all_command_revision_fields_have_same_upper_bound():
    from pydantic import ValidationError

    from sozvon.services.requests import ReportInput, TranscribeInput
    from sozvon.transcripts.schema import TranscriptEdit
    from sozvon.web.routes.templates import RevisionInput
    from sozvon.web.routes.transcripts import RestoreInput
    for model, value in [
        (ReportInput, {'template_id': 'meeting', 'template_revision': SQLITE_OVERFLOW}),
        (TranscribeInput, {'base_revision': SQLITE_OVERFLOW}),
        (TranscriptEdit, {'base_revision': SQLITE_OVERFLOW, 'edits': [{'id': 'id', 'text': 'text'}]}),
        (RevisionInput, {'base_revision': SQLITE_OVERFLOW}),
        (RestoreInput, {'base_revision': SQLITE_OVERFLOW, 'target_revision': 1}),
    ]:
        with pytest.raises(ValidationError):
            model.model_validate(value)
