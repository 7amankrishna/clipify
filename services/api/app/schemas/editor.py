from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ClipEditCreate(BaseModel):
    asset_id: int
    suggestion_id: int | None = None
    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None, gt=0)
    aspect_ratio: Literal["9:16", "1:1", "16:9"] = "9:16"
    crop_mode: Literal["center", "blurred", "split", "manual"] = "center"

    @model_validator(mode="after")
    def check_optional_bounds(self):
        if self.start_seconds is not None and self.end_seconds is not None and self.end_seconds <= self.start_seconds:
            raise ValueError("End must be after start.")
        return self


class ClipEditUpdate(BaseModel):
    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None, gt=0)
    aspect_ratio: Literal["9:16", "1:1", "16:9"] | None = None
    crop_mode: Literal["center", "blurred", "split", "manual"] | None = None
    manual_x: float | None = Field(default=None, ge=0, le=100)
    manual_y: float | None = Field(default=None, ge=0, le=100)


class ClipEditResponse(BaseModel):
    id: int
    project_id: int
    asset_id: int
    suggestion_id: int | None
    start_seconds: float
    end_seconds: float
    aspect_ratio: str
    crop_mode: str
    manual_x: float
    manual_y: float
    created_at: datetime
    updated_at: datetime


class WaveformResponse(BaseModel):
    asset_id: int
    duration_seconds: float | None
    peaks: list[float]
