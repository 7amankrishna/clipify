from datetime import datetime
from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    status: str | None = Field(default=None, max_length=32)


class MediaAssetResponse(BaseModel):
    id: int
    original_path: str
    media_type: str
    duration_seconds: float | None
    width: int | None
    height: int | None
    fps: float | None
    size_bytes: int | None
    created_at: datetime


class ProjectResponse(BaseModel):
    id: int
    name: str
    status: str
    created_at: datetime
    updated_at: datetime
    assets: list[MediaAssetResponse] = []


class DashboardResponse(BaseModel):
    project_count: int
    imported_video_count: int
    storage_bytes: int
    recent_projects: list[ProjectResponse]
