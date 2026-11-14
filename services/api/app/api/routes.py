from pathlib import Path
from uuid import uuid4
import shutil

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.media_tools import MediaProbeError, media_tools_status, probe_video
from app.db.session import get_db
from app.models import ClipSuggestion, MediaAsset, ProcessingJob, Project, Transcript
from app.schemas.clips import ClipAnalysisCreate, ClipSuggestionResponse
from app.schemas.projects import DashboardResponse, ProjectCreate, ProjectResponse, ProjectUpdate
from app.schemas.transcription import JobResponse, TranscriptResponse, TranscriptionCreate
from app.services.transcription import request_cancel, serialize_job, serialize_transcript, submit
from app.services.clip_detection import serialize_suggestion, submit as submit_clip_analysis

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
    if job.job_type not in {"transcription", "clip_analysis"} or job.status not in {"failed", "cancelled"}:
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
    else:
        submit_clip_analysis(job.id)
    return serialize_job(job)
