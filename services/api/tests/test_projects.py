from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import select
from app.main import app
from app.core.media_tools import probe_video
from app.db.session import SessionLocal
from app.models import ClipSuggestion, ProcessingJob, Transcript


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


def test_clip_analysis_ranks_and_persists_non_overlapping_suggestions(monkeypatch):
    monkeypatch.setattr("app.api.routes.probe_video", lambda _: {"duration_seconds": 40.0, "width": 1920, "height": 1080, "fps": 30.0})
    monkeypatch.setattr("app.api.routes.submit_clip_analysis", lambda _: None)
    sample_text = "How do you avoid this common mistake? Here is the simple powerful answer that changes everything for creators."
    segments = [{"start": index * 8.0, "end": (index + 1) * 8.0, "text": sample_text, "words": []} for index in range(5)]
    with TestClient(app) as client:
        project_id = client.post("/api/v1/projects", json={"name": "Clip analysis"}).json()["id"]
        asset = client.post(f"/api/v1/projects/{project_id}/media", files={"file": ("source.mp4", b"source", "video/mp4")}).json()["assets"][0]
        database = SessionLocal()
        transcription_job = ProcessingJob(project_id=project_id, job_type="transcription", status="completed", progress=100, metadata_json={})
        database.add(transcription_job)
        database.commit()
        database.refresh(transcription_job)
        database.add(Transcript(project_id=project_id, asset_id=asset["id"], job_id=transcription_job.id, language="en", language_probability=0.99, segments_json=segments, transcript_path="/tmp/test-transcript.json"))
        database.commit()
        database.close()
        analysis = client.post(f"/api/v1/projects/{project_id}/clip-analysis", json={"asset_id": asset["id"], "min_duration_seconds": 8, "max_duration_seconds": 24})
        assert analysis.status_code == 202
        analysis_job_id = analysis.json()["id"]

        monkeypatch.setattr("app.services.clip_detection.audio_profile", lambda _path, candidates: ([], {index: -12.0 for index in range(len(candidates))}))
        from app.services.clip_detection import run_analysis
        run_analysis(analysis_job_id)

        completed = client.get(f"/api/v1/jobs/{analysis_job_id}").json()
        suggestions = client.get(f"/api/v1/projects/{project_id}/suggestions?asset_id={asset['id']}").json()
        assert completed["status"] == "completed"
        assert suggestions[0]["score"] > 50
        assert suggestions[0]["reasons"]
        for earlier, later in zip(suggestions, suggestions[1:]):
            assert min(earlier["end_seconds"], later["end_seconds"]) - max(earlier["start_seconds"], later["start_seconds"]) < min(earlier["end_seconds"] - earlier["start_seconds"], later["end_seconds"] - later["start_seconds"]) * 0.56


def test_clip_edit_persists_timeline_and_crop_settings(monkeypatch):
    monkeypatch.setattr("app.api.routes.probe_video", lambda _: {"duration_seconds": 90.0, "width": 1920, "height": 1080, "fps": 30.0})
    with TestClient(app) as client:
        project_id = client.post("/api/v1/projects", json={"name": "Editor persistence"}).json()["id"]
        asset = client.post(f"/api/v1/projects/{project_id}/media", files={"file": ("source.mp4", b"source", "video/mp4")}).json()["assets"][0]
        database = SessionLocal()
        analysis_job = ProcessingJob(project_id=project_id, job_type="clip_analysis", status="completed", progress=100, metadata_json={})
        database.add(analysis_job)
        database.commit(); database.refresh(analysis_job)
        suggestion = ClipSuggestion(project_id=project_id, asset_id=asset["id"], analysis_job_id=analysis_job.id, start_seconds=10.0, end_seconds=35.0, score=82, transcript_text="A suggested section", title="Suggested section", reasons_json=["Hook language appears early"], metrics_json={})
        database.add(suggestion); database.commit(); database.refresh(suggestion); database.close()
        edit = client.post(f"/api/v1/projects/{project_id}/edits", json={"asset_id": asset["id"], "suggestion_id": suggestion.id})
        assert edit.status_code == 201
        assert edit.json()["start_seconds"] == 10.0
        changed = client.patch(f"/api/v1/edits/{edit.json()['id']}", json={"start_seconds": 12.5, "end_seconds": 31.5, "aspect_ratio": "1:1", "crop_mode": "manual", "manual_x": 24, "manual_y": 72})
        assert changed.status_code == 200
        assert changed.json()["aspect_ratio"] == "1:1"
        assert changed.json()["manual_x"] == 24
        saved = client.get(f"/api/v1/projects/{project_id}/edits").json()[0]
        assert saved["end_seconds"] == 31.5


def test_caption_tracks_and_export_jobs_are_persisted(monkeypatch):
    monkeypatch.setattr("app.api.routes.probe_video", lambda _: {"duration_seconds": 40.0, "width": 1920, "height": 1080, "fps": 30.0})
    monkeypatch.setattr("app.api.routes.submit_render", lambda _: None)
    with TestClient(app) as client:
        project_id = client.post("/api/v1/projects", json={"name": "Caption export"}).json()["id"]
        asset = client.post(f"/api/v1/projects/{project_id}/media", files={"file": ("source.mp4", b"source", "video/mp4")}).json()["assets"][0]
        database = SessionLocal()
        transcription_job = ProcessingJob(project_id=project_id, job_type="transcription", status="completed", progress=100, metadata_json={})
        database.add(transcription_job); database.commit(); database.refresh(transcription_job)
        database.add(Transcript(project_id=project_id, asset_id=asset["id"], job_id=transcription_job.id, language="en", language_probability=.99, transcript_path="/tmp/captions.json", segments_json=[{"start": 2.0, "end": 5.0, "text": "Make better videos today", "words": [{"word": "Make", "start": 2.0, "end": 2.5}, {"word": "better", "start": 2.5, "end": 3.2}, {"word": "videos", "start": 3.2, "end": 4.1}, {"word": "today", "start": 4.1, "end": 5.0}]}]))
        database.add(ClipSuggestion(project_id=project_id, asset_id=asset["id"], analysis_job_id=transcription_job.id, start_seconds=2.0, end_seconds=5.0, score=90, transcript_text="Make better videos today", title="Make better videos", reasons_json=["Hook"], metrics_json={}))
        database.commit(); suggestion = database.scalar(select(ClipSuggestion).where(ClipSuggestion.project_id == project_id)); database.close()
        edit = client.post(f"/api/v1/projects/{project_id}/edits", json={"asset_id": asset["id"], "suggestion_id": suggestion.id}).json()
        captions = client.get(f"/api/v1/edits/{edit['id']}/captions")
        assert captions.status_code == 200
        assert [word["word"] for word in captions.json()["words"]] == ["Make", "better", "videos", "today"]
        updated = client.patch(f"/api/v1/edits/{edit['id']}/captions", json={"preset": "neon_creator", "words": captions.json()["words"]})
        assert updated.json()["preset"] == "neon_creator"
        queued = client.post(f"/api/v1/edits/{edit['id']}/exports", json={"preview": False})
        assert queued.status_code == 202
        assert queued.json()["status"] == "queued"
        assert client.get(f"/api/v1/projects/{project_id}/exports").json()[0]["job_id"] == queued.json()["job_id"]
