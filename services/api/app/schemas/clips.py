from datetime import datetime
from pydantic import BaseModel, Field


class ClipAnalysisCreate(BaseModel):
    asset_id: int
    min_duration_seconds: int = Field(default=15, ge=8, le=120)
    max_duration_seconds: int = Field(default=60, ge=10, le=180)


class ClipSuggestionResponse(BaseModel):
    id: int
    project_id: int
    asset_id: int
    analysis_job_id: int
    start_seconds: float
    end_seconds: float
    score: int
    transcript_text: str
    title: str
    reasons: list[str]
    metrics: dict
    created_at: datetime
