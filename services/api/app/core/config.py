from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="CLIPFORGE_", extra="ignore")

    app_name: str = "ClipForge Local API"
    app_version: str = "0.1.0"
    host: str = "127.0.0.1"
    port: int = 8000
    database_url: str = "sqlite:///./data/clipforge.db"
    storage_dir: Path = Path("./data")
    max_upload_size_mb: int = 10_240
    ffmpeg_path: str | None = None
    ffprobe_path: str | None = None
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3.2"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.0-flash"
    cors_origins: str = "http://localhost:5173,tauri://localhost,http://tauri.localhost"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
