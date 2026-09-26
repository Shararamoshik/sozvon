"""Import boundaries and wire-contract validation."""

import subprocess
import sys
import threading

import pytest


def test_source_audio_imports_do_not_load_any_native_libraries():
    code = """
import importlib.abc
import sys
class BlockNative(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'av', 'numpy', 'sounddevice', 'pyaudiowpatch',
                                      'faster_whisper', 'ctranslate2'}:
            raise AssertionError('eager native import: ' + fullname)
sys.meta_path.insert(0, BlockNative())
import sozvon.audio.operations
import sozvon.audio.capture
import sozvon.audio.backends
import sozvon.audio.probe
import sozvon.audio.stt
import sozvon.audio.mix
print('all audio imports are lazy')
"""
    process = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=10, check=False
    )
    assert process.returncode == 0, process.stderr
    assert "all audio imports are lazy" in process.stdout


@pytest.mark.parametrize("payload", [{}, {"path": None}, {"path": 3}])
def test_audio_info_invalid_payload_is_value_error(payload):
    from sozvon.audio.operations import run

    with pytest.raises(ValueError):
        run("audio_info", payload, threading.Event(), lambda _: None)


def test_unknown_operation_is_safe_error():
    from sozvon.audio.operations import run

    with pytest.raises(ValueError, match="Неизвестная"):
        run("invented", {}, threading.Event(), lambda _: None)


def test_missing_decode_library_is_a_safe_runtime_error(tmp_path, monkeypatch):
    from test_audio_stt import stt_payload

    from sozvon.audio.mix import prepared_audio

    monkeypatch.setitem(sys.modules, "numpy", None)
    with (
        pytest.raises(RuntimeError, match="библиотек"),
        prepared_audio(stt_payload(tmp_path), threading.Event()),
    ):
        pytest.fail("decoder unavailable")


def test_audio_info_through_real_worker_process(tmp_path):
    from test_audio_probe import make_wav

    from sozvon.runtime.host import WorkerHost

    path = make_wav(tmp_path / "worker-input.wav")
    result = WorkerHost().run("audio_info", {"path": str(path)}, timeout=10)
    assert result["duration_ms"] == 1000
    assert result["sample_rate"] == 8000 and result["channels"] == 2
    assert result["peak"] > 0.4
