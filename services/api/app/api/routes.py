from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.media_tools import media_tools_status
from app.db.session import get_db

router = APIRouter()


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
