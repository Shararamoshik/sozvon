"""Worker-facing operation dispatch with no eager native imports."""


def run(operation, payload, stop_event, emit):
    if operation == "devices":
        from .backends import devices

        return devices()
    if operation == "audio_info":
        from .probe import audio_info

        return audio_info(payload.get("path"), stop_event)
    if operation == "transcribe":
        from .stt import transcribe

        return transcribe(payload, stop_event, emit)
    if operation == "record":
        from .capture import record

        return record(payload, stop_event, emit)
    raise ValueError(f"Неизвестная аудиооперация: {operation}")
