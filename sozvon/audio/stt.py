"""Offline-only faster-whisper inference; import does not load the model."""

import inspect
import math
import os
import re
from uuid import uuid4

from .mix import prepared_audio
from .paths import local_path
from .probe import check_cancelled
from .result_limits import MAX_RESULT_BYTES, MAX_SEGMENTS_BYTES
from .result_limits import json_size as _json_size


class TranscriptLimitError(ValueError):
    """A terminal size limit, not a GPU failure eligible for CPU retry."""


def _size_error():
    return TranscriptLimitError(
        "Расшифровка превышает предел: сегменты 900 КиБ, полный результат менее 1 МиБ. "
        "Разделите запись на части и распознайте отдельно; текст не обрезан."
    )


def _run_model(factory, model_path, path, device, language, stop_event, emit):
    check_cancelled(stop_event)
    kwargs = {"device": device, "compute_type": "int8"}
    parameters = inspect.signature(factory).parameters
    if "local_files_only" in parameters or any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values()
    ):
        kwargs["local_files_only"] = True
    model = factory(str(model_path), **kwargs)
    stream, _ = model.transcribe(str(path), language=language)
    segments = []
    segments_bytes = 2  # Array brackets; include escaping/UTF-8 and each comma below.
    try:
        for segment in stream:
            check_cancelled(stop_event)
            text = segment.text.strip()
            if not text:
                continue
            if not (
                math.isfinite(segment.start)
                and math.isfinite(segment.end)
                and 0 <= segment.start <= segment.end
            ):
                raise ValueError("Модель вернула неверные временные границы")
            if len(text) > MAX_SEGMENTS_BYTES:
                raise _size_error()
            item = {
                "id": uuid4().hex,
                "ordinal": len(segments),
                "start_ms": round(segment.start * 1000),
                "end_ms": round(segment.end * 1000),
                "speaker": None,
                "text": text,
            }
            segments_bytes += _json_size(item) + bool(segments)
            if segments_bytes > MAX_SEGMENTS_BYTES:
                raise _size_error()
            segments.append(item)
            emit(
                {
                    "type": "progress",
                    "stage": "Распознавание",
                    "completed": segment.end,
                    "unit": "с",
                }
            )
        check_cancelled(stop_event)
    finally:
        if hasattr(stream, "close"):
            stream.close()
    # CTranslate2 resolves auto to the actual device; avoid claiming a GPU when it chose CPU.
    actual_device = getattr(getattr(model, "model", None), "device", device)
    return segments, actual_device


def transcribe(payload, stop_event, emit):
    model_path = local_path(payload.get("model_path"))
    if not model_path.is_dir():
        raise ValueError("Нужен существующий локальный каталог модели, не имя для скачивания")
    # faster-whisper otherwise calls Tokenizer.from_pretrained even with local_files_only=True.
    if not (model_path / "tokenizer.json").is_file():
        raise ValueError("В локальной модели нет tokenizer.json; скачивание запрещено")
    device = payload.get("device", "cpu")
    if device not in {"cpu", "auto", "cuda"}:
        raise ValueError("Устройство распознавания должно быть cpu, auto или cuda")
    if payload.get("compute_type", "int8") != "int8":
        raise ValueError("Поддерживается только compute_type=int8")
    language = payload.get("language", "auto")
    if not isinstance(language, str) or not re.fullmatch(r"auto|[a-z]{2,3}", language):
        raise ValueError("Неверный код языка распознавания")
    language = None if language == "auto" else language
    check_cancelled(stop_event)
    # These are worker-local environment flags; no network or implicit asset downloads.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    warnings = []
    with prepared_audio(payload, stop_event) as path:
        # Validate bounded decoded duration before importing or constructing native STT.
        try:
            from faster_whisper import WhisperModel
        except (ImportError, OSError) as exc:
            raise RuntimeError("Не загружена локальная библиотека faster-whisper") from exc
        try:
            segments, actual_device = _run_model(
                WhisperModel, model_path, path, device, language, stop_event, emit
            )
        except TranscriptLimitError:
            raise
        except Exception as exc:
            check_cancelled(stop_event)
            if device != "auto":
                raise RuntimeError(
                    "Ошибка локального распознавания; проверьте модель и устройство"
                ) from exc
            warnings.append("Автовыбор устройства не сработал: повтор на CPU/int8")
            emit({"type": "progress", "stage": warnings[-1], "completed": 0, "unit": "с"})
            try:
                segments, actual_device = _run_model(
                    WhisperModel, model_path, path, "cpu", language, stop_event, emit
                )
            except TranscriptLimitError:
                raise
            except Exception as fallback_exc:
                check_cancelled(stop_event)
                raise RuntimeError(
                    "Локальное распознавание не удалось и на CPU/int8"
                ) from fallback_exc
    if not segments:
        raise RuntimeError("Речь не обнаружена")
    result = {
        "segments": segments,
        "model": str(model_path),
        "device": actual_device,
        "warnings": warnings,
    }
    # Account for every field AND the terminal event wrapper, before runtime framing.
    if _json_size({"type": "result", "result": result}) > MAX_RESULT_BYTES:
        raise _size_error()
    return result
