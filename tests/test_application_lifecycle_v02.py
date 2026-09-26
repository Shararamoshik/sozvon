"""Экспорт имеет отдельный ограничитель и отменяется вместе с приложением."""
from sozvon.services.application import Application


def test_close_signals_export_cancel_and_lock_is_per_instance(tmp_path):
    first = Application(tmp_path / 'first')
    second = Application(tmp_path / 'second')
    try:
        assert not first._export_stop.is_set()
        assert first._export_lock.acquire(blocking=False)
        assert not first._export_lock.acquire(blocking=False)
        assert second._export_lock.acquire(blocking=False)
        second._export_lock.release()
        first._export_lock.release()
        first.close()
        assert first._export_stop.is_set()
        assert not second._export_stop.is_set()
    finally:
        first.close()
        second.close()
