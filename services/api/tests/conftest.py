import os
from pathlib import Path
import shutil

TEST_ROOT = Path(__file__).resolve().parent / ".test-data"
os.environ["CLIPFORGE_DATABASE_URL"] = f"sqlite:///{TEST_ROOT / 'clipforge.db'}"
os.environ["CLIPFORGE_STORAGE_DIR"] = str(TEST_ROOT / "storage")


def pytest_sessionstart(session):
    shutil.rmtree(TEST_ROOT, ignore_errors=True)
    TEST_ROOT.mkdir(parents=True, exist_ok=True)


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(TEST_ROOT, ignore_errors=True)
