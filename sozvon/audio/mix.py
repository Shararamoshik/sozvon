"""Offline, bounded-block conversion/mixing for STT, leaving originals untouched."""

import tempfile
import wave
from contextlib import ExitStack, contextmanager
from pathlib import Path

from .paths import local_path
from .probe import MAX_DECODED_SAMPLES, check_cancelled, local_audio_path

STT_RATE = 16000
BLOCK_FRAMES = 16000
MAX_STT_SECONDS = 1800
STT_DURATION_ERROR = (
    "Локальное распознавание ограничено 30 минутами аудио. "
    "Разделите запись на части; исходные дорожки сохранены."
)


def _normalize(path, target, stop_event):
    import av
    import numpy as np

    try:
        with (
            path.open("rb") as source,
            av.open(source, mode="r", options={"protocol_whitelist": ""}) as container,
            target.open("wb") as output,
        ):
            if not container.streams.audio:
                raise ValueError("В файле нет аудиодорожки")
            if container.duration and container.duration / av.time_base > MAX_STT_SECONDS:
                raise ValueError(STT_DURATION_ERROR)
            stream = container.streams.audio[0]
            # Each source owns its resampler: format/rate/layout may all differ.
            resampler = av.AudioResampler(format="fltp", layout="mono", rate=STT_RATE)
            count = decoded = 0

            def write(frames):
                nonlocal count
                for frame in frames:
                    check_cancelled(stop_event)
                    count += frame.samples
                    if count > MAX_STT_SECONDS * STT_RATE:
                        raise ValueError(STT_DURATION_ERROR)
                    samples = frame.to_ndarray().ravel()
                    if not np.isfinite(samples).all():
                        raise ValueError("Повреждённые значения в аудио")
                    output.write(samples.astype("<f4").tobytes())

            for frame in container.decode(stream):
                check_cancelled(stop_event)
                decoded += frame.samples * len(frame.layout.channels)
                if decoded > MAX_DECODED_SAMPLES:
                    raise ValueError("Превышен предел безопасного анализа аудио")
                write(resampler.resample(frame))
            write(resampler.resample(None))
            if not count:
                raise ValueError("Аудиофайл пуст или повреждён")
    except av.error.FFmpegError as exc:
        raise ValueError("Не удалось декодировать аудио для распознавания") from exc
    except OSError as exc:
        raise ValueError("Не удалось подготовить локальное аудио для распознавания") from exc


@contextmanager
def prepared_audio(payload, stop_event):
    """Yield a temporary 16kHz mono PCM16 WAV; delete staging on success/error/cancel.

    `paths` accepts one or two synchronized sources, mixed at time zero rather
    than concatenated. A shorter source is padded with silence, never truncated.
    """
    values = payload.get("paths", [payload.get("path")])
    if not isinstance(values, list) or not 1 <= len(values) <= 2:
        raise ValueError("Нужно передать один или два локальных аудиофайла в paths")
    paths = [local_audio_path(value) for value in values]
    check_cancelled(stop_event)
    root = payload.get("output_dir")
    if root is not None:
        root = local_path(root)
        root.mkdir(parents=True, exist_ok=True)
    try:
        import av  # noqa: F401 — validate both native libraries before allocating staging
        import numpy as np
    except (ImportError, OSError) as exc:
        raise RuntimeError("Недоступна библиотека подготовки аудио PyAV/NumPy") from exc

    with tempfile.TemporaryDirectory(prefix="sozvon-stt-", dir=root) as directory:
        staging = Path(directory)
        normalized = []
        for index, path in enumerate(paths):
            target = staging / f"source-{index}.f32"
            _normalize(path, target, stop_event)
            normalized.append(target)
        mixed = staging / "mixed.wav"
        with ExitStack() as stack:
            inputs = [stack.enter_context(path.open("rb")) for path in normalized]
            output = stack.enter_context(wave.open(str(mixed), "wb"))
            output.setparams((1, 2, STT_RATE, 0, "NONE", "not compressed"))
            while True:
                check_cancelled(stop_event)
                blocks = [
                    np.frombuffer(source.read(BLOCK_FRAMES * 4), dtype="<f4") for source in inputs
                ]
                length = max(block.size for block in blocks)
                if not length:
                    break
                samples = np.zeros(length, dtype=np.float32)
                for block in blocks:
                    samples[: block.size] += block / len(blocks)
                pcm = np.rint(np.clip(samples, -1, 32767 / 32768) * 32768).astype("<i2")
                output.writeframesraw(pcm.tobytes())
        yield mixed
