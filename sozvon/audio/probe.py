"""Decode local audio with PyAV independently of capture counters."""

from .paths import local_path

MAX_AUDIO_SECONDS = 14400
MAX_FILE_BYTES = 4 * 1024**3
MAX_DECODED_SAMPLES = 1_500_000_000


def local_audio_path(value):
    path = local_path(value)
    if not path.is_file():
        raise ValueError("Нужен существующий локальный аудиофайл с абсолютным путём")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("Превышен предел размера аудиофайла (4 ГиБ)")
    return path


def check_cancelled(stop_event):
    if stop_event is not None and stop_event.is_set():
        raise RuntimeError("Операция отменена")


def audio_info(path, stop_event=None):
    path = local_audio_path(path)
    check_cancelled(stop_event)
    try:
        import av
        import numpy as np
    except (ImportError, OSError) as exc:
        raise RuntimeError("Недоступна библиотека декодирования аудио PyAV/NumPy") from exc

    try:
        # Custom IO and a deny-all protocol whitelist prohibit playlist/network redirects.
        with (
            path.open("rb") as file,
            av.open(file, mode="r", options={"protocol_whitelist": ""}) as container,
        ):
            if not container.streams.audio:
                raise ValueError("В файле нет аудиодорожки")
            stream = container.streams.audio[0]
            sample_rate = stream.codec_context.sample_rate
            channels = stream.codec_context.channels
            if not (0 < sample_rate <= 384000 and 0 < channels <= 32):
                raise ValueError("Неподдерживаемый формат аудио")
            if container.duration and container.duration / av.time_base > MAX_AUDIO_SECONDS:
                raise ValueError("Превышен предел длительности аудио")
            resampler = av.AudioResampler(format="fltp")
            peak = energy = 0.0
            samples = frames = 0
            for frame in container.decode(stream):
                check_cancelled(stop_event)
                if frame.sample_rate != sample_rate or len(frame.layout.channels) != channels:
                    raise ValueError("Формат аудио меняется внутри файла")
                for converted in resampler.resample(frame):
                    frames += converted.samples
                    if frames / sample_rate > MAX_AUDIO_SECONDS:
                        raise ValueError("Превышен предел длительности аудио")
                    if frames * channels > MAX_DECODED_SAMPLES:
                        raise ValueError("Превышен предел безопасного анализа аудио")
                    data = converted.to_ndarray().astype(np.float64)
                    if not np.isfinite(data).all():
                        raise ValueError("Повреждённые значения в аудио")
                    peak = max(peak, float(np.abs(data).max(initial=0)))
                    energy += float(np.square(data).sum())
                    samples += data.size
            if not frames:
                raise ValueError("Аудиофайл пуст или повреждён")
            # Count decoded *frames*, not packed ndarray width (stereo doubles that width).
            duration_ms = round(frames * 1000 / sample_rate)
    except av.error.FFmpegError as exc:
        raise ValueError(
            "Не удалось декодировать аудио: файл повреждён или формат не поддержан"
        ) from exc
    except OSError as exc:
        raise ValueError("Не удалось прочитать локальный аудиофайл") from exc
    return {
        "duration_ms": duration_ms,
        "sample_rate": sample_rate,
        "channels": channels,
        "peak": peak,
        "rms": (energy / samples) ** 0.5,
    }
