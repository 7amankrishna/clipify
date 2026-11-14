from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "ClipForge Local API"
    app_version: str = "0.1.0"
    host: str = "127.0.0.1"
    port: int = 8000
    database_url: str = "sqlite:///./data/clipforge.db"
    storage_dir: Path = Path("./data")
    ffmpeg_path: str | None = None
    ffprobe_path: str | None = None
    cors_origins: str = "http://localhost:5173,tauri://localhost,http://tauri.localhost"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
