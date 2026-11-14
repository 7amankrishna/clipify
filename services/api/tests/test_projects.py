from pathlib import Path
from types import SimpleNamespace

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


def test_transcription_job_can_be_queued_cancelled_and_retried(monkeypatch):
    monkeypatch.setattr("app.api.routes.probe_video", lambda _: {"duration_seconds": 12.0, "width": 1920, "height": 1080, "fps": 25.0})
    monkeypatch.setattr("app.api.routes.submit", lambda _: None)
    with TestClient(app) as client:
        project_id = client.post("/api/v1/projects", json={"name": "Transcription test"}).json()["id"]
        asset = client.post(
            f"/api/v1/projects/{project_id}/media",
            files={"file": ("source.webm", b"test-video-bytes", "video/webm")},
        ).json()["assets"][0]
        queued = client.post(
            f"/api/v1/projects/{project_id}/transcriptions",
            json={"asset_id": asset["id"], "model": "base", "language": "hinglish", "device": "cpu"},
        )
        assert queued.status_code == 202
        job_id = queued.json()["id"]
        assert queued.json()["status"] == "queued"
        assert queued.json()["metadata_json"]["language"] == "hinglish"
        assert client.post(f"/api/v1/jobs/{job_id}/cancel").json()["status"] == "cancelled"
        retried = client.post(f"/api/v1/jobs/{job_id}/retry")
        assert retried.status_code == 202
        assert retried.json()["status"] == "queued"
        assert retried.json()["metadata_json"]["attempt"] == 2


def test_worker_persists_word_timestamped_transcript(monkeypatch):
    monkeypatch.setattr("app.api.routes.probe_video", lambda _: {"duration_seconds": 8.0, "width": 1280, "height": 720, "fps": 30.0})
    monkeypatch.setattr("app.api.routes.submit", lambda _: None)
    with TestClient(app) as client:
        project_id = client.post("/api/v1/projects", json={"name": "Worker test"}).json()["id"]
        asset = client.post(f"/api/v1/projects/{project_id}/media", files={"file": ("source.mp4", b"source", "video/mp4")}).json()["assets"][0]
        job_id = client.post(f"/api/v1/projects/{project_id}/transcriptions", json={"asset_id": asset["id"]}).json()["id"]

        class FakeModel:
            def transcribe(self, *_args, **_kwargs):
                segment = SimpleNamespace(start=0.0, end=1.2, text=" Hello world ", words=[SimpleNamespace(word=" Hello", start=0.0, end=0.5, probability=0.98), SimpleNamespace(word=" world", start=0.6, end=1.2, probability=0.97)])
                return iter([segment]), SimpleNamespace(language="en", language_probability=0.99)

        monkeypatch.setattr("app.services.transcription._extract_audio", lambda _source, destination, _job: destination.parent.mkdir(parents=True, exist_ok=True) or destination.touch())
        monkeypatch.setattr("app.services.transcription._load_model", lambda *_: (FakeModel(), "cpu"))
        from app.services.transcription import run_transcription
        run_transcription(job_id)

        completed = client.get(f"/api/v1/jobs/{job_id}").json()
        transcript = client.get(f"/api/v1/projects/{project_id}/transcripts").json()[0]
        assert completed["status"] == "completed"
        assert completed["progress"] == 100
        assert transcript["segments"][0]["words"][1]["word"] == "world"
