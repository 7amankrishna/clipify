from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field


class CaptionWord(BaseModel):
    word: str = Field(min_length=1, max_length=120)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    line: int = Field(default=0, ge=0, le=1000)


class CaptionUpdate(BaseModel):
    preset: Literal["bold_yellow", "white_minimal", "neon_creator"] | None = None
    words: list[CaptionWord] | None = Field(default=None, max_length=5000)


class CaptionResponse(BaseModel):
    id: int
    clip_edit_id: int
    preset: str
    words: list[CaptionWord]
    updated_at: datetime


class ExportCreate(BaseModel):
    preview: bool = False


class ExportResponse(BaseModel):
    id: int
    project_id: int
    clip_edit_id: int
    job_id: int
    is_preview: bool
    status: str
    output_path: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
