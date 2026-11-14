from pathlib import Path

from fastapi.testclient import TestClient
from app.main import app
from app.core.media_tools import probe_video


def test_create_import_reopen_and_delete_project(monkeypatch):
    monkeypatch.setattr("app.api.routes.probe_video", lambda _: {"duration_seconds": 15.4, "width": 1920, "height": 1080, "fps": 30.0})
    with TestClient(app) as client:
        created = client.post("/api/v1/projects", json={"name": "Podcast Episode 01"})
        assert created.status_code == 201
        project_id = created.json()["id"]

        imported = client.post(
            f"/api/v1/projects/{project_id}/media",
            files={"file": ("episode.mp4", b"test-video-bytes", "video/mp4")},
        )
        assert imported.status_code == 201
        asset = imported.json()["assets"][0]
        assert asset["width"] == 1920
        assert asset["duration_seconds"] == 15.4
        assert Path(asset["original_path"]).exists()

    with TestClient(app) as restarted_client:
        reopened = restarted_client.get(f"/api/v1/projects/{project_id}")
        assert reopened.status_code == 200
        assert reopened.json()["name"] == "Podcast Episode 01"
        assert len(reopened.json()["assets"]) == 1
        dashboard = restarted_client.get("/api/v1/dashboard").json()
        assert dashboard["storage_bytes"] == len(b"test-video-bytes")
        assert restarted_client.delete(f"/api/v1/projects/{project_id}").status_code == 204
        assert restarted_client.get(f"/api/v1/projects/{project_id}").status_code == 404


def test_rejects_unsupported_import():
    with TestClient(app) as client:
        project_id = client.post("/api/v1/projects", json={"name": "Invalid format"}).json()["id"]
        response = client.post(f"/api/v1/projects/{project_id}/media", files={"file": ("notes.txt", b"not video", "text/plain")})
    assert response.status_code == 415
