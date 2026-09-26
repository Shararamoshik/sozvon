"""Разрешены только две новые фиксированные операции, без произвольного dispatcher."""
import sys
import threading
import types

import pytest

from sozvon.runtime.protocol import validate_request
from sozvon.runtime.worker import _dispatch


@pytest.mark.parametrize('operation,module,function', [
    ('transcribe_cloud', 'sozvon.audio.cloud_stt', 'transcribe_cloud'),
    ('render_export', 'sozvon.export.worker', 'render_export'),
])
def test_new_operations_use_fixed_lazy_handler(monkeypatch, operation, module, function):
    calls = []
    handler = types.ModuleType(module)

    def run(payload, stop, emit):
        calls.append((payload, stop))
        emit({'type': 'progress', 'stage': 'Проверка'})
        return {'checked': True}

    setattr(handler, function, run)
    monkeypatch.setitem(sys.modules, module, handler)
    payload = {'fixture': 'synthetic'}
    assert validate_request({'operation': operation, 'payload': payload}) == (operation, payload)
    stop = threading.Event()
    events = []
    assert _dispatch(operation, payload, stop, events.append) == {'checked': True}
    assert calls == [(payload, stop)]
    assert events == [{'type': 'progress', 'stage': 'Проверка'}]
