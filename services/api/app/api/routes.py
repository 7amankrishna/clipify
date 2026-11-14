from pathlib import Path
from uuid import uuid4
import os
import shutil

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.media_tools import MediaProbeError, media_tools_status, probe_video
from app.db.session import get_db
from app.models import CaptionTrack, ClipEdit, ClipSuggestion, MediaAsset, ProcessingJob, Project, Transcript, VideoExport
from app.schemas.clips import ClipAnalysisCreate, ClipSuggestionResponse
from app.schemas.editor import ClipEditCreate, ClipEditResponse, ClipEditUpdate, WaveformResponse
from app.schemas.export import CaptionResponse, CaptionUpdate, ExportCreate, ExportResponse
from app.schemas.projects import DashboardResponse, ProjectCreate, ProjectResponse, ProjectUpdate
from app.schemas.transcription import JobResponse, TranscriptResponse, TranscriptionCreate
from app.services.transcription import request_cancel, serialize_job, serialize_transcript, submit
from app.services.clip_detection import serialize_suggestion, submit as submit_clip_analysis
from app.services.media_workspace import WaveformError, waveform_peaks
from app.services.rendering import serialize_caption, serialize_export, submit as submit_render

router = APIRouter()
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}


def serialize_asset(asset: MediaAsset) -> dict:
    return {
        "id": asset.id,
        "original_path": asset.original_path,
        "media_type": asset.media_type,
        "duration_seconds": asset.duration_seconds,
        "width": asset.width,
        "height": asset.height,
        "fps": asset.fps,
        "size_bytes": asset.size_bytes,
        "created_at": asset.created_at,
    }


def serialize_project(project: Project, assets: list[MediaAsset]) -> dict:
    return {
        "id": project.id,
        "name": project.name,
        "status": project.status,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
        "assets": [serialize_asset(asset) for asset in assets],
    }


def serialize_edit(edit: ClipEdit) -> dict:
    return {"id": edit.id, "project_id": edit.project_id, "asset_id": edit.asset_id, "suggestion_id": edit.suggestion_id, "start_seconds": edit.start_seconds, "end_seconds": edit.end_seconds, "aspect_ratio": edit.aspect_ratio, "crop_mode": edit.crop_mode, "manual_x": edit.manual_x, "manual_y": edit.manual_y, "created_at": edit.created_at, "updated_at": edit.updated_at}


def validate_clip_bounds(asset: MediaAsset, start: float, end: float) -> None:
    if end <= start:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Clip end must be after its start.")
    if asset.duration_seconds is not None and end > asset.duration_seconds + 0.1:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Clip end cannot be beyond the source-video duration.")


def caption_words_for_edit(edit: ClipEdit, database: Session) -> list[dict]:
    """Return editable, timestamped caption words for the clip's source transcript."""
    transcript = database.scalar(
        select(Transcript).where(Transcript.asset_id == edit.asset_id).order_by(Transcript.created_at.desc())
    )
    if not transcript:
        return []
    words: list[dict] = []
    for segment in transcript.segments_json:
        segment_words = segment.get("words") or []
        if segment_words:
            for item in segment_words:
                start, end = float(item.get("start", 0)), float(item.get("end", 0))
                if end > edit.start_seconds and start < edit.end_seconds and str(item.get("word", "")).strip():
                    words.append({"word": str(item["word"]).strip(), "start": start, "end": max(end, start + 0.01), "line": 0})
            continue
        # Some transcription backends omit per-word data; preserve a usable caption fallback.
        text_words = str(segment.get("text", "")).split()
        start, end = float(segment.get("start", 0)), float(segment.get("end", 0))
        if not text_words or end <= edit.start_seconds or start >= edit.end_seconds:
            continue
        span = max(0.01, (end - start) / len(text_words))
        for index, word in enumerate(text_words):
            word_start, word_end = start + index * span, start + (index + 1) * span
            if word_end > edit.start_seconds and word_start < edit.end_seconds:
                words.append({"word": word, "start": word_start, "end": word_end, "line": 0})
    # Short grouped lines give readable mobile captions, while still retaining word timing.
    for index, word in enumerate(words):
        word["line"] = index // 5
    return words


def project_or_404(project_id: int, database: Session) -> Project:
    project = database.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")
    return project


def get_project_assets(project_id: int, database: Session) -> list[MediaAsset]:
    return list(database.scalars(select(MediaAsset).where(MediaAsset.project_id == project_id).order_by(MediaAsset.created_at.desc())))


@router.get("/health")
def health_check(database: Session = Depends(get_db)) -> dict:
    database.execute(text("SELECT 1"))
    tools = media_tools_status()
    return {
        "status": "ok",
        "service": settings.app_name,
        "version": settings.app_version,
        "database": "connected",
        "tools": {
            key: {"available": tool.available, "path": tool.path, "version": tool.version}
            for key, tool in tools.items()
        },
    }


@router.get("/dashboard", response_model=DashboardResponse)
def dashboard(database: Session = Depends(get_db)) -> dict:
    projects = list(database.scalars(select(Project).order_by(Project.updated_at.desc()).limit(8)))
    total_size = database.scalar(select(func.coalesce(func.sum(MediaAsset.size_bytes), 0))) or 0
    asset_count = database.scalar(select(func.count(MediaAsset.id))) or 0
    return {
        "project_count": database.scalar(select(func.count(Project.id))) or 0,
        "imported_video_count": asset_count,
        "storage_bytes": int(total_size),
        "recent_projects": [serialize_project(project, get_project_assets(project.id, database)) for project in projects],
    }


@router.post("/projects", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, database: Session = Depends(get_db)) -> dict:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Project name cannot be blank.")
    project = Project(name=name)
    database.add(project)
    database.commit()
    database.refresh(project)
    return serialize_project(project, [])


@router.get("/projects/{project_id}", response_model=ProjectResponse)
def get_project(project_id: int, database: Session = Depends(get_db)) -> dict:
    project = project_or_404(project_id, database)
    return serialize_project(project, get_project_assets(project_id, database))


@router.patch("/projects/{project_id}", response_model=ProjectResponse)
def update_project(project_id: int, payload: ProjectUpdate, database: Session = Depends(get_db)) -> dict:
    project = project_or_404(project_id, database)
    if payload.name is not None:
        project.name = payload.name.strip()
        if not project.name:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Project name cannot be blank.")
    if payload.status is not None:
        project.status = payload.status
    database.commit()
    database.refresh(project)
    return serialize_project(project, get_project_assets(project_id, database))


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: int, database: Session = Depends(get_db)) -> None:
    project = project_or_404(project_id, database)
    edit_ids = select(ClipEdit.id).where(ClipEdit.project_id == project_id)
    database.query(VideoExport).filter(VideoExport.project_id == project_id).delete()
    database.query(CaptionTrack).filter(CaptionTrack.clip_edit_id.in_(edit_ids)).delete(synchronize_session=False)
    database.query(ClipEdit).filter(ClipEdit.project_id == project_id).delete()
    database.query(ClipSuggestion).filter(ClipSuggestion.project_id == project_id).delete()
    database.query(Transcript).filter(Transcript.project_id == project_id).delete()
    database.query(ProcessingJob).filter(ProcessingJob.project_id == project_id).delete()
    database.query(MediaAsset).filter(MediaAsset.project_id == project_id).delete()
    database.delete(project)
    database.commit()
    project_dir = settings.storage_dir / "projects" / str(project_id)
    shutil.rmtree(project_dir, ignore_errors=True)


@router.post("/projects/{project_id}/media", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def import_video(
    project_id: int,
    file: UploadFile = File(...),
    database: Session = Depends(get_db),
) -> dict:
    project = project_or_404(project_id, database)
    filename = Path(file.filename or "").name
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_VIDEO_EXTENSIONS:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Use an MP4, MOV, MKV, or WebM video file.")
    destination_dir = settings.storage_dir / "projects" / str(project_id) / "source"
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"{uuid4().hex}{extension}"
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    written = 0
    try:
        with destination.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=f"Videos must be under {settings.max_upload_size_mb} MB.")
                output.write(chunk)
        metadata = probe_video(destination)
    except HTTPException:
        destination.unlink(missing_ok=True)
        raise
    except MediaProbeError as exc:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Video import failed: {exc}") from exc
    finally:
        await file.close()
    asset = MediaAsset(
        project_id=project.id,
        original_path=str(destination.resolve()),
        media_type=extension.lstrip("."),
        duration_seconds=metadata["duration_seconds"],
        width=metadata["width"],
        height=metadata["height"],
        fps=metadata["fps"],
        size_bytes=written,
    )
    project.status = "imported"
    database.add(asset)
    database.commit()
    database.refresh(project)
    return serialize_project(project, get_project_assets(project_id, database))


@router.post("/projects/{project_id}/transcriptions", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
def start_transcription(project_id: int, payload: TranscriptionCreate, database: Session = Depends(get_db)) -> dict:
    project_or_404(project_id, database)
    asset = database.get(MediaAsset, payload.asset_id)
    if not asset or asset.project_id != project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source video not found in this project.")
    job = ProcessingJob(
        project_id=project_id,
        job_type="transcription",
        status="queued",
        progress=0,
        metadata_json={"asset_id": asset.id, "model": payload.model, "language": payload.language, "device": payload.device, "attempt": 1, "cancel_requested": False},
    )
    database.add(job)
    database.commit()
    database.refresh(job)
    submit(job.id)
    return serialize_job(job)


@router.get("/projects/{project_id}/transcripts", response_model=list[TranscriptResponse])
def list_transcripts(project_id: int, database: Session = Depends(get_db)) -> list[dict]:
    project_or_404(project_id, database)
    transcripts = list(database.scalars(select(Transcript).where(Transcript.project_id == project_id).order_by(Transcript.created_at.desc())))
    return [serialize_transcript(transcript) for transcript in transcripts]


@router.post("/projects/{project_id}/clip-analysis", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
def start_clip_analysis(project_id: int, payload: ClipAnalysisCreate, database: Session = Depends(get_db)) -> dict:
    project_or_404(project_id, database)
    if payload.min_duration_seconds >= payload.max_duration_seconds:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Maximum clip duration must be greater than minimum duration.")
    asset = database.get(MediaAsset, payload.asset_id)
    transcript = database.scalar(select(Transcript).where(Transcript.asset_id == payload.asset_id).order_by(Transcript.created_at.desc()))
    if not asset or asset.project_id != project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source video not found in this project.")
    if not transcript:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Generate a transcript before analyzing clips.")
    job = ProcessingJob(project_id=project_id, job_type="clip_analysis", status="queued", progress=0, metadata_json={"asset_id": asset.id, "min_duration_seconds": payload.min_duration_seconds, "max_duration_seconds": payload.max_duration_seconds, "attempt": 1, "cancel_requested": False})
    database.add(job)
    database.commit()
    database.refresh(job)
    submit_clip_analysis(job.id)
    return serialize_job(job)


@router.get("/projects/{project_id}/suggestions", response_model=list[ClipSuggestionResponse])
def list_suggestions(project_id: int, asset_id: int | None = None, database: Session = Depends(get_db)) -> list[dict]:
    project_or_404(project_id, database)
    query = select(ClipSuggestion).where(ClipSuggestion.project_id == project_id)
    if asset_id is not None:
        query = query.where(ClipSuggestion.asset_id == asset_id)
    suggestions = list(database.scalars(query.order_by(ClipSuggestion.score.desc(), ClipSuggestion.created_at.desc())))
    return [serialize_suggestion(suggestion) for suggestion in suggestions]


@router.get("/media/{asset_id}/stream")
def stream_media(asset_id: int, database: Session = Depends(get_db)) -> FileResponse:
    asset = database.get(MediaAsset, asset_id)
    path = Path(asset.original_path) if asset else None
    if not asset or not path or not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Local source video was not found.")
    return FileResponse(path, media_type=f"video/{asset.media_type}", filename=path.name)


@router.get("/media/{asset_id}/waveform", response_model=WaveformResponse)
def get_waveform(asset_id: int, points: int = 180, database: Session = Depends(get_db)) -> dict:
    asset = database.get(MediaAsset, asset_id)
    path = Path(asset.original_path) if asset else None
    if not asset or not path or not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Local source video was not found.")
    try:
        peaks = waveform_peaks(path, max(40, min(points, 400)))
    except WaveformError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return {"asset_id": asset.id, "duration_seconds": asset.duration_seconds, "peaks": peaks}


@router.post("/projects/{project_id}/edits", response_model=ClipEditResponse, status_code=status.HTTP_201_CREATED)
def create_clip_edit(project_id: int, payload: ClipEditCreate, database: Session = Depends(get_db)) -> dict:
    project_or_404(project_id, database)
    asset = database.get(MediaAsset, payload.asset_id)
    if not asset or asset.project_id != project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source video not found in this project.")
    suggestion = database.get(ClipSuggestion, payload.suggestion_id) if payload.suggestion_id else None
    if payload.suggestion_id and (not suggestion or suggestion.project_id != project_id or suggestion.asset_id != asset.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Suggestion not found for this source video.")
    if suggestion:
        existing = database.scalar(select(ClipEdit).where(ClipEdit.suggestion_id == suggestion.id))
        if existing:
            return serialize_edit(existing)
    start = payload.start_seconds if payload.start_seconds is not None else (suggestion.start_seconds if suggestion else None)
    end = payload.end_seconds if payload.end_seconds is not None else (suggestion.end_seconds if suggestion else None)
    if start is None or end is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Start and end are required when creating a manual clip.")
    validate_clip_bounds(asset, start, end)
    edit = ClipEdit(project_id=project_id, asset_id=asset.id, suggestion_id=suggestion.id if suggestion else None, start_seconds=start, end_seconds=end, aspect_ratio=payload.aspect_ratio, crop_mode=payload.crop_mode)
    database.add(edit)
    database.commit()
    database.refresh(edit)
    return serialize_edit(edit)


@router.get("/projects/{project_id}/edits", response_model=list[ClipEditResponse])
def list_clip_edits(project_id: int, database: Session = Depends(get_db)) -> list[dict]:
    project_or_404(project_id, database)
    edits = list(database.scalars(select(ClipEdit).where(ClipEdit.project_id == project_id).order_by(ClipEdit.updated_at.desc())))
    return [serialize_edit(edit) for edit in edits]


@router.patch("/edits/{edit_id}", response_model=ClipEditResponse)
def update_clip_edit(edit_id: int, payload: ClipEditUpdate, database: Session = Depends(get_db)) -> dict:
    edit = database.get(ClipEdit, edit_id)
    if not edit:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clip edit not found.")
    asset = database.get(MediaAsset, edit.asset_id)
    next_start = payload.start_seconds if payload.start_seconds is not None else edit.start_seconds
    next_end = payload.end_seconds if payload.end_seconds is not None else edit.end_seconds
    validate_clip_bounds(asset, next_start, next_end)
    for field in ("start_seconds", "end_seconds", "aspect_ratio", "crop_mode", "manual_x", "manual_y"):
        value = getattr(payload, field)
        if value is not None:
            setattr(edit, field, value)
    database.commit()
    database.refresh(edit)
    return serialize_edit(edit)


@router.get("/edits/{edit_id}/captions", response_model=CaptionResponse)
def get_captions(edit_id: int, database: Session = Depends(get_db)) -> dict:
    edit = database.get(ClipEdit, edit_id)
    if not edit:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clip edit not found.")
    track = database.scalar(select(CaptionTrack).where(CaptionTrack.clip_edit_id == edit.id))
    if not track:
        track = CaptionTrack(clip_edit_id=edit.id, words_json=caption_words_for_edit(edit, database))
        database.add(track)
        database.commit()
        database.refresh(track)
    return serialize_caption(track)


@router.patch("/edits/{edit_id}/captions", response_model=CaptionResponse)
def update_captions(edit_id: int, payload: CaptionUpdate, database: Session = Depends(get_db)) -> dict:
    edit = database.get(ClipEdit, edit_id)
    if not edit:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clip edit not found.")
    track = database.scalar(select(CaptionTrack).where(CaptionTrack.clip_edit_id == edit.id))
    if not track:
        track = CaptionTrack(clip_edit_id=edit.id, words_json=caption_words_for_edit(edit, database))
        database.add(track)
    if payload.preset is not None:
        track.preset = payload.preset
    if payload.words is not None:
        for word in payload.words:
            if word.end <= word.start:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Each caption word must end after it starts.")
        track.words_json = [word.model_dump() for word in payload.words]
    database.commit()
    database.refresh(track)
    return serialize_caption(track)


@router.post("/edits/{edit_id}/exports", response_model=ExportResponse, status_code=status.HTTP_202_ACCEPTED)
def create_export(edit_id: int, payload: ExportCreate, database: Session = Depends(get_db)) -> dict:
    edit = database.get(ClipEdit, edit_id)
    if not edit:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clip edit not found.")
    track = database.scalar(select(CaptionTrack).where(CaptionTrack.clip_edit_id == edit.id))
    if not track:
        track = CaptionTrack(clip_edit_id=edit.id, words_json=caption_words_for_edit(edit, database))
        database.add(track)
        database.flush()
    if not track.words_json:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="No transcript captions are available for this clip.")
    job = ProcessingJob(project_id=edit.project_id, job_type="render_export", status="queued", progress=0, metadata_json={"clip_edit_id": edit.id, "preview": payload.preview, "attempt": 1, "cancel_requested": False})
    database.add(job)
    database.flush()
    export = VideoExport(project_id=edit.project_id, clip_edit_id=edit.id, job_id=job.id, is_preview=payload.preview, status="queued")
    database.add(export)
    database.flush()
    job.metadata_json = {**job.metadata_json, "export_id": export.id}
    database.commit()
    database.refresh(export)
    submit_render(job.id)
    return serialize_export(export)


@router.get("/projects/{project_id}/exports", response_model=list[ExportResponse])
def list_exports(project_id: int, database: Session = Depends(get_db)) -> list[dict]:
    project_or_404(project_id, database)
    exports = list(database.scalars(select(VideoExport).where(VideoExport.project_id == project_id).order_by(VideoExport.created_at.desc())))
    return [serialize_export(item) for item in exports]


@router.get("/exports/{export_id}/download")
def download_export(export_id: int, database: Session = Depends(get_db)) -> FileResponse:
    export = database.get(VideoExport, export_id)
    path = Path(export.output_path) if export and export.output_path else None
    if not export or export.status != "completed" or not path or not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rendered video is not available.")
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@router.post("/exports/{export_id}/open")
def open_export(export_id: int, database: Session = Depends(get_db)) -> dict:
    export = database.get(VideoExport, export_id)
    path = Path(export.output_path) if export and export.output_path else None
    if not export or export.status != "completed" or not path or not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rendered video is not available.")
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    return {"path": str(path)}


@router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: int, database: Session = Depends(get_db)) -> dict:
    job = database.get(ProcessingJob, job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")
    return serialize_job(job)


@router.post("/jobs/{job_id}/cancel", response_model=JobResponse)
def cancel_job(job_id: int, database: Session = Depends(get_db)) -> dict:
    job = database.get(ProcessingJob, job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")
    if job.status not in {"queued", "processing", "cancel_requested"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only queued or running jobs can be cancelled.")
    request_cancel(job.id)
    database.refresh(job)
    return serialize_job(job)


@router.post("/jobs/{job_id}/retry", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
def retry_job(job_id: int, database: Session = Depends(get_db)) -> dict:
    job = database.get(ProcessingJob, job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")
    if job.job_type not in {"transcription", "clip_analysis", "render_export"} or job.status not in {"failed", "cancelled"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only failed or cancelled processing jobs can be retried.")
    metadata = dict(job.metadata_json or {})
    metadata["attempt"] = int(metadata.get("attempt", 0)) + 1
    metadata["cancel_requested"] = False
    job.metadata_json = metadata
    job.status = "queued"
    job.progress = 0
    job.error_message = None
    database.commit()
    database.refresh(job)
    if job.job_type == "transcription":
        submit(job.id)
    elif job.job_type == "clip_analysis":
        submit_clip_analysis(job.id)
    else:
        export = database.get(VideoExport, metadata.get("export_id"))
        if export:
            export.status = "queued"
            export.error_message = None
            database.commit()
        submit_render(job.id)
    return serialize_job(job)
