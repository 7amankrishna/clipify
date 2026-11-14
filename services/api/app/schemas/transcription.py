from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class TranscriptionCreate(BaseModel):
    asset_id: int
    model: Literal["tiny", "base", "small", "medium", "large-v3"] = "base"
    language: Literal["auto", "en", "hi", "hinglish"] = "auto"
    device: Literal["auto", "cpu", "cuda"] = "auto"


class JobResponse(BaseModel):
    id: int
    project_id: int | None
    job_type: str
    status: str
    progress: int
    error_message: str | None
    metadata_json: dict | None
    created_at: datetime
    updated_at: datetime


class WordResponse(BaseModel):
    word: str
    start: float
    end: float
    probability: float | None = None


class TranscriptSegmentResponse(BaseModel):
    start: float
    end: float
    text: str
    words: list[WordResponse]


class TranscriptResponse(BaseModel):
    id: int
    project_id: int
    asset_id: int
    job_id: int
    language: str | None
    language_probability: float | None
    segments: list[TranscriptSegmentResponse]
    created_at: datetime
