"""Регрессия Windows CRT: блокирующее чтение stdin мешало импорту NumPy."""
import os
from pathlib import Path

import pytest

from sozvon.runtime.host import WorkerHost


@pytest.mark.skipif(os.name != "nt", reason="Windows CRT/native loader")
def test_native_audio_import_while_control_pipe_waits():
    path = Path(__file__).parent / "fixtures" / "hello_ru.wav"
    result = WorkerHost().run("audio_info", {"path": str(path.resolve())}, timeout=10)
    assert result["duration_ms"] > 1000
