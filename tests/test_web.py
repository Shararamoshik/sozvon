import threading

from fastapi.testclient import TestClient


def open_client(tmp_path, worker_factory=None):
    from sozvon.web.server import create_app
    app = create_app(tmp_path, worker_factory=worker_factory)
    client = TestClient(app, base_url="http://127.0.0.1")
    origin = {"Origin": "http://127.0.0.1"}
    response = client.post("/api/session", json={"key": app.state.launch_key}, headers=origin)
    assert response.status_code == 200
    headers = {**origin, "X-CSRF-Token": response.json()["csrf"]}
    return app, client, headers


def test_local_api_requires_session_and_origin(tmp_path):
    from sozvon.web.server import create_app
    app = create_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/meetings").status_code == 401
        assert client.post("/api/session", json={"key": app.state.launch_key},
                           headers={"Origin": "https://evil.invalid"}).status_code == 403
    _, client, headers = open_client(tmp_path)
    with client:
        assert client.post("/api/import/text", json={"text": "Привет"}).status_code == 403
        assert client.post("/api/import/text", json={"text": "Привет"}, headers=headers).status_code == 200
        assert client.get("/api/meetings", headers={"Host": "evil.invalid"}).status_code == 400


def test_import_notes_export_survive_restart(tmp_path):
    _, client, headers = open_client(tmp_path)
    with client:
        mid = client.post("/api/import/text", json={"title": "Созвон", "text": "Анна: привет"},
                          headers=headers).json()["id"]
        page = client.get(f"/api/meetings/{mid}").json()
        assert page["segments"][0]["start_ms"] is None
        assert page["job"] is None
        assert client.put(f"/api/meetings/{mid}/notes", json={"text": "Заметка"},
                          headers=headers).status_code == 200
        assert "Анна: привет" in client.get(f"/api/meetings/{mid}/export?format=md").text
    _, again, _ = open_client(tmp_path)
    with again:
        assert again.get(f"/api/meetings/{mid}").json()["notes"] == "Заметка"


def test_missing_model_and_cloud_consent_fail_before_job(tmp_path):
    _, client, headers = open_client(tmp_path)
    with client:
        mid = client.post("/api/import/text", json={"text": "Обсудили план"}, headers=headers).json()["id"]
        assert client.post(f"/api/meetings/{mid}/report", json={}, headers=headers).status_code == 409
        assert client.put("/api/settings", json={"llm": {"base_url": "https://example.invalid/v1", "model": "m"}},
                          headers=headers).status_code == 200
        response = client.post(f"/api/meetings/{mid}/report", json={}, headers=headers)
        assert response.status_code == 409
        assert "внешн" in response.json()["detail"].lower()
        assert client.get("/api/meetings").json()["job"] is None


def test_background_result_saved_without_stt(tmp_path):
    calls = []
    finished = threading.Event()

    class Worker:
        def run(self, operation, payload, **kwargs):
            calls.append(operation)
            finished.set()
            return {"document": {"summary": [], "tasks": []}, "model": "test"}

    app, client, headers = open_client(tmp_path, Worker)
    with client:
        client.put("/api/settings", json={"llm": {"model": "test"}}, headers=headers)
        mid = client.post("/api/import/text", json={"text": "Текст"}, headers=headers).json()["id"]
        response = client.post(f"/api/meetings/{mid}/report", json={}, headers=headers)
        assert response.status_code == 202
        assert finished.wait(3)
        app.state.service.wait(3)
        result = client.get(f"/api/meetings/{mid}").json()
        assert len(result["reports"]) == 1
        assert calls == ["report"]


def test_shutdown_and_cancel_do_not_publish_late_report(tmp_path):
    from sozvon.services.application import Application
    started = threading.Event()

    class Late:
        def run(self, operation, payload, **kwargs):
            started.set()
            kwargs["stop_event"].wait(3)
            return {"document": {"summary": []}, "model": "late"}

    service = Application(tmp_path, worker_factory=Late)
    service.settings.save({"llm": {"model": "test"}})
    mid = service.repo.create_text("test", "text")
    jid = service.report(mid)
    assert started.wait(3)
    service.stop(jid)
    service.wait(3)
    assert service.repo.detail(mid)["reports"] == []
    assert service.repo.job(jid)["status"] == "cancelled"
    service.close()
