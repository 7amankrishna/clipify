from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class SettingsUpdate(BaseModel):
    ai_provider: Literal["fallback", "ollama", "gemini"] | None = None
    ollama_url: str | None = Field(default=None, max_length=300)
    ollama_model: str | None = Field(default=None, min_length=1, max_length=120)
    gemini_model: str | None = Field(default=None, min_length=1, max_length=120)
    gemini_api_key: str | None = Field(default=None, min_length=10, max_length=500)
    clear_gemini_api_key: bool = False
    whisper_model: str | None = Field(default=None, max_length=80)
    processing_device: Literal["auto", "cpu", "cuda"] | None = None
    theme: Literal["dark", "light", "system"] | None = None


class SettingsResponse(BaseModel):
    ai_provider: Literal["fallback", "ollama", "gemini"]
    ollama_url: str
    ollama_model: str
    gemini_model: str
    has_gemini_api_key: bool
    whisper_model: str
    processing_device: str
    theme: str
    storage_dir: str
    ffmpeg_path: str | None


class MetadataGenerate(BaseModel):
    provider: Literal["fallback", "ollama", "gemini"] | None = None


class ClipMetadataUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    hook: str | None = Field(default=None, min_length=1, max_length=240)
    description: str | None = Field(default=None, min_length=1, max_length=1000)
    hashtags: list[str] | None = Field(default=None, max_length=20)
    explanation: str | None = Field(default=None, min_length=1, max_length=1000)


class ClipMetadataResponse(BaseModel):
    id: int
    project_id: int
    suggestion_id: int
    provider: str
    title: str
    hook: str
    description: str
    hashtags: list[str]
    explanation: str
    generation_note: str | None
    created_at: datetime
    updated_at: datetime
