import os
from pathlib import Path

os.environ["CLIPFORGE_DATABASE_URL"] = "sqlite:///./test_clipforge.db"
os.environ["CLIPFORGE_STORAGE_DIR"] = "./test-data"

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402


def test_health_reports_database_and_tools():
    with TestClient(app) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["database"] == "connected"
    assert {"ffmpeg", "ffprobe"}.issubset(payload["tools"])

    Path("test_clipforge.db").unlink(missing_ok=True)
    test_data = Path("test-data")
    if test_data.exists():
        test_data.rmdir()
