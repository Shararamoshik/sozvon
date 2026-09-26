from pathlib import Path

import pytest

from sozvon.services.application import Application


def test_model_network_path_rejected_before_filesystem(tmp_path, monkeypatch):
    app = Application(tmp_path)
    mid = app.repo.create('test', 'audio')
    source = tmp_path / 'test.wav'
    source.write_bytes(b'test')
    app.repo.attach_source(mid, source, 'audio')
    app.settings.save({'stt': {'model_path': r'\\example.invalid\models\tiny'}})
    original = Path.is_dir

    def guard(path):
        if str(path).startswith('\\\\'):
            pytest.fail('Network path reached filesystem')
        return original(path)

    monkeypatch.setattr(Path, 'is_dir', guard)
    try:
        with pytest.raises(ValueError, match='локальн'):
            app.transcribe(mid)
    finally:
        app.close()
