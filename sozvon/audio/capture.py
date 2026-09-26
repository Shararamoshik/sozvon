"""Two independent PCM sources and a bounded callback-to-writer queue."""

import math
import queue
import threading
import time
import wave
from uuid import uuid4

from .paths import local_path


class CaptureService:
    """One-shot capture. start() explicitly starts streams; stop() returns finalized WAVs.

    A backend supplies devices(), default_device(), format(id),
    open(id, rate, channels, callback)->dormant stream, close(). Streams expose
    start(), stop(), close(), active. The callback receives PCM16 bytes and status.
    """

    def __init__(self, payload, *, backends=None, queue_size=128):
        root = local_path(payload.get("output_dir"))
        seconds = payload.get("max_seconds", 60)
        if (
            isinstance(seconds, bool)
            or not isinstance(seconds, (int, float))
            or not math.isfinite(seconds)
            or not 0 < seconds <= 14400
        ):
            raise ValueError("Лимит записи должен быть больше 0 и не больше 14400 секунд")
        if not isinstance(payload.get("allow_partial", False), bool):
            raise ValueError("allow_partial должен быть логическим значением")  # noqa: TRY004 — wire contract
        if not isinstance(queue_size, int) or queue_size <= 0:
            raise ValueError("Очередь записи должна быть ограничена")
        self.payload = payload
        self.root = root
        self.max_seconds = seconds
        self.backends = backends
        self.queue = queue.Queue(maxsize=queue_size)
        self.failed = threading.Event()
        self.finished = threading.Event()
        self.lock = threading.Lock()
        self.streams = []
        self.tracks = {}
        self.warnings = []
        self.result = None
        self.writer = None
        self.started_at = None
        self.closed = False
        self.failure = None

    def _fail(self, message):
        if not self.failed.is_set():
            self.warnings.append(message)
        self.failed.set()

    def _callback(self, source):
        def callback(data, status):
            if self.finished.is_set() or self.failed.is_set():
                return
            if status:
                self._fail(f"Поток {source}: потеря аудиоданных; запись остановлена")
                return
            if not data:
                return
            if len(data) > 4 * 1024**2:
                self._fail(f"Поток {source}: превышен размер блока аудио")
                return
            try:
                self.queue.put_nowait((source, bytes(data)))
            except queue.Full:
                self._fail("Переполнена очередь записи: остановлено с сохранением принятых блоков")

        return callback

    def _write(self):
        try:
            import numpy as np

            while not self.finished.is_set() or not self.queue.empty():
                try:
                    source, data = self.queue.get(timeout=0.05)
                except queue.Empty:
                    continue
                track = self.tracks[source]
                if len(data) % (2 * track["channels"]):
                    raise ValueError("Неполный PCM-кадр")
                if not track["frames"]:
                    # File time zero is the common monotonic epoch, not this stream's start.
                    # Write padding in bounded blocks, and only for a source that gave PCM.
                    remaining = track["start_offset_frames"]
                    while remaining:
                        count = min(remaining, 16000)
                        track["file"].writeframesraw(b"\0" * (count * track["channels"] * 2))
                        track["frames"] += count
                        track["samples"] += count * track["channels"]
                        remaining -= count
                track["file"].writeframesraw(data)
                samples = np.frombuffer(data, dtype="<i2").astype(np.float64) / 32768
                with self.lock:
                    track["frames"] += len(data) // (2 * track["channels"])
                    track["audio_frames"] += len(data) // (2 * track["channels"])
                    track["samples"] += samples.size
                    track["energy"] += float(np.square(samples).sum())
                    track["level"] = float(np.abs(samples).max(initial=0))
                    track["peak"] = max(track["peak"], track["level"])
        except Exception:  # noqa: BLE001 — native PortAudio errors inherit Exception directly
            self._fail("Ошибка записи WAV на диск; сохранены только доступные данные")

    def _close_backends(self):
        for backend in (self.backends or {}).values():
            try:
                backend.close()
            except Exception:  # noqa: BLE001 — native PortAudio errors inherit Exception directly
                self.warnings.append("Не удалось штатно закрыть аудиобиблиотеку")

    def _preflight(self):
        if self.backends is None:
            from .backends import create_backends

            self.backends, self.warnings = create_backends()
        plan = []
        for source in ("mic", "system"):
            backend = self.backends.get(source)
            if backend is None:
                self.warnings.append(f"Дорожка {source} недоступна: нет библиотеки захвата")
                continue
            try:
                key = "input_device" if source == "mic" else "output_device"
                device = self.payload.get(key) or backend.default_device()
                if device not in {d["id"] for d in backend.devices()}:
                    raise ValueError("Устройство отсутствует")
                rate, channels = backend.format(device)
                if not (0 < rate <= 384000 and 0 < channels <= 32):
                    raise ValueError("Неподдерживаемый формат")
                if self.max_seconds * rate * channels * 2 > 4 * 1024**3 - 1024:
                    raise ValueError("Лимит записи превышает ёмкость WAV для этого формата")
                plan.append((source, backend, device, rate, channels))
            except Exception:  # noqa: BLE001 — native PortAudio errors inherit Exception directly
                self.warnings.append(
                    f"Дорожка {source} недоступна: устройство или формат не доступны"
                )
        if not plan or (len(plan) != 2 and not self.payload.get("allow_partial", False)):
            raise RuntimeError("Нельзя начать запись двух дорожек: " + "; ".join(self.warnings))
        return plan

    def start(self):
        if self.started_at is not None or self.closed:
            raise RuntimeError("Этот сеанс записи уже запущен или завершён")
        try:
            plan = self._preflight()
            self.root.mkdir(parents=True, exist_ok=True)
            for source, backend, device, rate, channels in plan:
                path = self.root / f"{source}-{uuid4().hex}.wav"
                output = wave.open(str(path), "wb")  # noqa: SIM115 — lifetime is start() to stop()
                self.tracks[source] = {
                    "source": source,
                    "path": str(path),
                    "sample_rate": rate,
                    "channels": channels,
                    "file": output,
                    "frames": 0,
                    "audio_frames": 0,
                    "start_offset_frames": 0,
                    "samples": 0,
                    "energy": 0.0,
                    "peak": 0.0,
                    "level": 0.0,
                }
                output.setnchannels(channels)
                output.setsampwidth(2)
                output.setframerate(rate)
                self.streams.append(backend.open(device, rate, channels, self._callback(source)))
            self.writer = threading.Thread(target=self._write, name="audio-writer", daemon=True)
            self.writer.start()
            self.started_at = time.monotonic()
            for track, stream in zip(self.tracks.values(), self.streams, strict=True):
                track["start_offset_frames"] = round(
                    (time.monotonic() - self.started_at) * track["sample_rate"]
                )
                stream.start()
            return self
        except Exception as exc:
            self._cleanup()
            raise RuntimeError("Не удалось начать запись: " + "; ".join(self.warnings)) from exc

    def _cleanup(self):
        if self.closed:
            return
        for stream in self.streams:
            try:
                stream.stop()
            except Exception:  # noqa: BLE001 — native PortAudio errors inherit Exception directly
                self.warnings.append("Аудиопоток остановлен нештатно")
            finally:
                try:
                    stream.close()
                except Exception:  # noqa: BLE001 — native PortAudio errors inherit Exception directly
                    self.warnings.append("Не удалось закрыть аудиопоток")
        self.finished.set()
        if self.writer is not None:
            self.writer.join(timeout=10)
            if self.writer.is_alive():
                self._close_backends()
                self.closed = True
                raise RuntimeError("Писатель WAV не завершился за 10 секунд; файл не подтверждён")
        for track in self.tracks.values():
            try:
                track["file"].close()
            except Exception:  # noqa: BLE001 — native PortAudio errors inherit Exception directly
                track["invalid"] = True
                self.warnings.append(f"Не удалось завершить WAV {track['source']}")
        self._close_backends()
        self.closed = True

    def snapshot(self):
        if self.result is not None:
            return {
                "type": "progress",
                "stage": "Запись сохранена",
                "levels": {t["source"]: t["peak"] for t in self.result["tracks"]},
                "duration_ms": self.result["duration_ms"],
            }
        with self.lock:
            levels = {source: track["level"] for source, track in self.tracks.items()}
        return {
            "type": "progress",
            "stage": "Запись",
            "levels": levels,
            "duration_ms": round((time.monotonic() - self.started_at) * 1000)
            if self.started_at is not None
            else 0,
        }

    def stop(self):
        if self.result is not None:
            return self.result
        if self.failure is not None:
            raise RuntimeError(self.failure)
        self._cleanup()
        tracks = []
        for track in self.tracks.values():
            if not track["audio_frames"] or track.get("invalid"):
                self.warnings.append(f"Дорожка {track['source']} пуста или не завершена")
                continue
            tracks.append(
                {k: track[k] for k in ("source", "path", "sample_rate", "channels", "peak")}
                | {
                    "start_offset_frames": track["start_offset_frames"],
                    "start_offset_ms": round(
                        track["start_offset_frames"] * 1000 / track["sample_rate"]
                    ),
                    "duration_ms": round(track["frames"] * 1000 / track["sample_rate"]),
                    "rms": (track["energy"] / track["samples"]) ** 0.5,
                }
            )
        if not tracks:
            self.failure = "Запись пуста: аудиоданные не получены; " + "; ".join(self.warnings)
            raise RuntimeError(self.failure)
        self.result = {
            "tracks": tracks,
            "duration_ms": max(t["duration_ms"] for t in tracks),
            "warnings": self.warnings.copy(),
        }
        return self.result


def record(payload, stop_event, emit):
    from .probe import check_cancelled

    check_cancelled(stop_event)
    service = CaptureService(payload)
    service.start()
    try:
        while not stop_event.is_set() and not service.failed.is_set():
            elapsed = time.monotonic() - service.started_at
            if elapsed >= service.max_seconds:
                service.warnings.append(
                    f"Запись остановлена автоматически: лимит {service.max_seconds} с"
                )
                break
            try:
                inactive = any(not stream.active for stream in service.streams)
            except Exception:  # noqa: BLE001 — native active query fails on device removal
                service._fail(
                    "Не удалось проверить состояние аудиопотока; сохранены полученные данные"
                )
                break
            if inactive:
                service._fail("Аудиопоток неожиданно остановился; сохранены полученные данные")
                break
            emit(service.snapshot())
            stop_event.wait(min(0.1, service.max_seconds - elapsed))
    finally:
        result = service.stop()
    emit(
        {
            "type": "progress",
            "stage": "Запись сохранена",
            "duration_ms": result["duration_ms"],
            "levels": {track["source"]: track["peak"] for track in result["tracks"]},
        }
    )
    return result
