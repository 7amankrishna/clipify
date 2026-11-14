from pathlib import Path
from uuid import uuid4
import shutil

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.media_tools import MediaProbeError, media_tools_status, probe_video
from app.db.session import get_db
from app.models import MediaAsset, ProcessingJob, Project
from app.schemas.projects import DashboardResponse, ProjectCreate, ProjectResponse, ProjectUpdate

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
