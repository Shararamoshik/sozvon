"""Shared public runtime error and the fixed operation allowlist."""

OPERATIONS = frozenset({"ping", "devices", "audio_info", "transcribe", "record", "report"})


class WorkerError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def validate_request(command: dict) -> tuple[str, dict]:
    if (
        set(command) != {"operation", "payload"}
        or not isinstance(command.get("operation"), str)
        or not isinstance(command.get("payload"), dict)
    ):
        raise WorkerError("invalid_request", "Ожидается команда {operation, payload}")
    if command["operation"] not in OPERATIONS:
        raise WorkerError("unknown_operation", "Неизвестная операция worker")
    return command["operation"], command["payload"]
