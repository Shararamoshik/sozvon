import hashlib
import json
import threading

import pytest

from sozvon.config import SettingsStore


@pytest.fixture
def vault(monkeypatch):
    values = {}
    monkeypatch.setattr(
        "keyring.get_password", lambda service, account: values.get((service, account))
    )
    monkeypatch.setattr(
        "keyring.set_password",
        lambda service, account, key: values.update({(service, account): key}),
    )
    monkeypatch.setattr(
        "keyring.delete_password", lambda service, account: values.pop((service, account))
    )
    return values


def test_stt_and_llm_keys_are_independent(tmp_path, vault):
    store = SettingsStore(tmp_path)
    store.save({"llm": {"api_key": "llm-key"}})
    result = store.save({"stt": {"cloud": {"api_key": "stt-key"}}})
    assert store.report_snapshot()[1] == "llm-key"
    config, key = store.stt_snapshot()
    assert config == store.snapshot()
    assert key == "stt-key"
    assert {service for service, _ in vault} == {"sozvon-llm", "sozvon-stt"}
    cloud = result["stt"]["cloud"]
    assert cloud["configured"] is True
    assert cloud["endpoint"] == "https://api.openai.com/v1/audio/transcriptions"
    assert "stt-key" not in json.dumps(result)
    assert "stt-key" not in store.path.read_text()
    store.save(result)
    assert "configured" not in store.path.read_text()
    store.save({"stt": {"cloud": {"delete_key": True}}})
    assert store.stt_snapshot()[1] is None
    assert store.report_snapshot()[1] == "llm-key"


@pytest.mark.parametrize(
    "llm_op,stt_op",
    [
        ({"api_key": "a"}, {"api_key": "b"}),
        ({"delete_key": True}, {"api_key": "b"}),
        ({"api_key": "a"}, {"delete_key": True}),
    ],
)
def test_two_secret_changes_rejected_before_write(tmp_path, vault, monkeypatch, llm_op, stt_op):
    store = SettingsStore(tmp_path)
    before = store.snapshot()

    def no_io(*args):
        pytest.fail("keyring IO before validation")

    monkeypatch.setattr("keyring.get_password", no_io)
    with pytest.raises(ValueError, match="одного ключа"):
        store.save({"llm": llm_op, "stt": {"cloud": stt_op}})
    assert store.snapshot() == before
    assert not store.path.exists()
    assert vault == {}


@pytest.mark.parametrize(
    "cloud",
    [
        None,
        [],
        42,
        "bad",
        {"unknown": 1},
        {"allow_remote": "false"},
        {"delete_key": "false"},
        {"model": "  "},
        {"model": "secret\nmodel"},
    ],
)
def test_invalid_cloud_rejected_before_any_io(tmp_path, vault, monkeypatch, cloud):
    store = SettingsStore(tmp_path)

    def no_io(*args):
        pytest.fail("keyring IO before validation")

    monkeypatch.setattr("keyring.get_password", no_io)
    with pytest.raises(ValueError):
        store.save({"llm": {"api_key": "do-not-store"}, "stt": {"cloud": cloud}})
    assert not store.path.exists()
    assert vault == {}


def test_stt_key_binding_merge_and_consent_reset(tmp_path, vault):
    store = SettingsStore(tmp_path)
    store.save(
        {
            "stt": {
                "cloud": {
                    "base_url": "https://old.example/v1",
                    "model": "custom",
                    "timeout_s": 90,
                    "allow_remote": True,
                    "api_key": "old-stt",
                }
            }
        }
    )
    store.save({"stt": {"cloud": {"model": "another"}}})
    assert store.snapshot()["stt"]["cloud"]["timeout_s"] == 90
    assert store.snapshot()["stt"]["cloud"]["allow_remote"] is True
    store.save({"stt": {"cloud": {"base_url": "https://new.example/v1"}}})
    config, key = store.stt_snapshot()
    assert config["stt"]["cloud"]["model"] == "another"
    assert key is None
    assert config["stt"]["cloud"]["allow_remote"] is False
    store.save({"stt": {"cloud": {"base_url": "https://old.example/v1", "allow_remote": True}}})
    assert store.stt_snapshot()[1] == "old-stt"
    assert store.snapshot()["stt"]["cloud"]["allow_remote"] is True


@pytest.mark.parametrize(
    "url,consent,blocked",
    [
        ("http://127.0.0.2:9000/v1", True, False),
        ("https://external.example/v1", False, False),
        ("https://external.example/v1", True, True),
    ],
)
def test_stt_snapshot_vault_failure_only_blocks_enabled_remote(
    tmp_path, monkeypatch, vault, url, consent, blocked
):
    store = SettingsStore(tmp_path)
    store.save({"stt": {"engine": "cloud", "cloud": {"base_url": url, "allow_remote": consent}}})

    def unavailable(*args):
        raise RuntimeError("private secret-backend-value")

    monkeypatch.setattr("keyring.get_password", unavailable)
    assert "private" not in str(store.public())
    assert "secret_error" in store.public()["stt"]["cloud"]
    if blocked:
        with pytest.raises(ValueError, match="хранилище ключей недоступно"):
            store.stt_snapshot()
    else:
        config, key = store.stt_snapshot()
        assert config == store.snapshot()
        assert key is None


def test_template_accepts_custom_string_id(tmp_path, vault):
    store = SettingsStore(tmp_path)
    store.save({"template": "f" * 32})
    assert SettingsStore(tmp_path).snapshot()["template"] == "f" * 32
    for value in ["", "f" * 65, 123]:
        with pytest.raises(ValueError):
            store.save({"template": value})


def test_stt_snapshot_atomic(tmp_path, vault, monkeypatch):
    store = SettingsStore(tmp_path)
    store.save(
        {
            "stt": {
                "cloud": {
                    "base_url": "https://old.example/v1",
                    "model": "old",
                    "api_key": "old-key",
                    "allow_remote": True,
                }
            }
        }
    )
    reading, attempted = threading.Event(), threading.Event()
    lock, blocked, snapshots, errors = threading.RLock(), [], [], []

    class ObservedLock:
        def __enter__(self):
            if threading.current_thread().name == "writer":
                acquired = lock.acquire(blocking=False)
                blocked.append(not acquired)
                attempted.set()
                if acquired:
                    return self
            lock.acquire()
            return self

        def __exit__(self, *args):
            lock.release()

    read_secret = store._read_profile_secret

    def paused(kind, config):
        if threading.current_thread().name == "reader":
            reading.set()
            assert attempted.wait(5)
        return read_secret(kind, config)

    def reader():
        try:
            snapshots.append(store.stt_snapshot())
        except BaseException as error:  # noqa: BLE001 - surface thread failures
            errors.append(error)

    def writer():
        try:
            assert reading.wait(5)
            store.save(
                {
                    "stt": {
                        "cloud": {
                            "base_url": "https://new.example/v1",
                            "model": "new",
                            "api_key": "new-key",
                        }
                    }
                }
            )
        except BaseException as error:  # noqa: BLE001 - surface thread failures
            errors.append(error)

    monkeypatch.setattr(store, "_lock", ObservedLock())
    monkeypatch.setattr(store, "_read_profile_secret", paused)
    threads = [
        threading.Thread(target=reader, name="reader"),
        threading.Thread(target=writer, name="writer"),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    config, key = snapshots[0]
    assert (config["stt"]["cloud"]["model"], key) == ("old", "old-key")
    assert config["stt"]["cloud"]["allow_remote"] is True
    assert blocked[0] is True
    config["stt"]["cloud"]["model"] = "mutate-copy"
    assert store.stt_snapshot()[0]["stt"]["cloud"]["model"] == "new"
    assert store.stt_snapshot()[1] == "new-key"


@pytest.mark.parametrize("operation", [{"api_key": "replacement"}, {"delete_key": True}])
def test_stt_failed_file_commit_restores_target_profile(tmp_path, vault, monkeypatch, operation):
    store = SettingsStore(tmp_path)
    store.save({"stt": {"cloud": {"base_url": "https://old.example/v1", "api_key": "original"}}})
    store.save({"stt": {"cloud": {"base_url": "https://new.example/v1", "api_key": "target-key"}}})
    store.save({"stt": {"cloud": {"base_url": "https://old.example/v1"}}})
    before_config, before_file, before_vault = (
        store.snapshot(),
        store.path.read_bytes(),
        dict(vault),
    )

    def failed(*args):
        raise OSError("failed replacement")

    monkeypatch.setattr("sozvon.config.os.replace", failed)
    with pytest.raises(OSError):
        store.save({"stt": {"cloud": {"base_url": "https://new.example/v1", **operation}}})
    assert store.snapshot() == before_config
    assert store.path.read_bytes() == before_file
    assert vault == before_vault
    assert not store.path.with_suffix(".toml.pending").exists()


@pytest.mark.parametrize("operation", [{"api_key": "replacement"}, {"delete_key": True}])
def test_stt_unreadable_vault_cannot_mutate_secrets(tmp_path, vault, monkeypatch, operation):
    store = SettingsStore(tmp_path)
    store.save({"stt": {"cloud": {"api_key": "original"}}})
    before = store.path.read_bytes(), store.snapshot(), dict(vault)

    def failed(*args):
        raise RuntimeError("secret-sensitive-detail")

    monkeypatch.setattr("keyring.get_password", failed)
    with pytest.raises(ValueError) as error:
        store.save({"stt": {"cloud": operation}})
    assert "secret-sensitive-detail" not in str(error.value)
    assert (store.path.read_bytes(), store.snapshot(), vault) == before


@pytest.mark.parametrize("key", ["space key", "line\nkey", "юникод", "bad\x7f"])
def test_stt_invalid_bearer_key_is_rejected_before_io(tmp_path, vault, monkeypatch, key):
    store = SettingsStore(tmp_path)

    def forbid(*args):
        pytest.fail("invalid secret reached keyring")

    monkeypatch.setattr("keyring.get_password", forbid)
    with pytest.raises(ValueError):
        store.save({"stt": {"cloud": {"api_key": key}}})
    assert not store.path.exists() and not vault


def test_local_mode_does_not_require_locked_cloud_vault(tmp_path, vault, monkeypatch):
    store = SettingsStore(tmp_path)
    store.save({"stt": {"engine": "local", "cloud": {"allow_remote": True}}})

    def unavailable(*args):
        raise RuntimeError("private-backend")

    monkeypatch.setattr("keyring.get_password", unavailable)
    config, key = store.stt_snapshot()
    assert config["stt"]["engine"] == "local" and key is None


def test_old_config_defaults_to_local(tmp_path, vault):
    (tmp_path / "config.toml").write_text(
        '[stt]\nmodel_path="/models/local"\ndevice="cpu"\n[llm]\nbase_url="https://old.example/v1"\n'
    )
    old_hash = (
        "base-url:"
        + hashlib.sha256(
            json.dumps([str(tmp_path.resolve()), "https://old.example/v1"]).encode()
        ).hexdigest()
    )
    vault[("sozvon-llm", old_hash)] = "old-llm-key"
    store = SettingsStore(tmp_path)
    config, key = store.report_snapshot()
    assert key == "old-llm-key"
    assert config["stt"]["engine"] == "local"
    assert config["stt"]["model_path"] == "/models/local"
    assert config["stt"]["cloud"] == {
        "base_url": "https://api.openai.com/v1",
        "model": "whisper-1",
        "response_format": "verbose_json",
        "timeout_s": 120,
        "max_upload_bytes": 24_000_000,
        "allow_remote": False,
    }
