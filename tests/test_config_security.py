import threading

import pytest
from keyring.errors import KeyringError

from sozvon.config import SettingsStore


@pytest.fixture
def vault(monkeypatch):
    passwords = {}
    monkeypatch.setattr("keyring.get_password", lambda service, user: passwords.get((service, user)))
    monkeypatch.setattr(
        "keyring.set_password", lambda service, user, value: passwords.update({(service, user): value})
    )
    monkeypatch.setattr("keyring.delete_password", lambda service, user: passwords.pop((service, user)))
    return passwords


@pytest.mark.parametrize("change_endpoint", [False, True])
def test_report_snapshot_serializes_config_and_secret_with_save(tmp_path, monkeypatch, vault,
                                                              change_endpoint):
    store = SettingsStore(tmp_path)
    old_url = "https://old.example/v1"
    new_url = "https://new.example/v1" if change_endpoint else old_url
    store.save({"llm": {"base_url": old_url, "model": "old", "api_key": "old-key"}})
    read_report = store.report_snapshot
    read_secret = store._read_secret
    legacy_secret = store.secret
    reader_waiting = threading.Event()
    writer_attempted = threading.Event()
    writer_done = threading.Event()
    writer_blocked = []
    errors = []
    reports = []
    real_lock = threading.RLock()

    class ObservedLock:
        def __enter__(self):
            if threading.current_thread().name == "settings-writer" and not writer_attempted.is_set():
                acquired = real_lock.acquire(blocking=False)
                writer_blocked.append(not acquired)
                writer_attempted.set()
                if acquired:
                    return self
            real_lock.acquire()
            return self

        def __exit__(self, *args):
            real_lock.release()

    def pause_reader():
        if threading.current_thread().name == "report-reader":
            reader_waiting.set()
            assert writer_attempted.wait(5), "writer did not attempt to save"
            if not writer_blocked[0]:
                assert writer_done.wait(5), "unlocked writer did not finish"

    def paused_secret(*args, **kwargs):
        pause_reader()
        return read_secret(*args, **kwargs)

    def paused_legacy_secret():
        pause_reader()
        return legacy_secret()

    def read():
        try:
            reports.append(read_report())
        except BaseException as exc:  # noqa: BLE001 - surface thread failures in the test
            errors.append(exc)
        finally:
            reader_waiting.set()

    def write():
        try:
            assert reader_waiting.wait(5), "reader did not reach keyring"
            store.save({"llm": {"base_url": new_url, "model": "new", "api_key": "new-key"}})
        except BaseException as exc:  # noqa: BLE001 - surface thread failures in the test
            errors.append(exc)
        finally:
            writer_done.set()

    monkeypatch.setattr(store, "_lock", ObservedLock())
    monkeypatch.setattr(store, "_read_secret", paused_secret)
    monkeypatch.setattr(store, "secret", paused_legacy_secret)
    reader = threading.Thread(target=read, name="report-reader")
    writer = threading.Thread(target=write, name="settings-writer")
    reader.start()
    writer.start()
    reader.join(10)
    writer.join(10)
    assert not reader.is_alive() and not writer.is_alive()
    assert not errors
    config, key = reports[0]
    assert (config["llm"]["base_url"], config["llm"]["model"], key) == (old_url, "old", "old-key")
    assert writer_blocked == [True]
    config, key = store.report_snapshot()
    assert (config["llm"]["base_url"], config["llm"]["model"], key) == (new_url, "new", "new-key")
    assert "api_key" not in config["llm"]
    config["llm"]["model"] = "mutated-copy"
    assert store.snapshot()["llm"]["model"] == "new"


@pytest.mark.parametrize("operation", [{"delete_key": True}, {"api_key": "replacement-key"}])
@pytest.mark.parametrize(
    "error_type", [KeyringError, RuntimeError, OSError, ImportError, ValueError, LookupError]
)
def test_destructive_secret_operations_refuse_unreadable_vault(tmp_path, monkeypatch, vault,
                                                             operation, error_type):
    store = SettingsStore(tmp_path)
    store.save({"llm": {"api_key": "existing-key", "model": "original"}})
    before_file = store.path.read_bytes()
    before_config = store.snapshot()
    before_vault = dict(vault)

    def unavailable(service, user):
        raise error_type("sensitive-backend-detail existing-key")

    monkeypatch.setattr("keyring.get_password", unavailable)
    with pytest.raises(ValueError, match="хранилище ключей недоступно") as raised:
        store.save({"llm": {"model": "must-not-save", **operation}})
    assert "existing-key" not in str(raised.value)
    assert "sensitive-backend-detail" not in str(raised.value)
    assert store.snapshot() == before_config
    assert store.path.read_bytes() == before_file
    assert vault == before_vault
    assert not store.path.with_suffix(".toml.pending").exists()


@pytest.mark.parametrize("new_url", [
    "https://other.example/v1", "https://old.example/other/v1", "https://old.example:8443/v1",
])
def test_secret_binding_follows_exact_base_url_without_overwriting_other_profile(tmp_path, vault,
                                                                              new_url):
    store = SettingsStore(tmp_path)
    old_url = "https://old.example/v1"
    store.save({"llm": {"base_url": old_url, "api_key": "old-profile-key"}})
    before_vault = dict(vault)
    result = store.save({"llm": {"base_url": new_url}})
    assert result["llm"]["configured"] is False
    assert store.report_snapshot()[1] is None
    assert vault == before_vault
    assert SettingsStore(tmp_path).secret() is None
    store.save({"llm": {"api_key": "new-profile-key"}})
    assert store.secret() == "new-profile-key"
    assert all(vault[account] == value for account, value in before_vault.items())
    store.save({"llm": {"base_url": old_url}})
    assert store.report_snapshot()[1] == "old-profile-key"
    assert SettingsStore(tmp_path).secret() == "old-profile-key"
    store.save({"llm": {"base_url": new_url, "delete_key": True}})
    assert store.secret() is None
    assert vault == before_vault


def test_legacy_unbound_secret_is_not_implicitly_migrated_or_deleted(tmp_path, vault):
    legacy_account = ("sozvon-llm", str(tmp_path.resolve()))
    vault[legacy_account] = "legacy-unbound-key"
    store = SettingsStore(tmp_path)
    assert store.secret() is None
    store.save({"llm": {"base_url": "https://new.example/v1", "delete_key": True}})
    assert store.secret() is None
    assert vault[legacy_account] == "legacy-unbound-key"
    store.save({"llm": {"api_key": "explicit-new-key"}})
    assert store.secret() == "explicit-new-key"
    assert vault[legacy_account] == "legacy-unbound-key"


@pytest.mark.parametrize("section", ["llm", "stt", "recording"])
@pytest.mark.parametrize("invalid", [None, 42, False, [], "model"])
def test_section_requires_object_before_config_or_secret_changes(tmp_path, vault, section, invalid):
    store = SettingsStore(tmp_path)
    store.save({"llm": {"model": "original", "api_key": "keep-key"}})
    before_file = store.path.read_bytes()
    before_config = store.snapshot()
    before_vault = dict(vault)
    candidate = {"llm": {"model": "do-not-save", "api_key": "do-not-store"}, section: invalid}
    with pytest.raises(ValueError, match=section):
        store.save(candidate)
    assert store.snapshot() == before_config
    assert store.path.read_bytes() == before_file
    assert vault == before_vault


@pytest.mark.parametrize(
    "error_type", [KeyringError, RuntimeError, OSError, ImportError, ValueError, LookupError]
)
def test_public_reports_safe_secret_error_without_disclosing_backend_details(tmp_path, monkeypatch,
                                                                           vault, error_type):
    store = SettingsStore(tmp_path)
    store.save({"llm": {"api_key": "never-echo-this-key"}})

    def unavailable(service, user):
        raise error_type("private-backend-details never-echo-this-key")

    monkeypatch.setattr("keyring.get_password", unavailable)
    result = store.public()
    assert result["llm"]["configured"] is False
    assert "хранилище ключей недоступно" in result["llm"]["secret_error"]
    assert "private-backend-details" not in str(result)
    assert "never-echo-this-key" not in str(result)
    assert "api_key" not in result["llm"]
    # UI round-trips public status, which must not become persisted config.
    assert store.save(result)["llm"]["configured"] is False
    assert "secret_error" not in store.path.read_text()


@pytest.mark.parametrize("operation", [{"delete_key": True}, {"api_key": "replacement-key"}])
@pytest.mark.parametrize("new_profile", [False, True])
def test_failed_file_commit_restores_exact_target_secret_profile(tmp_path, monkeypatch, vault,
                                                                operation, new_profile):
    store = SettingsStore(tmp_path)
    store.save({"llm": {"base_url": "https://old.example/v1", "api_key": "old-key"}})
    if new_profile:
        store.save({"llm": {"base_url": "https://new.example/v1", "api_key": "target-key"}})
        store.save({"llm": {"base_url": "https://old.example/v1"}})
    before_file = store.path.read_bytes()
    before_config = store.snapshot()
    before_vault = dict(vault)

    def failed_replace(*args):
        raise OSError("cannot replace config")

    monkeypatch.setattr("sozvon.config.os.replace", failed_replace)
    target = "https://new.example/v1" if new_profile else "https://old.example/v1"
    with pytest.raises(OSError, match="cannot replace config"):
        store.save({"llm": {"base_url": target, **operation}})
    assert store.path.read_bytes() == before_file
    assert store.snapshot() == before_config
    assert vault == before_vault
    assert store.report_snapshot()[1] == "old-key"
    assert not store.path.with_suffix(".toml.pending").exists()


@pytest.mark.parametrize("operation", [{"delete_key": True}, {"api_key": "first-key"}])
def test_file_failure_does_not_invent_an_absent_secret_to_roll_back(tmp_path, monkeypatch, vault,
                                                                 operation):
    store = SettingsStore(tmp_path)
    store.save({"llm": {"model": "original"}})
    before = store.path.read_bytes()

    def failed_replace(*args):
        raise OSError("cannot replace config")

    monkeypatch.setattr("sozvon.config.os.replace", failed_replace)
    with pytest.raises(OSError, match="cannot replace config"):
        store.save({"llm": {"model": "not-saved", **operation}})
    assert not vault
    assert store.path.read_bytes() == before
    assert store.snapshot()["llm"]["model"] == "original"
    assert not store.path.with_suffix(".toml.pending").exists()


@pytest.mark.parametrize("error_type", [ImportError, ValueError, LookupError])
def test_rollback_backend_failure_is_redacted(tmp_path, monkeypatch, vault, error_type):
    store = SettingsStore(tmp_path)
    store.save({"llm": {"api_key": "private-old-key"}})
    before_file = store.path.read_bytes()
    before_config = store.snapshot()

    def failed_replace(*args):
        raise OSError("cannot replace config")

    def fail_rollback(service, user, value):
        if value == "private-old-key":
            raise error_type("backend-details private-old-key")
        vault[(service, user)] = value

    monkeypatch.setattr("sozvon.config.os.replace", failed_replace)
    monkeypatch.setattr("keyring.set_password", fail_rollback)
    with pytest.raises(ValueError, match="проверьте состояние ключа") as raised:
        store.save({"llm": {"api_key": "replacement-key"}})
    assert "private-old-key" not in str(raised.value)
    assert "backend-details" not in str(raised.value)
    assert store.path.read_bytes() == before_file
    assert store.snapshot() == before_config
    assert not store.path.with_suffix(".toml.pending").exists()


@pytest.mark.parametrize("base_url", [
    "https://external.example/v1", "http://127.0.0.1:1234/v1", "http://localhost:1234/v1",
    "http://[::1]:1234/v1",
])
def test_report_snapshot_vault_failure_blocks_only_remote_service(tmp_path, monkeypatch, vault,
                                                                base_url):
    store = SettingsStore(tmp_path)
    store.save({"llm": {"base_url": base_url, "model": "test-model", "allow_remote": True}})

    def unavailable(service, user):
        raise KeyringError("sensitive-backend-details")

    monkeypatch.setattr("keyring.get_password", unavailable)
    if base_url.startswith("https://external"):
        with pytest.raises(ValueError, match="хранилище ключей недоступно") as raised:
            store.report_snapshot()
        assert "sensitive-backend-details" not in str(raised.value)
    else:
        config, secret = store.report_snapshot()
        assert secret is None
        assert config == store.snapshot()
        assert config["llm"]["base_url"] == base_url


def test_disabled_remote_processing_does_not_require_unavailable_vault(tmp_path, monkeypatch, vault):
    store = SettingsStore(tmp_path)
    store.save({"llm": {"base_url": "https://external.example/v1", "model": "test-model"}})

    def unavailable(*args):
        raise KeyringError("private backend details")

    monkeypatch.setattr("keyring.get_password", unavailable)
    config, secret = store.report_snapshot()
    assert config["llm"]["allow_remote"] is False
    assert secret is None


@pytest.mark.parametrize("operation", [{"delete_key": True}, {"api_key": "replacement-key"}])
@pytest.mark.parametrize(
    "error_type", [KeyringError, RuntimeError, OSError, ImportError, ValueError, LookupError]
)
def test_failed_secret_mutation_preserves_config_and_hides_details(tmp_path, monkeypatch, vault,
                                                                 operation, error_type):
    store = SettingsStore(tmp_path)
    store.save({"llm": {"api_key": "existing-key", "model": "original"}})
    before_file = store.path.read_bytes()
    before_config = store.snapshot()
    before_vault = dict(vault)

    def unavailable(*args):
        raise error_type("sensitive-backend-detail existing-key replacement-key")

    method = "delete_password" if operation.get("delete_key") else "set_password"
    monkeypatch.setattr(f"keyring.{method}", unavailable)
    with pytest.raises(ValueError, match="хранилище ключей недоступно") as raised:
        store.save({"llm": {"model": "must-not-save", **operation}})
    assert "existing-key" not in str(raised.value)
    assert "replacement-key" not in str(raised.value)
    assert "sensitive-backend-detail" not in str(raised.value)
    assert store.snapshot() == before_config
    assert store.path.read_bytes() == before_file
    assert vault == before_vault
    assert not store.path.with_suffix(".toml.pending").exists()
