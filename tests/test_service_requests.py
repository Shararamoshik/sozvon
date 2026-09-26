"""Входные параметры заданий: без неявного преобразования ревизий и согласия."""
import pytest
from pydantic import ValidationError


def test_transcription_confirmation_is_strict_and_revision_optional_only_for_legacy():
    from sozvon.services.requests import TranscribeInput
    value = TranscribeInput.model_validate({})
    assert value.base_revision is None
    assert value.replace_confirmed is False
    assert value.cloud_confirmed_url is None
    for bad in ({'base_revision': -1}, {'base_revision': True},
                {'replace_confirmed': 'true'}, {'cloud_confirmed_url': 4}, {'extra': 1}):
        with pytest.raises(ValidationError):
            TranscribeInput.model_validate(bad)
    value = TranscribeInput(base_revision=0, cloud_confirmed_url='https://example.invalid/v1/audio/transcriptions')
    assert value.base_revision == 0


def test_report_choice_requires_complete_pair():
    from sozvon.services.requests import ReportInput
    assert ReportInput().template_id is None
    assert ReportInput(template_id='meeting', template_revision=1).template_revision == 1
    for bad in ({'template_id': 'meeting'}, {'template_revision': 1},
                {'template_id': 'meeting', 'template_revision': 0},
                {'template_id': '', 'template_revision': 1}, {'extra': 1}):
        with pytest.raises(ValidationError):
            ReportInput.model_validate(bad)
