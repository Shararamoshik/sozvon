"""Валидируемая конфигурация без открытых ключей."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import tomllib
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import tomli_w
from pydantic import BaseModel, ConfigDict, field_validator


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Llm(Section):
    base_url: str = "http://127.0.0.1:1234/v1"
    protocol: Literal["openai", "anthropic"] = "openai"
    model: str = ""
    allow_remote: bool = False

    @field_validator("base_url")
    @classmethod
    def check_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (parsed.scheme not in {"https", "http"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("Укажите адрес API без ключа, параметров и учётных данных")
        if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Для внешнего сервиса требуется HTTPS")
        return value.rstrip("/")


class Stt(Section):
    model_path: str = ""
    device: Literal["cpu", "auto", "cuda"] = "cpu"
    language: str = "auto"


class Recording(Section):
    input_device: str | None = None
    output_device: str | None = None


class Settings(Section):
    llm: Llm = Llm()
    stt: Stt = Stt()
    recording: Recording = Recording()
    template: Literal["meeting", "client", "technical"] = "meeting"


class SettingsStore:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "config.toml"
        self._lock = threading.RLock()
        self._config = Settings.model_validate(
            tomllib.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        )

    def _secret_account(self, llm: Llm) -> str:
        # Bind to the full validated URL (including path/port) and data directory.
        # Legacy root-only accounts have no trusted endpoint: require explicit re-entry.
        profile = json.dumps([str(self.root), llm.base_url]).encode("utf-8")
        return "base-url:" + hashlib.sha256(profile).hexdigest()

    def _read_secret(self, account: str | None = None) -> str | None:
        """Strict read: an inaccessible vault is not an empty vault."""
        try:
            import keyring
            return keyring.get_password("sozvon-llm", account or self._secret_account(self._config.llm))
        except Exception:  # noqa: BLE001 - backend failures must never disclose secret details
            raise ValueError(
                "Системное хранилище ключей недоступно; разблокируйте его и повторите действие"
            ) from None

    def secret(self) -> str | None:
        with self._lock:
            try:
                return self._read_secret()
            except ValueError:
                return None

    def snapshot(self) -> dict:
        with self._lock:
            return self._config.model_dump()

    def report_snapshot(self) -> tuple[dict, str | None]:
        """Read config and its bound credential under the save lock.

        Enabled remote processing requires a readable vault (ValueError if unavailable).
        Loopback services may run without a key when the vault is unavailable.
        Without remote consent, callers must reject the report before sending anything.
        The returned configuration is a detached, secret-free dict.
        """
        with self._lock:
            config = self._config.model_dump()
            try:
                key = self._read_secret()
            except ValueError:
                llm = self._config.llm
                remote = urlsplit(llm.base_url).hostname not in {"127.0.0.1", "localhost", "::1"}
                if remote and llm.allow_remote:
                    raise
                key = None
            return config, key

    def public(self) -> dict:
        with self._lock:
            result = self._config.model_dump()
            try:
                result["llm"]["configured"] = bool(self._read_secret())
            except ValueError as exc:
                result["llm"]["configured"] = False
                result["llm"]["secret_error"] = str(exc)
            result["data_dir"] = str(self.root)
            return result

    def save(self, candidate: dict) -> dict:
        with self._lock:
            value = dict(candidate)
            value.pop("data_dir", None)
            for section in ("llm", "stt", "recording"):
                if section in value and not isinstance(value[section], dict):
                    raise ValueError(f"Раздел {section} должен быть объектом настроек")
            llm = dict(value.get("llm", {}))
            llm.pop("configured", None)
            llm.pop("secret_error", None)
            key = llm.pop("api_key", None)
            delete_key = llm.pop("delete_key", False)
            if key is not None and not isinstance(key, str):
                raise ValueError("Ключ должен быть строкой")
            if delete_key and key:
                raise ValueError("Нельзя одновременно заменить и удалить ключ")
            current = self.snapshot()
            for section in ("llm", "stt", "recording"):
                incoming = llm if section == "llm" else value.get(section, {})
                current[section].update(incoming)
            if "template" in value:
                current["template"] = value["template"]
            unknown = set(value) - {"llm", "stt", "recording", "template"}
            if unknown:
                raise ValueError("Неизвестные разделы настроек")
            try:
                validated = Settings.model_validate(current)
            except ValueError:
                raise ValueError("Проверьте адрес сервиса, устройство и значения настроек") from None
            serializable = validated.model_dump(exclude_none=True)
            temporary = self.path.with_suffix(".toml.pending")
            account = self._secret_account(validated.llm)
            previous_key = self._read_secret(account) if key or delete_key else None
            changed_key = False
            try:
                with temporary.open("wb") as handle:
                    handle.write(tomli_w.dumps(serializable).encode("utf-8"))
                    handle.flush()
                    os.fsync(handle.fileno())
                if key or delete_key:
                    try:
                        import keyring
                        if key:
                            keyring.set_password("sozvon-llm", account, key)
                            changed_key = True
                        elif previous_key is not None:
                            keyring.delete_password("sozvon-llm", account)
                            changed_key = True
                    except Exception:  # noqa: BLE001 - keyring backend boundary, redact details
                        raise ValueError("Системное хранилище ключей недоступно; настройки не сохранены") from None
                os.replace(temporary, self.path)
            except Exception:
                if changed_key:
                    try:
                        import keyring
                        if previous_key:
                            keyring.set_password("sozvon-llm", account, previous_key)
                        else:
                            keyring.delete_password("sozvon-llm", account)
                    except Exception:  # noqa: BLE001 - rollback errors must also redact secrets
                        raise ValueError(
                            "Сохранение настроек прервано; проверьте состояние ключа в хранилище"
                        ) from None
                raise
            finally:
                temporary.unlink(missing_ok=True)
            self._config = validated
        return self.public()
