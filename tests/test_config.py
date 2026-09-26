import pytest


def test_config_atomic_and_no_secret(tmp_path):
    from sozvon.config import SettingsStore
    store = SettingsStore(tmp_path)
    assert store.public()["llm"]["allow_remote"] is False
    store.save({"llm": {"model": "local-test", "base_url": "http://127.0.0.1:1234/v1"}})
    assert SettingsStore(tmp_path).public()["llm"]["model"] == "local-test"
    before = (tmp_path / "config.toml").read_bytes()
    with pytest.raises(ValueError):
        store.save({"llm": {"model": "changed"}, "stt": {"device": "bad-device"}})
    assert store.public()["llm"]["model"] == "local-test"
    assert (tmp_path / "config.toml").read_bytes() == before


def test_remote_http_rejected_and_system_device_reset(tmp_path):
    from sozvon.config import SettingsStore
    store = SettingsStore(tmp_path)
    with pytest.raises(ValueError):
        store.save({"llm": {"base_url": "http://example.com/v1"}})
    store.save({"recording": {"input_device": "12"}})
    store.save({"recording": {"input_device": None}})
    assert store.public()["recording"]["input_device"] is None


def test_secret_only_in_vault(tmp_path, monkeypatch):
    from sozvon.config import SettingsStore
    vault = {}
    monkeypatch.setattr("keyring.set_password", lambda s, u, v: vault.update({(s, u): v}))
    monkeypatch.setattr("keyring.get_password", lambda s, u: vault.get((s, u)))
    store = SettingsStore(tmp_path)
    store.save({"llm": {"api_key": "test-not-a-real-secret"}})
    assert store.public()["llm"]["configured"]
    assert "test-not-a-real-secret" not in str(store.public())
    assert "test-not-a-real-secret" not in (tmp_path / "config.toml").read_text()
    assert store.secret() == "test-not-a-real-secret"
